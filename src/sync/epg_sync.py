"""
EPG 数据同步模块 — VIS schedules API → SQLite epg_programs 表。
从 VIS 节目单服务器拉取全频道节目数据，按 backTime 分档决定同步天数。
"""
import concurrent.futures
import json
import threading
import time
from datetime import datetime, timedelta

import requests

from src.db.models import get_db_connection
from src.utils.helpers import parse_epg_json
from src.utils.helpers import fetch_with_retry
from src.utils.logger import logger


def _build_tvg_lookup(conn) -> dict:
    """从 live_channels 表构建 channel_id → tvg_id 映射。

    live_channels 的 tvg_id 始终存在，直接取用（与 M3U 同源，保证播放器匹配）。
    某 channel_id 查不到 tvg_id 时，worker 会回退到服务器原始频道名。

    Returns:
        {str(channel_id): str(tvg_id)}
    """
    c = conn.cursor()
    c.execute("SELECT channel_id, tvg_id FROM live_channels")
    lookup = {}
    for row in c.fetchall():
        ch_id = str(row["channel_id"])
        tvg = (row["tvg_id"] or "").strip()
        if tvg:
            lookup[ch_id] = tvg
    return lookup

# 第二轮整体并发重试次数（不再串行 sleep，间隔由线程池并发度控制）
_MAX_BATCH_RETRIES = 2

# 同步状态
epg_sync_status = {
    "running": False,
    "progress": "",
    "current_channel": "",
    "done": 0,
    "total": 0,
    "last_sync_time": None,
    "last_error": None,
    "channel_count": 0,
    "program_count": 0,
}


def _set_epg_status(**kwargs):
    """线程安全地更新 EPG 同步状态。"""
    global epg_sync_status
    for k, v in kwargs.items():
        epg_sync_status[k] = v


def get_channel_code_mapping(sim):
    """通过 data.jsp channelListAll 获取 channelID → channelCode 映射。

    Args:
        sim: STBSimulator 实例（需已登录）

    Returns:
        dict: {channel_id: {"code": str, "backTime": int, "name": str}}
    """
    if not sim.state.is_authenticated:
        logger.error("[EPG Sync] 未认证，无法获取频道编码映射")
        return {}

    data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
    try:
        res = sim.state.session.get(
            data_url,
            params={"Action": "channelListAll"},
            headers=sim.config.headers,
            timeout=15,
        )
        data = parse_epg_json(res.text)
        mapping = {}
        for item in data.get("result", []):
            cid = str(item["channelID"])
            mapping[cid] = {
                "code": item.get("code", ""),
                "backTime": item.get("backTime", 0),
                "name": item.get("name", ""),
            }
        logger.info("[EPG Sync] 获取到 %d 个频道的编码映射", len(mapping))
        return mapping
    except Exception as e:
        logger.error("[EPG Sync] 获取频道编码映射失败: %s", e)
        return {}


def _fetch_schedule(vis_base: str, channel_code: str, begintime: str, endtime: str, headers: dict = None) -> list:
    """拉取单个频道的时间段节目单（带重试）。

    Args:
        vis_base: VIS 服务器基址（sim.state.vis_base_url，形如 http://ip:port/epg/）

    Returns:
        tuple: (programs: list, error: str|None)
            - programs: resultSet 列表，空列表表示无数据（非错误）
            - error: 非空字符串表示请求失败，None 表示成功（包括返回空数据的情况）
    """
    url = f"{vis_base}api/schedules/{channel_code}.json"
    params = {"begintime": begintime, "endtime": endtime}

    try:
        res = fetch_with_retry(url, params=params, headers=headers, timeout=15, tag="EPG Sync")
    except requests.RequestException as e:
        logger.error("[EPG Sync] 请求耗尽重试: %s", e)
        return [], str(e)

    if res.status_code == 200:
        return res.json().get("resultSet", []), None
    logger.warning("[EPG Sync] schedules API HTTP %d for code=%s",
                   res.status_code, channel_code)
    return [], f"HTTP {res.status_code}"



def _upsert_programs(conn, programs: list, channel_id: str, channel_name: str,
                     epg_channel_id: str, sync_time: int) -> int:
    """批量 UPSERT 节目数据到 epg_programs。

    Returns:
        成功写入的条数
    """
    c = conn.cursor()
    count = 0
    for prog in programs:
        title = prog.get("title", "")
        start = prog.get("startTime", "")
        end = prog.get("endTime", "")
        if not title or not start or not end:
            continue
        # program_date 从 startTime 前8位提取
        program_date = start[:4] + "-" + start[4:6] + "-" + start[6:8]
        # 转换为可读时间格式
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]} {start[8:10]}:{start[10:12]}:{start[12:14]}"
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]} {end[8:10]}:{end[10:12]}:{end[12:14]}"
        raw_json = json.dumps(prog, ensure_ascii=False)

        try:
            c.execute("""
                INSERT INTO epg_programs
                    (channel_id, channel_name, title, start_time, end_time,
                     program_date, epg_channel_id, raw_data_json, synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, start_time, title) DO UPDATE SET
                    end_time=excluded.end_time,
                    raw_data_json=excluded.raw_data_json,
                    synced_at=excluded.synced_at
            """, (channel_id, channel_name, title, start_fmt, end_fmt,
                  program_date, epg_channel_id, raw_json, sync_time, sync_time))
            count += 1
        except Exception as e:
            logger.warning("[EPG Sync] 写入节目失败 (%s/%s): %s", channel_name, title, e)

    conn.commit()
    return count


def _clean_expired(conn, keep_days: int = 9):
    """清理超过保留天数的过期节目数据。

    Args:
        keep_days: 保留最近 N 天的数据（默认 9 天，覆盖最大 backTime=7 + 1天缓冲）
    """
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d 00:00:00")
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs WHERE end_time < ?", (cutoff,))
    deleted = c.rowcount
    conn.commit()
    if deleted:
        logger.info("[EPG Sync] 清理 %d 条超过 %d 天的过期节目", deleted, keep_days)


def full_sync(sim) -> dict:
    """全量同步 EPG 数据。

    1. 登录 → channelListAll 获取 channelCode 映射
    2. 按 backTime 决定每个频道的同步日期范围
    3. 逐频道查询 VIS schedules API
    4. UPSERT 写入 epg_programs
    5. 清理过期数据

    Returns:
        {"channel_count": int, "program_count": int}
    """
    _set_epg_status(
        running=True,
        progress="获取频道编码映射...",
        done=0, total=0,
        last_error=None,
        channel_count=0, program_count=0,
    )

    # VIS 服务器基址（与 filter_sync 统一，均取自登录后解析的地址）
    vis_base = sim.state.vis_base_url
    if not vis_base:
        logger.error("[EPG Sync] VIS 服务器地址未解析，无法同步")
        _set_epg_status(running=False, last_error="VIS 服务器地址未解析")
        return {"channel_count": 0, "program_count": 0}

    # Step 1: 获取 channelCode 映射
    code_mapping = get_channel_code_mapping(sim)
    if not code_mapping:
        _set_epg_status(running=False, last_error="获取频道编码映射失败")
        return {"channel_count": 0, "program_count": 0}

    # 一次性更新所有频道在数据库中的 back_time
    conn_bt = get_db_connection()
    c_bt = conn_bt.cursor()
    for cid, info in code_mapping.items():
        back_time = info.get("backTime", 0)
        try:
            c_bt.execute("UPDATE live_channels SET back_time = ? WHERE channel_id = ?", (back_time, cid))
        except Exception as e:
            logger.warning("[EPG Sync] 更新 channel_id=%s 的 back_time 失败: %s", cid, e)
    conn_bt.commit()
    conn_bt.close()

    # Step 2: 确定待同步频道总数（直接用服务器返回的频道映射，不做去重）
    total_channels = len(code_mapping)
    logger.info("[EPG Sync] %d 个频道待同步", total_channels)

    _set_epg_status(total=total_channels, progress="开始同步节目数据...")

    # Step 3: 并发逐频道同步（线程池拉取 VIS schedules，每个 worker 独立 DB 连接）
    # 同步所有频道，每个频道独立写库（无跨频道合并）。
    # epg_channel_id 取自 live_channels.tvg_id —— HD/SD/4K 兄弟频道在 live_channels
    # 里被配成同一 tvg_id，因此会写入同一个 epg_channel_id；"落库多条但 tvg_id 归并"
    # 正是这个原因。XMLTV 生成时按 tvg_id + 时段去重，播放器侧不会看到重复节目。
    # 仅当某 channel_id 在 live_channels 中查不到 tvg_id 时，才回退到服务器原始频道名。
    today = datetime.now()
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y%m%d")
    begin = (today - timedelta(days=7)).strftime("%Y%m%d")  # 所有频道统一同步范围

    conn = get_db_connection()
    sync_time = int(time.time())
    total_programs = 0
    ok_channels = 0
    no_data_channels = 0
    failed_channels = []  # 仅记录网络请求失败的频道（(code, info)）

    # 从 live_channels 获取 channel_id → tvg_id 映射（与 M3U 同一来源，确保匹配）
    tvg_lookup = _build_tvg_lookup(conn)

    headers = sim.config.headers

    def _worker(code, channel_id, info):
        """单个频道同步 worker：拉取 → 独立连接写库。返回 (status, count, name)。"""
        channel_name = info["name"]
        # epg_channel_id 优先取 live_channels.tvg_id（与 M3U 同源，保证播放器匹配）；
        # HD/SD/4K 兄弟频道因此共享同一 epg_channel_id。仅当该 channel_id 在
        # live_channels 中查不到 tvg_id 时，才回退到服务器原始频道名（不归一化）。
        epg_chid = tvg_lookup.get(channel_id) or channel_name
        programs, error = _fetch_schedule(vis_base, code, begin, tomorrow_str, headers=headers)
        if error:
            return ("failed", 0, channel_name)
        if not programs:
            return ("nodata", 0, channel_name)
        wconn = get_db_connection()
        try:
            count = _upsert_programs(wconn, programs, channel_id, channel_name, epg_chid, sync_time)
        finally:
            wconn.close()
        return ("ok", count, channel_name)

    def _drain(executor, task_list, count_progress=True):
        """提交一批任务并在主线程汇总进度，返回仍失败的 (cid, info) 列表（供下一轮重试）。"""
        nonlocal total_programs, ok_channels, no_data_channels, done_count
        futures = {
            executor.submit(_worker, info["code"], cid, info): (cid, info)
            for cid, info in task_list
        }
        remaining = []
        for future in concurrent.futures.as_completed(futures):
            cid, info = futures[future]
            if count_progress:
                done_count += 1
            try:
                status, count, name = future.result()
            except Exception as e:
                logger.warning("[EPG Sync] worker 异常 (%s): %s", info["name"], e)
                status, count, name = "failed", 0, info["name"]
            _set_epg_status(
                progress=f"[{done_count}/{total_channels}] {name}" + ("" if count_progress else " (重试)"),
                current_channel=name,
                done=done_count,
            )
            if status == "ok":
                total_programs += count
                ok_channels += 1
            elif status == "nodata":
                no_data_channels += 1
            elif status == "failed":
                remaining.append((cid, info))
        return remaining

    done_count = 0

    # 第一轮：全量并发拉取（max_workers=5，不加额外限流）
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        failed_channels = _drain(executor, list(code_mapping.items()))

    logger.info("[EPG Sync] 第一轮: %d 有数据, %d 无EPG, %d 请求失败",
                ok_channels, no_data_channels, len(failed_channels))

    # 第二轮：只并发重试网络请求失败的频道
    for retry_round in range(_MAX_BATCH_RETRIES):
        if not failed_channels:
            break
        logger.info("[EPG Sync] 第 2 轮重试 %d 个失败频道 (第 %d/%d 次)",
                    len(failed_channels), retry_round + 1, _MAX_BATCH_RETRIES)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            failed_channels = _drain(executor, failed_channels, count_progress=False)

    if failed_channels:
        names = [info["name"] for _, info in failed_channels]
        logger.warning("[EPG Sync] 最终网络失败 %d 个频道: %s", len(failed_channels), ", ".join(names))

    # Step 4: 清理过期
    _clean_expired(conn)
    conn.close()

    _set_epg_status(
        running=False,
        progress="同步完成",
        last_sync_time=int(time.time()),
        done=0, total=0,
        channel_count=ok_channels,
        program_count=total_programs,
    )

    logger.info("[EPG Sync] 完成: %d 频道有数据 (%d 条节目), %d 无EPG, %d 网络失败",
                ok_channels, total_programs, no_data_channels, len(failed_channels))
    return {"channel_count": ok_channels, "program_count": total_programs, "no_data": no_data_channels}


def start_epg_sync(sim):
    """在后台线程中启动 EPG 同步任务。

    Args:
        sim: STBSimulator 实例（需已登录）
    """
    global epg_sync_status
    if epg_sync_status["running"]:
        logger.warning("[EPG Sync] 同步任务已在运行中，跳过")
        return

    def _run():
        try:
            full_sync(sim)
        except Exception as e:
            logger.error("[EPG Sync] 同步任务异常: %s", e, exc_info=True)
            _set_epg_status(running=False, last_error=str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("[EPG Sync] 后台同步任务已启动")
