"""
直播频道 CRUD 模块 - live_categories / live_channels / live_config 的数据库操作。
"""
import json
import time

from src.db.models import get_db_connection
from src.utils.logger import logger

# ============================================================
# 初始化
# ============================================================

DEFAULT_CATEGORIES = [
    "央视高清", "央视标清", "卫视高清", "卫视标清",
    "地方高清", "地方标清", "4K超高清", "国际",
    "付费高清", "广播", "其他",
]

DEFAULT_CONFIGS = {
    "udpxy_address": "",
    "udpxy_enabled": "1",
    "m3u_auth_required": "0",
    "fcc_global_enabled": "1",
    "timeshift_enabled": "1",
    "epg_url": "",
    "logo_base_url": "/static/logo/",
    "m3u_dual_line": "0",
}


def init_live_defaults():
    """插入预置分类和默认配置（首次启动时调用）。"""
    conn = get_db_connection()
    c = conn.cursor()

    # 预置分类
    now = int(time.time())
    for i, name in enumerate(DEFAULT_CATEGORIES):
        c.execute("""
            INSERT OR IGNORE INTO live_categories (name, sort_index, created_at)
            VALUES (?, ?, ?)
        """, (name, i, now))

    # 默认配置
    for key, value in DEFAULT_CONFIGS.items():
        c.execute("""
            INSERT OR IGNORE INTO live_config (key, value)
            VALUES (?, ?)
        """, (key, value))

    conn.commit()
    conn.close()
    logger.info("[LiveDB] 预置分类与默认配置初始化完成")


# ============================================================
# 配置操作
# ============================================================

def get_live_config() -> dict:
    """获取所有 live_config 键值对。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM live_config")
    rows = c.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def update_live_config(updates: dict) -> bool:
    """批量更新 live_config。

    Args:
        updates: {key: value, ...} 字典
    """
    conn = get_db_connection()
    c = conn.cursor()
    for key, value in updates.items():
        c.execute("""
            INSERT OR REPLACE INTO live_config (key, value)
            VALUES (?, ?)
        """, (key, str(value)))
    conn.commit()
    conn.close()
    logger.info("[LiveDB] 配置更新: %s", updates)
    return True


# ============================================================
# 分类操作
# ============================================================

def get_categories() -> list:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM live_categories ORDER BY sort_index ASC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_category(name: str, sort_index: int = 99) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    now = int(time.time())
    try:
        c.execute("""
            INSERT INTO live_categories (name, sort_index, created_at)
            VALUES (?, ?, ?)
        """, (name, sort_index, now))
        conn.commit()
        cat_id = c.lastrowid
        conn.close()
        return {"id": cat_id, "name": name, "sort_index": sort_index}
    except Exception as e:
        conn.close()
        raise ValueError(f"分类已存在: {name}") from e


def update_category(cat_id: int, updates: dict) -> bool:
    conn = get_db_connection()
    c = conn.cursor()
    allowed = {"name", "sort_index", "color", "is_visible"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        conn.close()
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [cat_id]
    c.execute(f"UPDATE live_categories SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_category(cat_id: int) -> bool:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM live_categories WHERE id = ?", (cat_id,))
    deleted = c.rowcount
    # 该分类下的频道归入"其他"（id=10）
    if deleted:
        c.execute("UPDATE live_channels SET category_id = 10 WHERE category_id = ?", (cat_id,))
    conn.commit()
    conn.close()
    return deleted > 0


# ============================================================
# 频道操作
# ============================================================

def get_channels(
    category_id: int = None,
    source: str = None,
    enabled: int = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """分页查询频道列表。

    Returns:
        {"list": [...], "total": int}
    """
    conn = get_db_connection()
    c = conn.cursor()

    where_clauses = []
    params = []

    if category_id is not None:
        where_clauses.append("category_id = ?")
        params.append(category_id)
    if source:
        where_clauses.append("source = ?")
        params.append(source)
    if enabled is not None:
        where_clauses.append("is_enabled = ?")
        params.append(enabled)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Total
    c.execute(f"SELECT COUNT(*) FROM live_channels {where_sql}", params)
    total = c.fetchone()[0]

    # Paged query
    offset = (page - 1) * limit
    c.execute(
        f"SELECT * FROM live_channels {where_sql} ORDER BY sort_index ASC, id ASC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    rows = c.fetchall()
    conn.close()

    return {
        "list": [dict(row) for row in rows],
        "total": total,
    }


def get_channel(ch_id: int) -> dict | None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM live_channels WHERE id = ?", (ch_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_channel(ch_id: int, updates: dict) -> bool:
    """更新频道用户自定义字段。"""
    conn = get_db_connection()
    c = conn.cursor()
    # 只允许更新用户自定义字段
    allowed = {"tvg_id", "tvg_name", "logo_url", "category_id", "sort_index", "is_enabled", "name"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        conn.close()
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [ch_id]
    c.execute(f"UPDATE live_channels SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_channel(ch_id: int) -> bool:
    """删除频道（仅 external 源）。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM live_channels WHERE id = ? AND source = 'external'", (ch_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def delete_all_external_channels() -> int:
    """删除所有外部频道。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM live_channels WHERE source = 'external'")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    logger.info("[LiveDB] 已删除全部外部频道: %d 条", deleted)
    return deleted


def batch_delete_channels(ids: list) -> int:
    """批量删除外部频道。

    Args:
        ids: 频道 ID 列表
    Returns:
        实际删除数量
    """
    if not ids:
        return 0
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ",".join("?" for _ in ids)
    c.execute(
        f"DELETE FROM live_channels WHERE id IN ({placeholders}) AND source = 'external'",
        ids,
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    logger.info("[LiveDB] 批量删除外部频道: %d 条", deleted)
    return deleted


def batch_update_category(ids: list, category_id: int) -> int:
    """批量修改频道分类。

    Args:
        ids: 频道 ID 列表
        category_id: 目标分类 ID
    Returns:
        实际更新数量
    """
    if not ids:
        return 0
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ",".join("?" for _ in ids)
    c.execute(
        f"UPDATE live_channels SET category_id = ? WHERE id IN ({placeholders})",
        [category_id] + ids,
    )
    updated = c.rowcount
    conn.commit()
    conn.close()
    logger.info("[LiveDB] 批量修改分类: %d 条 → category_id=%d", updated, category_id)
    return updated


def add_external_channel(ch_data: dict) -> int:
    """添加外部频道。"""
    conn = get_db_connection()
    c = conn.cursor()
    now = int(time.time())
    name = ch_data.get("name", "")
    c.execute("""
        INSERT INTO live_channels (
            source, channel_id, name, tvg_id, tvg_name, logo_url,
            multicast_url, unicast_url, category_id, created_at, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "external",
        ch_data.get("channel_id", ""),
        name,
        ch_data.get("tvg_id", name),
        ch_data.get("tvg_name", name),
        ch_data.get("logo_url", name + ".png"),
        ch_data.get("multicast_url", ""),
        ch_data.get("unicast_url", ""),
        ch_data.get("category_id", 0),
        now,
        now,
    ))
    conn.commit()
    ch_id = c.lastrowid
    conn.close()
    return ch_id


def update_channels_order(order_list: list) -> bool:
    """批量更新频道排序（sort_index）。

    Args:
        order_list: [{"id": 1, "sort_index": 0}, ...]
    """
    conn = get_db_connection()
    c = conn.cursor()
    for item in order_list:
        c.execute("UPDATE live_channels SET sort_index = ? WHERE id = ?",
                  (item["sort_index"], item["id"]))
    conn.commit()
    conn.close()
    return True


# ============================================================
# 服务器频道同步（核心）
# ============================================================

def sync_channels_from_server(channel_list: list) -> dict:
    """将服务器频道列表 UPSERT 到数据库。

    Args:
        channel_list: get_channel_list() 返回的字典列表

    Returns:
        {"synced": int, "new": int, "updated": int, "disabled": int}
    """
    if not channel_list:
        logger.warning("[LiveDB] 频道列表为空，跳过同步")
        return {"synced": 0, "new": 0, "updated": 0, "disabled": 0}

    conn = get_db_connection()
    c = conn.cursor()
    sync_time = int(time.time())
    new_count = 0
    updated_count = 0

    for ch in channel_list:
        channel_id = ch.get("channel_id", "")
        if not channel_id:
            continue

        # 检查是否已存在
        c.execute("SELECT id FROM live_channels WHERE channel_id = ? AND source = 'server'", (channel_id,))
        existing = c.fetchone()

        if existing:
            # UPDATE: 只覆盖服务器下发字段
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
                    synced_at = ?
                WHERE id = ?
            """, (
                ch.get("user_channel_id", ""),
                ch.get("name", ""),
                ch.get("multicast_url", ""),
                ch.get("unicast_url", ""),
                ch.get("unicast_url_full", ""),
                ch.get("timeshift_enabled", 0),
                ch.get("timeshift_length", 0),
                ch.get("timeshift_url", ""),
                ch.get("is_hd", 0),
                ch.get("channel_type", ""),
                ch.get("channel_sdp", ""),
                ch.get("channel_url_raw", ""),
                ch.get("channel_locked", 0),
                ch.get("preview_enabled", 0),
                ch.get("fcc_enabled", 0),
                ch.get("fcc_ip", ""),
                ch.get("fcc_port", ""),
                ch.get("fec_port", ""),
                ch.get("raw_fields_json", ""),
                sync_time,
                existing["id"],
            ))
            updated_count += 1
        else:
            # INSERT: 用户自定义字段填充默认值
            name = ch.get("name", "")
            now = int(time.time())
            c.execute("""
                INSERT INTO live_channels (
                    source, channel_id, user_channel_id, name,
                    tvg_id, tvg_name, logo_url,
                    category_id, sort_index, is_enabled,
                    multicast_url, unicast_url, unicast_url_full,
                    timeshift_enabled, timeshift_length, timeshift_url,
                    is_hd, channel_type, channel_sdp, channel_url_raw,
                    channel_locked, preview_enabled,
                    fcc_enabled, fcc_ip, fcc_port, fec_port,
                    raw_fields_json,
                    synced_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "server",
                channel_id,
                ch.get("user_channel_id", ""),
                name,
                name,                                    # tvg_id 默认 = name
                name,                                    # tvg_name 默认 = name
                name + ".png" if name else "",           # logo_url 默认
                0,                                       # category_id
                0,                                       # sort_index
                1,                                       # is_enabled
                ch.get("multicast_url", ""),
                ch.get("unicast_url", ""),
                ch.get("unicast_url_full", ""),
                ch.get("timeshift_enabled", 0),
                ch.get("timeshift_length", 0),
                ch.get("timeshift_url", ""),
                ch.get("is_hd", 0),
                ch.get("channel_type", ""),
                ch.get("channel_sdp", ""),
                ch.get("channel_url_raw", ""),
                ch.get("channel_locked", 0),
                ch.get("preview_enabled", 0),
                ch.get("fcc_enabled", 0),
                ch.get("fcc_ip", ""),
                ch.get("fcc_port", ""),
                ch.get("fec_port", ""),
                ch.get("raw_fields_json", ""),
                sync_time,
                now,
            ))
            new_count += 1

    conn.commit()

    # 下线频道软删除：server 源且 synced_at != sync_time → is_enabled = 0
    c.execute("""
        UPDATE live_channels SET is_enabled = 0
        WHERE source = 'server' AND synced_at != ? AND synced_at > 0
    """, (sync_time,))
    disabled = c.rowcount

    conn.commit()
    conn.close()

    synced = new_count + updated_count
    logger.info("[LiveDB] 同步完成: 新增 %d, 更新 %d, 下线 %d", new_count, updated_count, disabled)

    return {
        "synced": synced,
        "new": new_count,
        "updated": updated_count,
        "disabled": disabled,
    }


def parse_sample_to_channels(raw_html: str) -> list:
    """解析包含 CTCSetConfig 调用的原始样本 HTML 为频道列表。

    用于测试环境：将样本 JS 代码中的 Channel 配置解析为与 get_channel_list()
    相同格式的列表，然后可传给 sync_channels_from_server()。

    Args:
        raw_html: 包含 Authentication.CTCSetConfig('Channel',...) 的原始 HTML/JS

    Returns:
        channel_list，格式同 get_channel_list()
    """
    from src.auth.simulator import STBSimulator
    return STBSimulator.parse_channel_response(raw_html)


# ============================================================
# 外部频道导入
# ============================================================

def _match_category(name: str) -> int:
    """根据 group-title 匹配 live_categories.id。

    先精确匹配，再模糊匹配（如"央视"→"央视高清"），无匹配返回 10（其他）。
    """
    conn = get_db_connection()
    c = conn.cursor()

    # 1. 精确匹配
    c.execute("SELECT id FROM live_categories WHERE name = ?", (name,))
    row = c.fetchone()
    if row:
        conn.close()
        return row["id"]

    # 2. 模糊匹配（分类名包含传入名，或传入名包含分类名）
    c.execute("SELECT id, name FROM live_categories")
    cats = c.fetchall()
    conn.close()

    for cat in cats:
        cat_name = cat["name"]
        # 央视 → 央视高清 / 央视标清
        if cat_name in name or name in cat_name:
            return cat["id"]

    return 10


def _parse_m3u_content(content: str) -> list:
    """解析 M3U 内容为频道列表。

    智能识别 URL 类型：
      - igmp:// 开头 → 组播地址
      - http://.../udp/IP:PORT  → 还原为 igmp://IP:PORT（组播地址）
      - 普通 http://  → 单播地址（external 频道不导入无组播的条目）

    Returns:
        [{"tvg_id": "", "tvg_name": "", "logo_url": "", "name": "",
          "group_title": "", "multicast_url": "", "unicast_url": ""}, ...]
    """
    import re as _re
    channels = []
    lines = content.strip().splitlines()
    current_extinf = ""
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            current_extinf = line
        elif not line.startswith("#") and current_extinf:
            extinf = current_extinf
            tvg_id = _re.search(r'tvg-id="([^"]*)"', extinf)
            tvg_name = _re.search(r'tvg-name="([^"]*)"', extinf)
            tvg_logo = _re.search(r'tvg-logo="([^"]*)"', extinf)
            group_title = _re.search(r'group-title="([^"]*)"', extinf)
            name_match = _re.search(r',\s*(.+)$', extinf)
            name = name_match.group(1).strip() if name_match else ""

            logo_raw = tvg_logo.group(1) if tvg_logo else ""
            logo_name = logo_raw.rsplit("/", 1)[-1] if logo_raw else ""

            # ---------- 智能识别组播 / 单播 ----------
            multicast_url = ""
            unicast_url = ""

            if line.startswith("igmp://"):
                # 原生组播地址，直接使用
                multicast_url = line
            elif "/udp/" in line:
                # udpxy 代理地址：http://x.x.x.x:port/udp/233.50.201.118:5140
                # 还原为 igmp://233.50.201.118:5140
                m_udp = _re.search(r'/udp/(\d+\.\d+\.\d+\.\d+:\d+)', line)
                if m_udp:
                    multicast_url = "igmp://" + m_udp.group(1)
                else:
                    # 无法提取有效组播 IP:Port，跳过
                    current_extinf = ""
                    continue
            elif line.startswith("rtp://") or line.startswith("udp://"):
                # 其他组播协议，原样保留
                multicast_url = line
            elif line.startswith("http://") or line.startswith("https://"):
                # 普通 HTTP(S) 单播地址 → 仅存入 unicast_url，不做组播导入
                unicast_url = line
            else:
                # 无法识别，跳过
                current_extinf = ""
                continue

            # 外部频道只导入有有效组播地址的条目（纯单播 http 跳过）
            if not multicast_url:
                current_extinf = ""
                continue

            ch = {
                "tvg_id": tvg_id.group(1) if tvg_id else name,
                "tvg_name": tvg_name.group(1) if tvg_name else name,
                "logo_url": logo_name,
                "name": name,
                "group_title": group_title.group(1) if group_title else "",
                "multicast_url": multicast_url,
                "unicast_url": unicast_url,
            }
            channels.append(ch)
            current_extinf = ""
    return channels


def import_external_channels(channel_list: list) -> dict:
    """导入外部频道（M3U 解析后），按组播地址去重写入。

    Args:
        channel_list: _parse_m3u_content 返回的列表

    Returns:
        {"new": int, "skipped": int, "total": int}
    """
    conn = get_db_connection()
    c = conn.cursor()
    now = int(time.time())
    new_count = 0
    skip_count = 0

    for ch in channel_list:
        multicast = ch.get("multicast_url", "")
        name = ch.get("name", "")

        # 与服务器来源频道对比去重，服务器已有的组播地址不导入
        if multicast:
            c.execute(
                "SELECT id FROM live_channels WHERE multicast_url = ? AND source = 'server'",
                (multicast,),
            )
            if c.fetchone():
                skip_count += 1
                continue

        # 匹配分类
        group_title = ch.get("group_title", "")
        category_id = _match_category(group_title) if group_title else 0

        tvg_id = ch.get("tvg_id") or name
        tvg_name = ch.get("tvg_name") or name
        logo_url = ch.get("logo_url") or (name + ".png" if name else "")

        c.execute("""
            INSERT INTO live_channels (
                source, channel_id, name, tvg_id, tvg_name, logo_url,
                multicast_url, unicast_url, category_id, is_enabled, created_at, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            "external",
            "",
            name,
            tvg_id,
            tvg_name,
            logo_url,
            multicast,
            ch.get("unicast_url", ""),
            category_id,
            now,
            now,
        ))
        new_count += 1

    conn.commit()
    conn.close()

    logger.info("[LiveDB] 导入外部频道: 新增 %d, 跳过 %d (已存在)", new_count, skip_count)
    return {"new": new_count, "skipped": skip_count, "total": new_count + skip_count}


def get_channels_stats() -> dict:
    """获取频道统计信息。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT source, is_enabled FROM live_channels")
    rows = c.fetchall()
    conn.close()
    server_enabled = sum(1 for r in rows if r["source"] == "server" and r["is_enabled"])
    server_disabled = sum(1 for r in rows if r["source"] == "server" and not r["is_enabled"])
    ext_enabled = sum(1 for r in rows if r["source"] == "external" and r["is_enabled"])
    ext_disabled = sum(1 for r in rows if r["source"] == "external" and not r["is_enabled"])
    return {
        "server_total": server_enabled + server_disabled,
        "server_enabled": server_enabled,
        "server_disabled": server_disabled,
        "external_total": ext_enabled + ext_disabled,
        "external_enabled": ext_enabled,
        "external_disabled": ext_disabled,
        "total_enabled": server_enabled + ext_enabled,
        "total": len(rows),
    }
