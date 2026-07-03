from fastapi import APIRouter, Request, Response, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
import re
import json
import time
import io
import os
import requests
from typing import Optional, List
from src.db.models import get_db_connection
from src.utils.logger import logger
from src.utils.normalize import normalize_epg, normalize_logo

router = APIRouter(prefix="/api/live", tags=["live"])

_simulator = None
_login_func = None


def set_simulator(sim):
    global _simulator
    _simulator = sim


def set_login_func(func):
    global _login_func
    _login_func = func


def get_live_configs() -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM live_config")
    rows = c.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def get_alias_map() -> dict:
    """获取频道别名映射表，返回 {source_name: target_name} 字典。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT source_name, target_name FROM live_channel_aliases")
    rows = c.fetchall()
    conn.close()
    return {row["source_name"]: row["target_name"] for row in rows}


def resolve_channel_names(source_name: str, alias_map: dict = None) -> dict:
    """解析频道各项名称字段。

    Args:
        source_name: 服务器原始频道名
        alias_map: 别名映射表 {source_name: target_name}，可选

    Returns:
        {
            "name": str,          # 原始名称（存档）
            "display_name": str,  # 显示名称
            "tvg_id": str,        # EPG 匹配 ID
            "tvg_name": str,      # EPG 匹配名
            "logo_url": str,      # Logo 文件名
        }
    """
    if alias_map is None:
        alias_map = {}

    # 查映射表
    target = alias_map.get(source_name)

    # display_name：映射命中用 target，未命中用原始 name
    display_name = target if target else source_name

    # 归一化计算 tvg_id / tvg_name / logo_url
    base_epg = normalize_epg(display_name)
    base_logo = normalize_logo(display_name)

    return {
        "name": source_name,
        "display_name": display_name,
        "tvg_id": base_epg,
        "tvg_name": base_epg,
        "logo_url": base_logo + ".png",
    }


def parse_m3u_content(content: str) -> list:
    lines = content.splitlines()
    channels = []
    current_inf = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF:"):
            inf_part = line[8:]
            comma_idx = inf_part.rfind(",")
            if comma_idx != -1:
                attrs_str = inf_part[:comma_idx]
                name = inf_part[comma_idx+1:].strip()
            else:
                attrs_str = inf_part
                name = ""
            
            attrs = {}
            matches = re.findall(r'([\w-]+)="([^"]*)"', attrs_str)
            for k, v in matches:
                attrs[k.lower()] = v
                
            matches_no_quotes = re.findall(r'([\w-]+)=([^"\s]+)', attrs_str)
            for k, v in matches_no_quotes:
                if k.lower() not in attrs:
                    attrs[k.lower()] = v
                    
            current_inf = {
                "name": name,
                "channel_id": attrs.get("tvg-id", ""),  # Use tvg-id as channel_id fallback
                "tvg_id": attrs.get("tvg-id", name),
                "tvg_name": attrs.get("tvg-name", name),
                "logo_url": attrs.get("tvg-logo", ""),
                "group_title": attrs.get("group-title", ""),
            }
        elif not line.startswith("#"):
            if current_inf:
                current_inf["url"] = line
                channels.append(current_inf)
                current_inf = None
    return channels


# ---- API 接口 ----

@router.get("/config")
async def get_live_config():
    """获取所有直播配置。"""
    return get_live_configs()


@router.put("/config")
async def update_live_config(new_configs: dict):
    """批量更新直播配置。"""
    conn = get_db_connection()
    c = conn.cursor()
    for k, v in new_configs.items():
        c.execute("INSERT OR REPLACE INTO live_config (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.post("/sync")
async def sync_channels():
    """从 IPTV 网关同步频道列表。"""
    if _simulator is None:
        raise HTTPException(status_code=500, detail="STB 模拟器未初始化")
        
    if not _simulator.state.is_authenticated:
        logger.info("[Live Sync] 模拟器处于未登录状态，尝试登录...")
        login_success = False
        if _login_func:
            login_success = _login_func()
        else:
            login_success = _simulator.login()
        if not login_success:
            return JSONResponse(status_code=401, content={"status": "error", "message": "模拟器登录失败，无法同步"})

    try:
        sim_channels = _simulator.get_channel_list()
        if not sim_channels:
            return {"status": "success", "count": 0, "disabled": 0, "message": "同步完成，未发现可用频道"}
            
        sync_time = int(time.time())
        alias_map = get_alias_map()
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT MAX(synced_at) as max_sync FROM live_channels WHERE source = 'server'")
        row = c.fetchone()
        last_sync_time = row["max_sync"] if row and row["max_sync"] else 0
        
        added_count = 0
        updated_count = 0
        
        for ch in sim_channels:
            channel_id = ch["channel_id"]
            
            c.execute("SELECT id, is_enabled, synced_at FROM live_channels WHERE channel_id = ? AND source = 'server'", (channel_id,))
            existing = c.fetchone()
            
            if existing:
                is_enabled = existing["is_enabled"]
                if is_enabled == 0:
                    if existing["synced_at"] < last_sync_time:
                        is_enabled = 1
                        logger.info(f"[Live Sync] 频道 {ch['name']} (ID: {channel_id}) 重新上线，自动恢复启用")
                
                c.execute("""
                    UPDATE live_channels SET
                        user_channel_id = ?,
                        name = ?,
                        multicast_url = ?,
                        unicast_url = ?,
                        unicast_url_full = ?,
                        timeshift_enabled = ?,
                        timeshift_length = ?,
                        timeshift_url = ?,
                        is_hd = ?,
                        channel_type = ?,
                        channel_sdp = ?,
                        channel_url_raw = ?,
                        channel_locked = ?,
                        preview_enabled = ?,
                        fcc_enabled = ?,
                        fcc_ip = ?,
                        fcc_port = ?,
                        fec_port = ?,
                        raw_fields_json = ?,
                        synced_at = ?,
                        is_enabled = ?
                    WHERE id = ?
                """, (
                    ch["user_channel_id"],
                    ch["name"],
                    ch["multicast_url"],
                    ch["unicast_url"],
                    ch["unicast_url_full"],
                    ch["timeshift_enabled"],
                    ch["timeshift_length"],
                    ch["timeshift_url"],
                    ch["is_hd"],
                    ch["channel_type"],
                    ch["channel_sdp"],
                    ch["channel_url_raw"],
                    ch["channel_locked"],
                    ch["preview_enabled"],
                    ch["fcc_enabled"],
                    ch["fcc_ip"],
                    ch["fcc_port"],
                    ch["fec_port"],
                    ch["raw_fields_json"],
                    sync_time,
                    is_enabled,
                    existing["id"]
                ))
                updated_count += 1
            else:
                resolved = resolve_channel_names(ch["name"], alias_map)
                c.execute("""
                    INSERT INTO live_channels (
                        source, channel_id, user_channel_id, name, display_name,
                        tvg_id, tvg_name, logo_url, category_id, sort_index, is_enabled,
                        multicast_url, unicast_url, unicast_url_full, timeshift_enabled,
                        timeshift_length, timeshift_url, is_hd, channel_type, channel_sdp,
                        channel_url_raw, channel_locked, preview_enabled, fcc_enabled,
                        fcc_ip, fcc_port, fec_port, raw_fields_json, synced_at, created_at
                    ) VALUES (
                        'server', ?, ?, ?, ?,
                        ?, ?, ?, 0, 0, 1,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                """, (
                    channel_id,
                    ch["user_channel_id"],
                    resolved["name"],
                    resolved["display_name"],
                    resolved["tvg_id"],
                    resolved["tvg_name"],
                    resolved["logo_url"],
                    ch["multicast_url"],
                    ch["unicast_url"],
                    ch["unicast_url_full"],
                    ch["timeshift_enabled"],
                    ch["timeshift_length"],
                    ch["timeshift_url"],
                    ch["is_hd"],
                    ch["channel_type"],
                    ch["channel_sdp"],
                    ch["channel_url_raw"],
                    ch["channel_locked"],
                    ch["preview_enabled"],
                    ch["fcc_enabled"],
                    ch["fcc_ip"],
                    ch["fcc_port"],
                    ch["fec_port"],
                    ch["raw_fields_json"],
                    sync_time,
                    sync_time
                ))
                added_count += 1
                
        c.execute("""
            UPDATE live_channels 
            SET is_enabled = 0 
            WHERE source = 'server' AND synced_at != ? AND is_enabled = 1
        """, (sync_time,))
        disabled_count = c.rowcount
        
        conn.commit()
        conn.close()
        
        msg = f"同步完成。新增 {added_count} 个频道，更新 {updated_count} 个频道，下线并禁用 {disabled_count} 个频道。"
        logger.info(f"[Live Sync] {msg}")
        return {
            "status": "success",
            "count": added_count + updated_count,
            "disabled": disabled_count,
            "message": msg
        }
        
    except Exception as e:
        logger.error(f"[Live Sync] 同步频道异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"同步异常: {e}")


@router.get("/channels")
async def get_channels(
    category_id: Optional[int] = Query(None),
    enabled: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1)
):
    """分页查询直播频道列表。"""
    conn = get_db_connection()
    c = conn.cursor()
    
    where_clauses = []
    params = []
    
    if category_id is not None:
        where_clauses.append("c.category_id = ?")
        params.append(category_id)
    if enabled is not None:
        where_clauses.append("c.is_enabled = ?")
        params.append(enabled)
    if source is not None:
        where_clauses.append("c.source = ?")
        params.append(source)
    if keyword:
        where_clauses.append("(c.name LIKE ? OR c.channel_id LIKE ? OR c.user_channel_id LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])
        
    where_str = ""
    if where_clauses:
        where_str = "WHERE " + " AND ".join(where_clauses)
        
    c.execute(f"SELECT COUNT(*) as total FROM live_channels c {where_str}", params)
    total = c.fetchone()["total"]
    
    query = f"""
        SELECT c.*, cat.name as category_name, cat.color as category_color 
        FROM live_channels c
        LEFT JOIN live_categories cat ON c.category_id = cat.id
        {where_str}
        ORDER BY c.sort_index ASC, CASE WHEN c.user_channel_id IS NULL OR c.user_channel_id = '' THEN 1 ELSE 0 END ASC, CAST(c.user_channel_id AS INTEGER) ASC, c.id ASC
        LIMIT ? OFFSET ?
    """
    offset = (page - 1) * limit
    params_limit = params + [limit, offset]
    c.execute(query, params_limit)
    rows = c.fetchall()
    
    channels = [dict(row) for row in rows]
    conn.close()
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "channels": channels
    }


@router.get("/stats")
async def get_live_stats():
    """获取直播频道统计信息。"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) as cnt FROM live_channels WHERE source = 'server'")
    server_count = c.fetchone()["cnt"]
    
    c.execute("SELECT COUNT(*) as cnt FROM live_channels WHERE source = 'external'")
    external_count = c.fetchone()["cnt"]
    
    c.execute("SELECT COUNT(*) as cnt FROM live_channels WHERE is_enabled = 1")
    enabled_count = c.fetchone()["cnt"]
    
    c.execute("SELECT COUNT(*) as cnt FROM live_channels WHERE is_enabled = 0")
    disabled_count = c.fetchone()["cnt"]
    
    conn.close()
    return {
        "server": server_count,
        "external": external_count,
        "enabled": enabled_count,
        "disabled": disabled_count
    }


@router.post("/channels/batch-enabled")
async def batch_channels_enabled(payload: dict):
    """批量启用/禁用频道。"""
    ids = payload.get("ids", [])
    enabled = payload.get("enabled", 1)
    if not ids:
        raise HTTPException(status_code=400, detail="未指定频道 ID")
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ",".join(["?"] * len(ids))
    c.execute(f"UPDATE live_channels SET is_enabled = ? WHERE id IN ({placeholders})", [enabled] + ids)
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"成功更新 {c.rowcount} 个频道的状态"}


@router.post("/channels/batch-category")
async def batch_channels_category(payload: dict):
    """批量修改频道分类。"""
    ids = payload.get("ids", [])
    category_id = payload.get("category_id", 0)
    if not ids:
        raise HTTPException(status_code=400, detail="未指定频道 ID")
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ",".join(["?"] * len(ids))
    c.execute(f"UPDATE live_channels SET category_id = ? WHERE id IN ({placeholders})", [category_id] + ids)
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"成功批量归类 {c.rowcount} 个频道"}


@router.post("/channels/batch-delete")
async def batch_channels_delete(payload: dict):
    """批量删除频道。"""
    ids = payload.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="未指定频道 ID")
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ",".join(["?"] * len(ids))
    c.execute(f"DELETE FROM live_channels WHERE id IN ({placeholders})", ids)
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"成功删除 {deleted} 个频道"}


@router.post("/channels/reset-order")
async def reset_channels_order():
    """重置所有频道排序索引。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE live_channels SET sort_index = 0")
    conn.commit()
    conn.close()
    return {"status": "success", "message": "已成功重置排序为默认顺序"}


@router.post("/channels")
async def add_channel(ch_data: dict):
    """手动添加外部频道。"""
    conn = get_db_connection()
    c = conn.cursor()
    
    channel_id = ch_data.get("channel_id", "").strip()
    multicast_url = ch_data.get("multicast_url", "").strip()
    name = ch_data.get("name", "").strip()
    
    if not name:
        conn.close()
        raise HTTPException(status_code=400, detail="频道名称不能为空")
        
    if channel_id:
        c.execute("SELECT id FROM live_channels WHERE channel_id = ?", (channel_id,))
        if c.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="频道 ID 已存在")
            
    if multicast_url:
        c.execute("SELECT id FROM live_channels WHERE multicast_url = ?", (multicast_url,))
        if c.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="该组播地址已存在")
    
    # 名称解析
    display_name = ch_data.get("display_name", "").strip() or name
    tvg_id = ch_data.get("tvg_id", "").strip() or normalize_epg(display_name)
    tvg_name = ch_data.get("tvg_name", "").strip() or normalize_epg(display_name)
    logo_url = ch_data.get("logo_url", "").strip() or normalize_logo(display_name) + ".png"
    category_id = int(ch_data.get("category_id", 0))
    sort_index = int(ch_data.get("sort_index", 0))
    is_enabled = int(ch_data.get("is_enabled", 1))
    
    now = int(time.time())
    c.execute("""
        INSERT INTO live_channels (
            source, channel_id, user_channel_id, name, display_name,
            tvg_id, tvg_name, logo_url, category_id, sort_index, is_enabled,
            multicast_url, unicast_url, synced_at, created_at
        ) VALUES (
            'external', ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
    """, (
        channel_id,
        ch_data.get("user_channel_id", ""),
        name,
        display_name,
        tvg_id,
        tvg_name,
        logo_url,
        category_id,
        sort_index,
        is_enabled,
        multicast_url,
        ch_data.get("unicast_url", ""),
        now,
        now
    ))
    
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return {"status": "success", "id": new_id}


@router.put("/channels/{id}")
async def update_channel(id: int, ch_data: dict):
    """更新频道信息。"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT source FROM live_channels WHERE id = ?", (id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="频道不存在")
        
    source = row["source"]
    category_id = int(ch_data.get("category_id", 0))
    sort_index = int(ch_data.get("sort_index", 0))
    is_enabled = int(ch_data.get("is_enabled", 1))
    tvg_id = ch_data.get("tvg_id", "").strip()
    tvg_name = ch_data.get("tvg_name", "").strip()
    logo_url = ch_data.get("logo_url", "").strip()
    
    if source == 'external':
        name = ch_data.get("name", "").strip()
        if not name:
            conn.close()
            raise HTTPException(status_code=400, detail="频道名称不能为空")
        display_name = ch_data.get("display_name", "").strip() or name
        multicast_url = ch_data.get("multicast_url", "")
        unicast_url = ch_data.get("unicast_url", "")
        channel_id = ch_data.get("channel_id", "")
        user_channel_id = ch_data.get("user_channel_id", "")
        c.execute("""
            UPDATE live_channels SET
                name = ?, display_name = ?, tvg_id = ?, tvg_name = ?, logo_url = ?,
                category_id = ?, sort_index = ?, is_enabled = ?,
                multicast_url = ?, unicast_url = ?, channel_id = ?, user_channel_id = ?
            WHERE id = ?
        """, (
            name, display_name, tvg_id, tvg_name, logo_url,
            category_id, sort_index, is_enabled,
            multicast_url, unicast_url, channel_id, user_channel_id, id
        ))
    else:
        # server 频道：仅分类可手动改，其余全部由服务器/映射表/归一化自动生成
        c.execute("""
            UPDATE live_channels SET
                category_id = ?, sort_index = ?, is_enabled = ?
            WHERE id = ?
        """, (category_id, sort_index, is_enabled, id))
        
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.delete("/channels/{id}")
async def delete_channel(id: int):
    """删除外部频道。"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT source FROM live_channels WHERE id = ?", (id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="频道不存在")
        
    if row["source"] == 'server':
        conn.close()
        raise HTTPException(status_code=400, detail="服务器下发的频道不允许删除，只能禁用")
        
    c.execute("DELETE FROM live_channels WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.post("/channels/reorder")
async def reorder_channels(payload: dict):
    """批量更新频道排序。"""
    orders = payload.get("order", [])
    if not orders:
        return {"status": "success"}
        
    conn = get_db_connection()
    c = conn.cursor()
    for item in orders:
        c.execute("UPDATE live_channels SET sort_index = ? WHERE id = ?", (item["sort_index"], item["id"]))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.get("/categories")
async def get_categories():
    """获取所有频道分类。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM live_categories ORDER BY sort_index ASC, id ASC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/categories")
async def add_category(cat_data: dict):
    """新增分类。"""
    name = cat_data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
        
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT id FROM live_categories WHERE name = ?", (name,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="分类已存在")
        
    sort_index = int(cat_data.get("sort_index", 0))
    color = cat_data.get("color", "")
    is_visible = int(cat_data.get("is_visible", 1))
    
    now = int(time.time())
    c.execute("""
        INSERT INTO live_categories (name, sort_index, color, is_visible, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name, sort_index, color, is_visible, now))
    
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return {"status": "success", "id": new_id}


@router.put("/categories/{id}")
async def update_category(id: int, cat_data: dict):
    """更新分类信息。"""
    name = cat_data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
        
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT id FROM live_categories WHERE name = ? AND id != ?", (name, id))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="分类名称已存在")
        
    sort_index = int(cat_data.get("sort_index", 0))
    color = cat_data.get("color", "")
    is_visible = int(cat_data.get("is_visible", 1))
    
    c.execute("""
        UPDATE live_categories SET
            name = ?, sort_index = ?, color = ?, is_visible = ?
        WHERE id = ?
    """, (name, sort_index, color, is_visible, id))
    
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.delete("/categories/{id}")
async def delete_category(id: int):
    """删除分类。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE live_channels SET category_id = 0 WHERE category_id = ?", (id,))
    c.execute("DELETE FROM live_categories WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.post("/categories/reorder")
async def reorder_categories(payload: dict):
    """批量更新分类排序。"""
    orders = payload.get("order", [])
    if not orders:
        return {"status": "success"}
    conn = get_db_connection()
    c = conn.cursor()
    for item in orders:
        c.execute("UPDATE live_categories SET sort_index = ? WHERE id = ?", (item["sort_index"], item["id"]))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.post("/import")
async def import_channels(
    request: Request,
    file: Optional[UploadFile] = File(None)
):
    """解析并导入外部 M3U 频道。"""
    content = ""
    if file:
        content_bytes = await file.read()
        content = content_bytes.decode("utf-8", errors="ignore")
    else:
        try:
            body = await request.json()
            content = body.get("content", "")
        except Exception:
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 或使用表单上传文件")

    if not content.strip():
        raise HTTPException(status_code=400, detail="导入内容不能为空")

    # 如果传入的是 URL，先下载内容
    text = content.strip()
    if text.startswith("http://") or text.startswith("https://"):
        try:
            resp = requests.get(text, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            # 检测是否为 HTML 页面（如 GitHub blob 页），给出提示
            ct = resp.headers.get("Content-Type", "")
            if "text/html" in ct or resp.text.strip().startswith("<!DOCTYPE html>") or resp.text.strip().startswith("<html"):
                if "github.com" in text and "/blob/" in text:
                    raw_url = text.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    raise HTTPException(status_code=400, detail=f"链接返回的是 GitHub 网页，请改用 raw 原始链接: {raw_url}")
                raise HTTPException(status_code=400, detail="该链接返回的是 HTML 网页，请确认是 M3U 文件的原始下载地址")
            # 优先尝试 UTF-8 解码，回退到 GBK
            raw = resp.content
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="ignore")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"下载 M3U 链接失败: {e}")
    
    imported_list = parse_m3u_content(text)
        
    if not imported_list:
        return {"new": 0, "skipped": 0, "total": 0, "message": "未解析出任何有效频道，请检查是否为标准 M3U 格式"}
        
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT channel_id, multicast_url FROM live_channels")
    existing_rows = c.fetchall()
    
    existing_ids = {row["channel_id"] for row in existing_rows if row["channel_id"]}
    existing_multicast = {row["multicast_url"] for row in existing_rows if row["multicast_url"]}
    
    c.execute("SELECT id, name FROM live_categories")
    cat_rows = c.fetchall()
    cat_map = {row["name"]: row["id"] for row in cat_rows}

    def resolve_category(group_title: str) -> int:
        """智能匹配分类：精确 → 部分匹配 → 自动创建 → 未分类"""
        if not group_title:
            return 0
        if group_title in cat_map:
            return cat_map[group_title]
        for name, cid in cat_map.items():
            if group_title in name or name in group_title:
                return cid
        now = int(time.time())
        c.execute("INSERT INTO live_categories (name, sort_index, created_at) VALUES (?, 99, ?)", (group_title, now))
        new_id = c.lastrowid
        cat_map[group_title] = new_id
        logger.info(f"[Import] 自动创建分类: {group_title}")
        return new_id

    new_count = 0
    skipped_count = 0
    not_multicast = 0
    now = int(time.time())

    def normalize_multicast_url(url: str) -> str | None:
        """将导入地址标准化为组播格式，非组播返回 None"""
        if not url:
            return None
        if url.startswith("igmp://") or url.startswith("rtp://"):
            return url
        m = re.match(r'https?://[^/]+/udp/([\d]+\.[\d]+\.[\d]+\.[\d]+:\d+)', url)
        if m:
            return "igmp://" + m.group(1)
        return None

    for item in imported_list:
        ch_id = "999999"  # 外部导入频道统一 channel_id
        url = item.get("url", "").strip()
        name = item.get("name", "").strip()
        group_title = item.get("group_title", "").strip()
        
        if not name or not url:
            skipped_count += 1
            continue

        # 标准化组播地址，非组播跳过
        mcast_url = normalize_multicast_url(url)
        if not mcast_url:
            not_multicast += 1
            continue
            
        is_duplicate = False
        if mcast_url and mcast_url in existing_multicast:
            is_duplicate = True
            
        if is_duplicate:
            skipped_count += 1
            continue
            
        category_id = resolve_category(group_title)
        tvg_id = item.get("tvg_id", name) or name
        tvg_name = item.get("tvg_name", name) or name
        logo_url = item.get("logo_url", "")
        display_name = name
        
        if logo_url:
            logo_url = os.path.basename(logo_url)
            
        c.execute("""
            INSERT INTO live_channels (
                source, channel_id, name, display_name, tvg_id, tvg_name, logo_url,
                category_id, sort_index, is_enabled, multicast_url,
                synced_at, created_at
            ) VALUES (
                'external', ?, ?, ?, ?, ?, ?,
                ?, 0, 1, ?,
                ?, ?
            )
        """, (
            ch_id, name, display_name, tvg_id, tvg_name, logo_url,
            category_id, mcast_url, now, now
        ))
        
        if mcast_url:
            existing_multicast.add(mcast_url)
            
        new_count += 1
        
    conn.commit()
    conn.close()
    
    return {
        "new": new_count,
        "skipped": skipped_count,
        "not_multicast": not_multicast,
        "total": len(imported_list)
    }


# ---- 频道别名映射表 API ----


def _apply_alias_to_channels(c, source_name: str, target_name: str) -> int:
    """将单条别名映射应用到匹配的已有频道上。

    Args:
        c: 数据库 cursor
        source_name: 服务器原始名称
        target_name: 规范名称

    Returns:
        受影响的频道数量
    """
    display_name = target_name
    base_epg = normalize_epg(target_name)
    base_logo = normalize_logo(target_name)

    c.execute("""
        UPDATE live_channels
        SET display_name = ?, tvg_id = ?, tvg_name = ?, logo_url = ?
        WHERE name = ? AND source = 'server'
    """, (display_name, base_epg, base_epg, base_logo + ".png", source_name))
    return c.rowcount


def _reapply_all_aliases(c) -> dict:
    """将别名映射表中的所有映射应用到已有频道。

    Returns:
        {"applied": int, "affected": int}
        applied: 成功应用的别名数
        affected: 被更新的频道数
    """
    c.execute("SELECT source_name, target_name FROM live_channel_aliases")
    aliases = c.fetchall()
    total_affected = 0
    applied = 0
    for row in aliases:
        n = _apply_alias_to_channels(c, row["source_name"], row["target_name"])
        if n > 0:
            total_affected += n
            applied += 1
    return {"applied": applied, "affected": total_affected}


@router.get("/aliases")
async def get_aliases():
    """获取所有频道别名映射。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, source_name, target_name FROM live_channel_aliases ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/aliases")
async def add_alias(data: dict):
    """添加单条别名映射（自动应用到已有频道）。"""
    source_name = data.get("source_name", "").strip()
    target_name = data.get("target_name", "").strip()
    if not source_name or not target_name:
        raise HTTPException(status_code=400, detail="source_name 和 target_name 不能为空")

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT OR REPLACE INTO live_channel_aliases (source_name, target_name) VALUES (?, ?)",
            (source_name, target_name)
        )
        affected = _apply_alias_to_channels(c, source_name, target_name)
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
    conn.close()
    return {"status": "success", "affected_channels": affected}


@router.put("/aliases/{id}")
async def update_alias(id: int, data: dict):
    """更新别名映射（自动应用到已有频道）。"""
    source_name = data.get("source_name", "").strip()
    target_name = data.get("target_name", "").strip()
    if not source_name or not target_name:
        raise HTTPException(status_code=400, detail="source_name 和 target_name 不能为空")

    conn = get_db_connection()
    c = conn.cursor()

    # 取出旧值
    c.execute("SELECT source_name FROM live_channel_aliases WHERE id = ?", (id,))
    old_row = c.fetchone()
    old_source = old_row["source_name"] if old_row else ""

    c.execute(
        "UPDATE live_channel_aliases SET source_name = ?, target_name = ? WHERE id = ?",
        (source_name, target_name, id)
    )

    # 如果原名改了，旧名的频道回退为原始值
    if old_source and old_source != source_name:
        c.execute(
            "UPDATE live_channels SET display_name = name WHERE name = ? AND source = 'server'",
            (old_source,)
        )

    affected = _apply_alias_to_channels(c, source_name, target_name)
    conn.commit()
    conn.close()
    return {"status": "success", "affected_channels": affected}


@router.delete("/aliases/{id}")
async def delete_alias(id: int):
    """删除别名映射（同时回退相关频道的显示名称）。"""
    conn = get_db_connection()
    c = conn.cursor()

    # 查出 source_name 用于回退
    c.execute("SELECT source_name FROM live_channel_aliases WHERE id = ?", (id,))
    row = c.fetchone()
    source_name = row["source_name"] if row else ""

    c.execute("DELETE FROM live_channel_aliases WHERE id = ?", (id,))

    # 回退匹配频道的 display_name 为原始 name
    if source_name:
        c.execute(
            "UPDATE live_channels SET display_name = name WHERE name = ? AND source = 'server'",
            (source_name,)
        )

    conn.commit()
    conn.close()
    return {"status": "success"}


@router.get("/aliases/export")
async def export_aliases():
    """导出别名映射表为 JSON。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT source_name, target_name FROM live_channel_aliases ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()

    aliases = [{"source_name": r["source_name"], "target_name": r["target_name"]} for r in rows]
    return {
        "version": 1,
        "exported_at": int(time.time()),
        "count": len(aliases),
        "aliases": aliases
    }


@router.post("/aliases/import")
async def import_aliases(data: dict):
    """从 JSON 导入别名映射表（覆盖模式：清空后插入，并全部应用到频道）。"""
    aliases = data.get("aliases", [])
    if not aliases:
        raise HTTPException(status_code=400, detail="aliases 列表不能为空")

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM live_channel_aliases")
        imported = 0
        for item in aliases:
            src = item.get("source_name", "").strip()
            tgt = item.get("target_name", "").strip()
            if src and tgt:
                c.execute(
                    "INSERT INTO live_channel_aliases (source_name, target_name) VALUES (?, ?)",
                    (src, tgt)
                )
                imported += 1
        # 导入后全部应用到已有频道
        reapply_result = _reapply_all_aliases(c)
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")
    conn.close()
    return {
        "status": "success",
        "imported": imported,
        "applied": reapply_result["applied"],
        "affected_channels": reapply_result["affected"]
    }


@router.post("/aliases/reapply")
async def reapply_aliases():
    """重新将所有别名映射应用到已有频道。"""
    conn = get_db_connection()
    c = conn.cursor()
    result = _reapply_all_aliases(c)
    conn.commit()
    conn.close()
    logger.info(f"[Alias] 重新应用完成：{result['applied']} 条映射，{result['affected']} 个频道")
    return {"status": "success", "applied": result["applied"], "affected": result["affected"]}


@router.get("/tv.m3u")
async def generate_m3u(
    request: Request,
    category_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None)
):
    """动态生成并获取标准 M3U 订阅内容。"""
    configs = get_live_configs()
    udpxy_enabled = configs.get("udpxy_enabled", "1") == "1"
    udpxy_address = configs.get("udpxy_address", "").strip()
    fcc_global_enabled = configs.get("fcc_global_enabled", "0") == "1"
    timeshift_enabled_global = configs.get("timeshift_enabled", "1") == "1"
    logo_base_url = configs.get("logo_base_url", "/static/logo/").strip()
    m3u_dual_line = configs.get("m3u_dual_line", "0") == "1"
    epg_url = configs.get("epg_url", "").strip()
    
    conn = get_db_connection()
    c = conn.cursor()
    
    where_clauses = ["c.is_enabled = 1"]
    params = []
    
    if category_id is not None:
        where_clauses.append("c.category_id = ?")
        params.append(category_id)
    if source is not None:
        where_clauses.append("c.source = ?")
        params.append(source)
        
    where_str = "WHERE " + " AND ".join(where_clauses)
    
    query = f"""
        SELECT c.*, cat.name as category_name
        FROM live_channels c
        LEFT JOIN live_categories cat ON c.category_id = cat.id
        {where_str}
        ORDER BY c.sort_index ASC, CASE WHEN c.user_channel_id IS NULL OR c.user_channel_id = '' THEN 1 ELSE 0 END ASC, CAST(c.user_channel_id AS INTEGER) ASC, c.id ASC
    """
    c.execute(query, params)
    channels = c.fetchall()
    conn.close()
    
    host = request.headers.get("host", "")
    scheme = request.url.scheme
    base_url_dynamic = f"{scheme}://{host}"
    
    m3u_lines = []
    if epg_url:
        m3u_lines.append(f'#EXTM3U x-tvg-url="{epg_url}"')
    else:
        m3u_lines.append('#EXTM3U')
        
    def resolve_logo(logo_path: str) -> str:
        if not logo_path:
            return ""
        if logo_path.startswith("http://") or logo_path.startswith("https://"):
            return logo_path
        if logo_base_url.startswith("http://") or logo_base_url.startswith("https://"):
            return f"{logo_base_url.rstrip('/')}/{logo_path.lstrip('/')}"
        else:
            full_base = f"{base_url_dynamic.rstrip('/')}/{logo_base_url.strip('/')}"
            return f"{full_base.rstrip('/')}/{logo_path.lstrip('/')}"

    for ch in channels:
        name = ch["name"]
        display_name = ch["display_name"] or name
        tvg_id = ch["tvg_id"] or normalize_epg(display_name)
        tvg_name = ch["tvg_name"] or normalize_epg(display_name)
        logo_file = ch["logo_url"]
        logo_full = resolve_logo(logo_file)
        
        group = ch["category_name"] or "其他"
        
        # 时移参数
        catchup_str = ""
        if timeshift_enabled_global and ch["timeshift_enabled"] == 1 and ch["unicast_url"]:
            catchup_str = f' catchup="default" catchup-source="{ch["unicast_url"]}?playseek=${{(b)yyyyMMddHHmmss}}-${{(e)yyyyMMddHHmmss}}"'
            
        logo_str = f' tvg-logo="{logo_full}"' if logo_full else ""
        
        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}"{logo_str} group-title="{group}"{catchup_str},{display_name}'
        
        m_url = ch["multicast_url"]
        play_m_url = ""
        if m_url:
            if m_url.startswith("igmp://") and udpxy_enabled and udpxy_address:
                raw_addr = m_url[7:]
                udpxy_clean = udpxy_address.rstrip('/')
                if not udpxy_clean.startswith("http://") and not udpxy_clean.startswith("https://"):
                    udpxy_clean = f"http://{udpxy_clean}"
                
                fcc_str = ""
                if fcc_global_enabled and ch["fcc_enabled"] > 0 and ch["fcc_ip"] and ch["fcc_port"]:
                    fcc_str = f'?fcc={ch["fcc_ip"]}:{ch["fcc_port"]}'
                    
                play_m_url = f"{udpxy_clean}/udp/{raw_addr}{fcc_str}"
            else:
                play_m_url = m_url
                
        play_u_url = ch["unicast_url"]
        
        if m3u_dual_line:
            if play_m_url:
                m3u_lines.append(extinf)
                m3u_lines.append(play_m_url)
            if play_u_url:
                m3u_lines.append(extinf)
                m3u_lines.append(play_u_url)
        else:
            if play_m_url:
                m3u_lines.append(extinf)
                m3u_lines.append(play_m_url)
            elif play_u_url:
                m3u_lines.append(extinf)
                m3u_lines.append(play_u_url)
                
    m3u_content = "\n".join(m3u_lines)
    headers = {
        "Content-Disposition": 'attachment; filename="tv.m3u"'
    }
    return Response(
        content=m3u_content,
        media_type="application/vnd.apple.mpegurl",
        headers=headers
    )
