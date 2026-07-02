"""
直播频道 API 路由模块 - 频道同步、CRUD、M3U 生成等。
"""
import json
import re
import requests as _requests
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, PlainTextResponse

from src.utils.logger import logger
from src.db import live_crud

# 模拟器实例引用（由 main.py 注入）
_simulator = None

router = APIRouter(prefix="/api/live", tags=["live"])


def set_simulator(sim):
    """注入 STBSimulator 实例。"""
    global _simulator
    _simulator = sim


# ============================================================
# 配置
# ============================================================

@router.get("/config")
async def get_config():
    return live_crud.get_live_config()


@router.put("/config")
async def update_config(updates: dict):
    live_crud.update_live_config(updates)
    return {"status": "ok"}


# ============================================================
# 同步
# ============================================================

@router.post("/sync")
async def trigger_sync():
    """触发服务器频道同步。"""
    sim = _simulator
    if sim is None:
        return JSONResponse(content={"error": "模拟器未初始化"}, status_code=500)
    if not sim.state.is_authenticated:
        return JSONResponse(content={"error": "模拟器未认证，请先登录"}, status_code=400)

    try:
        channels = sim.get_channel_list()
        if not channels:
            return {"status": "warning", "message": "未拉取到任何频道", "count": 0}

        result = live_crud.sync_channels_from_server(channels)
        return {
            "status": "success",
            "synced": result["synced"],
            "new": result["new"],
            "updated": result["updated"],
            "disabled": result["disabled"],
            "message": f"同步完成，新增{result['new']}个，更新{result['updated']}个，下线{result['disabled']}个",
        }
    except Exception as e:
        logger.error("[LiveAPI] 同步失败: %s", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/sync/test")
async def trigger_sync_test(request: Request):
    """测试用同步：直接提交样本 HTML/JS 数据。"""
    try:
        body = await request.json()
    except Exception:
        body = await request.body()
        body = body.decode("utf-8", errors="ignore")

    raw_text = body if isinstance(body, str) else body.get("content", "")

    try:
        channels = _simulator.parse_channel_response(raw_text) if _simulator else []
        if not channels:
            from src.auth.simulator import STBSimulator
            channels = STBSimulator.parse_channel_response(raw_text)

        if not channels:
            return {"status": "warning", "message": "未解析到任何频道", "count": 0}

        result = live_crud.sync_channels_from_server(channels)
        return {
            "status": "success",
            "synced": result["synced"],
            "new": result["new"],
            "updated": result["updated"],
            "disabled": result["disabled"],
            "message": f"同步完成，新增{result['new']}个，更新{result['updated']}个，下线{result['disabled']}个",
        }
    except Exception as e:
        logger.error("[LiveAPI] 测试同步失败: %s", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ============================================================
# 分类
# ============================================================

@router.get("/categories")
async def get_categories():
    return live_crud.get_categories()


@router.post("/categories")
async def add_category(data: dict):
    try:
        result = live_crud.add_category(data.get("name", ""), data.get("sort_index", 99))
        return result
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=409)


@router.put("/categories/{cat_id}")
async def update_category(cat_id: int, data: dict):
    ok = live_crud.update_category(cat_id, data)
    return {"status": "ok" if ok else "noop"}


@router.delete("/categories/{cat_id}")
async def delete_category(cat_id: int):
    ok = live_crud.delete_category(cat_id)
    if ok:
        return {"status": "deleted"}
    return JSONResponse(content={"error": "分类不存在"}, status_code=404)


# ============================================================
# 频道 CRUD
# ============================================================

@router.get("/channels")
async def get_channels(
    category_id: int = None,
    source: str = None,
    enabled: int = None,
    page: int = 1,
    limit: int = 100,
):
    return live_crud.get_channels(
        category_id=category_id,
        source=source,
        enabled=enabled,
        page=page,
        limit=limit,
    )


@router.get("/channels/{ch_id}")
async def get_channel(ch_id: int):
    ch = live_crud.get_channel(ch_id)
    if ch:
        return ch
    return JSONResponse(content={"error": "频道不存在"}, status_code=404)


@router.put("/channels/{ch_id}")
async def update_channel(ch_id: int, data: dict):
    ok = live_crud.update_channel(ch_id, data)
    return {"status": "ok" if ok else "noop"}


@router.delete("/channels/{ch_id}")
async def delete_channel(ch_id: int):
    ok = live_crud.delete_channel(ch_id)
    if ok:
        return {"status": "deleted"}
    return JSONResponse(content={"error": "仅可删除外部频道"}, status_code=403)


@router.post("/channels")
async def add_channel(data: dict):
    ch_id = live_crud.add_external_channel(data)
    return {"status": "created", "id": ch_id}


@router.post("/channels/reorder")
async def reorder_channels(data: dict):
    """批量更新频道排序。"""
    order_list = data.get("order", [])
    ok = live_crud.update_channels_order(order_list)
    return {"status": "ok" if ok else "noop"}


@router.delete("/channels/external/all")
async def delete_all_external():
    """删除全部外部频道。"""
    deleted = live_crud.delete_all_external_channels()
    return {"status": "deleted", "count": deleted}


@router.post("/channels/batch-delete")
async def batch_delete(data: dict):
    """批量删除外部频道。"""
    ids = data.get("ids", [])
    if not ids:
        return JSONResponse(content={"error": "未提供频道ID"}, status_code=400)
    deleted = live_crud.batch_delete_channels(ids)
    return {"status": "deleted", "count": deleted}


@router.post("/channels/batch-category")
async def batch_category(data: dict):
    """批量修改频道分类。"""
    ids = data.get("ids", [])
    category_id = data.get("category_id")
    if not ids or category_id is None:
        return JSONResponse(content={"error": "参数不完整"}, status_code=400)
    updated = live_crud.batch_update_category(ids, category_id)
    return {"status": "updated", "count": updated}


# ============================================================
# 频道统计
# ============================================================

@router.get("/stats")
async def get_stats():
    return live_crud.get_channels_stats()


# ============================================================
# 外部频道导入
# ============================================================

@router.post("/import")
async def import_channels(request: Request):
    """导入外部频道（M3U 格式）。

    支持三种方式：
    1. JSON: {"content": "#EXTM3U\\n..."} — 粘贴 M3U 内容
    2. JSON: {"url": "http://.../xxx.m3u"} — 从 URL 拉取
    3. Multipart: 上传 .m3u 文件
    """
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        file: UploadFile = form.get("file")
        if not file:
            return JSONResponse(content={"error": "未提供文件"}, status_code=400)
        raw = (await file.read()).decode("utf-8", errors="ignore")
    else:
        try:
            body = await request.json()
        except Exception:
            try:
                raw_body = await request.body()
                raw = raw_body.decode("utf-8", errors="ignore")
            except Exception as e:
                return JSONResponse(content={"error": f"无效的请求格式: {e}"}, status_code=400)
        else:
            url = body.get("url", "")
            if url:
                try:
                    resp = _requests.get(url, timeout=30, headers={"User-Agent": "IPTV-Toolkit/1.0"})
                    resp.raise_for_status()
                    raw = resp.text
                except Exception as e:
                    logger.error("[LiveAPI] URL 获取失败: %s", e)
                    return JSONResponse(content={"error": f"无法获取 URL: {e}"}, status_code=400)
            else:
                raw = body.get("content", "")

    if not raw or not raw.strip():
        return JSONResponse(content={"error": "内容为空"}, status_code=400)

    try:
        channels = live_crud._parse_m3u_content(raw)
    except Exception as e:
        logger.error("[LiveAPI] 导入解析失败: %s", e)
        return JSONResponse(content={"error": f"解析失败: {e}"}, status_code=400)

    if not channels:
        return {"status": "warning", "message": "未识别到任何频道", "new": 0, "skipped": 0, "total": 0}

    result = live_crud.import_external_channels(channels)
    return {
        "status": "success",
        "new": result["new"],
        "skipped": result["skipped"],
        "total": result["total"],
        "message": f"导入完成，新增 {result['new']} 个频道，跳过 {result['skipped']} 个（已存在）",
    }


@router.post("/import/preview")
async def preview_import(request: Request):
    """预览 M3U 导入内容（不写入数据库，返回解析结果）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "无效的 JSON 格式"}, status_code=400)

    url = body.get("url", "")
    if url:
        try:
            resp = _requests.get(url, timeout=15, headers={"User-Agent": "IPTV-Toolkit/1.0"})
            resp.raise_for_status()
            raw = resp.text
        except Exception as e:
            logger.error("[LiveAPI] URL 获取失败: %s", e)
            return JSONResponse(content={"error": f"无法获取 URL: {e}"}, status_code=400)
    else:
        raw = body.get("content", "")

    if not raw or not raw.strip():
        return JSONResponse(content={"error": "内容为空"}, status_code=400)

    try:
        channels = live_crud._parse_m3u_content(raw)
    except Exception as e:
        return JSONResponse(content={"error": f"解析失败: {e}"}, status_code=400)

    return {"channels": channels, "count": len(channels)}


# ============================================================
# M3U 生成
# ============================================================

@router.get("/tv.m3u")
async def generate_m3u(
    category_id: int = None,
    source: str = None,
):
    """生成 M3U 播放列表。
    注：当前为简化版，Phase 3 将完整实现 udpxy + FCC + catchup。
    """
    config = live_crud.get_live_config()
    result = live_crud.get_channels(
        category_id=category_id,
        source=source,
        enabled=1,
        page=1,
        limit=9999,
    )
    channels = result["list"]
    cats = {c["id"]: c["name"] for c in live_crud.get_categories()}

    epg_url = config.get("epg_url", "")
    logo_base = config.get("logo_base_url", "")
    udpxy = config.get("udpxy_address", "")
    udpxy_enabled = int(config.get("udpxy_enabled", "1"))
    fcc_global = int(config.get("fcc_global_enabled", "1"))
    timeshift_global = int(config.get("timeshift_enabled", "0"))
    dual_line = int(config.get("m3u_dual_line", "0"))

    lines = []
    # 头部
    if epg_url:
        lines.append(f'#EXTM3U x-tvg-url="{epg_url}"')
    else:
        lines.append("#EXTM3U")

    for ch in channels:
        tvg_id = ch.get("tvg_id") or ch["name"]
        tvg_name = ch.get("tvg_name") or ch["name"]
        logo = (logo_base + ch.get("logo_url", "")) if ch.get("logo_url") else ""
        group = cats.get(ch.get("category_id", 0), "")

        # catchup 判断
        catchup = ""
        catchup_src = ""
        if timeshift_global and ch.get("timeshift_enabled"):
            catchup = 'catchup="default"'
            if ch.get("unicast_url"):
                catchup_src = f'catchup-source="{ch["unicast_url"]}?playseek=${{(b)yyyyMMddHHmmss}}-${{(e)yyyyMMddHHmmss}}"'

        # EXTINF 行
        extinf_parts = [f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}"']
        if logo:
            extinf_parts.append(f'tvg-logo="{logo}"')
        if group:
            extinf_parts.append(f'group-title="{group}"')
        if catchup:
            extinf_parts.append(catchup)
        if catchup_src:
            extinf_parts.append(catchup_src)
        extinf_parts.append(f",{ch['name']}")
        extinf = " ".join(extinf_parts)

        # 组播地址（udpxy 转换，仅在启用时）
        multicast = ch.get("multicast_url", "")
        m3u_multicast = ""
        if multicast.startswith("igmp://"):
            ip_port = multicast[7:]  # 去掉 igmp://
            if udpxy and udpxy_enabled:
                m3u_multicast = f"http://{udpxy}/udp/{ip_port}"
                if fcc_global and ch.get("fcc_ip") and ch.get("fcc_port"):
                    m3u_multicast += f"?fcc={ch['fcc_ip']}:{ch['fcc_port']}"
            else:
                m3u_multicast = multicast  # 保持原始 igmp:// 地址
        else:
            m3u_multicast = multicast

        # 单播地址
        unicast = ch.get("unicast_url", "")

        if dual_line and unicast:
            # 双线：组播 + 单播各一行
            lines.append(extinf)
            lines.append(m3u_multicast)
            lines.append(extinf)
            lines.append(unicast)
        else:
            # 单线：仅组播
            lines.append(extinf)
            lines.append(m3u_multicast)

    return PlainTextResponse("\n".join(lines), media_type="application/vnd.apple.mpegurl")
