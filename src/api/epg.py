"""
EPG API 路由 — 同步触发、XMLTV 生成、节目查询。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from src.db.models import get_db_connection
from src.sync.epg_sync import epg_sync_status, start_epg_sync
from src.utils.logger import logger

router = APIRouter(prefix="/api/epg", tags=["epg"])

_simulator = None
_login_func = None


def set_simulator(sim):
    global _simulator
    _simulator = sim


def set_login_func(func):
    global _login_func
    _login_func = func


def _ensure_auth() -> bool:
    """确保 STB 已认证，未认证则尝试登录。"""
    if _simulator and not _simulator.state.is_authenticated:
        if _login_func:
            return _login_func()
    return _simulator and _simulator.state.is_authenticated


# ── 同步管理 ──────────────────────────────────────────

@router.post("/sync")
async def trigger_epg_sync():
    """触发 EPG 数据同步（后台线程执行）。"""
    if not _simulator:
        return JSONResponse({"status": "error", "message": "模拟器未初始化"}, status_code=500)

    if not _ensure_auth():
        return JSONResponse({"status": "error", "message": "STB 未认证，请先配置凭证并登录"}, status_code=503)

    if epg_sync_status["running"]:
        return {"status": "already_running", "message": "EPG 同步已在运行中"}

    start_epg_sync(_simulator)
    return {"status": "started", "message": "EPG 同步已启动"}


@router.get("/sync/status")
async def get_epg_sync_status():
    """查询 EPG 同步状态。"""
    return epg_sync_status


# ── XMLTV 生成 ────────────────────────────────────────

@router.get("/xmltv.xml")
async def get_xmltv():
    """生成 XMLTV 格式的 EPG XML。"""
    conn = get_db_connection()
    c = conn.cursor()

    # 获取所有有节目数据的频道
    c.execute("""
        SELECT DISTINCT epg_channel_id, channel_name
        FROM epg_programs
        WHERE epg_channel_id != ''
    """)
    channels = c.fetchall()

    # 获取频道的 back_time 配置映射（以 tvg_id 为键，取该 ID 下最大 back_time，默认 0）
    c.execute("""
        SELECT tvg_id, MAX(back_time) as max_back_time
        FROM live_channels
        WHERE tvg_id != '' AND is_enabled = 1
        GROUP BY tvg_id
    """)
    back_time_map = {row["tvg_id"]: (row["max_back_time"] or 0) for row in c.fetchall()}

    # 获取从过去 7 天开始的所有节目（所有频道的最大数据范围，后面在 Python 中做动态裁剪）
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
    c.execute("""
        SELECT epg_channel_id, title, start_time, end_time
        FROM epg_programs
        WHERE end_time >= ?
        GROUP BY epg_channel_id, start_time, title
        ORDER BY epg_channel_id, start_time
    """, (seven_days_ago,))
    programs = c.fetchall()

    # 计算今天开始的绝对时刻（今天 00:00:00）
    now_dt = datetime.now()
    today_start = datetime(now_dt.year, now_dt.month, now_dt.day)

    # 组装 XML
    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv generator-info-name="IPTV-Toolkit EPG">',
    ]

    # 频道声明
    seen_channels = set()
    for ch in channels:
        ch_id = ch["epg_channel_id"]
        ch_name = ch["channel_name"]
        if ch_id in seen_channels:
            continue
        seen_channels.add(ch_id)
        xml_parts.append(f'  <channel id="{_xml_escape(ch_id)}">')
        xml_parts.append(f'    <display-name>{_xml_escape(ch_name)}</display-name>')
        xml_parts.append(f'  </channel>')

    # 节目数据
    for prog in programs:
        ch_id = prog["epg_channel_id"]
        title = prog["title"]
        start_time_str = prog["start_time"]
        end_time_str = prog["end_time"]

        # 根据该频道的 back_time 进行动态天数剪裁
        back_days = back_time_map.get(ch_id, 0)
        try:
            prog_end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        cutoff_dt = today_start - timedelta(days=back_days)
        if prog_end_dt < cutoff_dt:
            continue

        start = _format_xmltv_time(start_time_str)
        end = _format_xmltv_time(end_time_str)
        if not start or not end:
            continue
        xml_parts.append(f'  <programme channel="{_xml_escape(ch_id)}" start="{start}" stop="{end}">')
        xml_parts.append(f'    <title lang="zh">{_xml_escape(title)}</title>')
        xml_parts.append(f'  </programme>')

    xml_parts.append("</tv>")
    conn.close()

    return Response(
        content="\n".join(xml_parts),
        media_type="application/xml",
        headers={"Content-Disposition": "inline; filename=epg.xml"},
    )


# ── 节目查询 ──────────────────────────────────────────

@router.get("/programs")
async def query_programs(
    channel_id: str = Query(None),
    date: str = Query(None),
    keyword: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """查询节目单（分页）。"""
    conn = get_db_connection()
    c = conn.cursor()

    where_clauses = []
    params = []

    if channel_id:
        where_clauses.append("(channel_id = ? OR epg_channel_id = ?)")
        params.extend([channel_id, channel_id])
    if date:
        where_clauses.append("program_date = ?")
        params.append(date)
    if keyword:
        where_clauses.append("title LIKE ?")
        params.append(f"%{keyword}%")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # 计数（按 epg_channel_id、start_time、title 去重，口径与 XMLTV programme 一致）
    c.execute(f"SELECT COUNT(DISTINCT epg_channel_id || '_' || start_time || '_' || title) FROM epg_programs WHERE {where_sql}", params)
    total = c.fetchone()[0]

    # 分页查询（按 epg_channel_id、start_time、title 去重，避免多画质版本重复显示数据）
    offset = (page - 1) * limit
    c.execute(f"""
        SELECT id, channel_id, channel_name, title, start_time, end_time, program_date
        FROM epg_programs WHERE {where_sql}
        GROUP BY epg_channel_id, start_time, title
        ORDER BY start_time
        LIMIT ? OFFSET ?
    """, params + [limit, offset])
    rows = c.fetchall()

    items = [dict(r) for r in rows]
    conn.close()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": items,
    }


@router.get("/programs/now")
async def programs_now():
    """查询当前正在播放的节目。"""
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        SELECT channel_id, channel_name, title, start_time, end_time, epg_channel_id
        FROM epg_programs
        WHERE start_time <= ? AND end_time >= ?
        GROUP BY epg_channel_id, title
        ORDER BY epg_channel_id
    """, (now, now))
    rows = c.fetchall()
    items = [dict(r) for r in rows]
    conn.close()
    return {"total": len(items), "items": items}


# ── 统计 ──────────────────────────────────────────────

@router.get("/stats")
async def epg_stats():
    """EPG 数据统计。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM epg_programs")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT channel_id) FROM epg_programs")
    channels = c.fetchone()[0]
    c.execute("SELECT MIN(program_date), MAX(program_date) FROM epg_programs")
    row = c.fetchone()
    conn.close()
    return {
        "total_programs": total,
        "total_channels": channels,
        "date_range": {"earliest": row[0], "latest": row[1]},
        "last_sync_time": epg_sync_status.get("last_sync_time"),
    }


# ── 工具函数 ──────────────────────────────────────────

def _xml_escape(text: str) -> str:
    """XML 转义。"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


def _format_xmltv_time(time_str: str) -> str:
    """将 "YYYY-MM-DD HH:MM:SS" 转为 XMLTV 格式 "YYYYMMDDHHMMSS +0800"。"""
    if not time_str or len(time_str) < 19:
        return ""
    try:
        dt = time_str[:19].replace("-", "").replace(" ", "").replace(":", "")
        return dt + " +0800"
    except Exception:
        return ""
