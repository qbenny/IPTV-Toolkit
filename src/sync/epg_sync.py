"""
EPG 数据同步模块 — VIS schedules API → SQLite epg_programs 表。
从 VIS 节目单服务器拉取全频道节目数据，按 backTime 分档决定同步天数。
"""
import json
import threading
import time
from datetime import datetime, timedelta

import requests

from src.db.models import get_db_connection
from src.utils.helpers import parse_epg_json
from src.utils.logger import logger


def _normalize_epg(name: str) -> str:
    """频道名归一化（fallback 版本，仅在 live_channels 无 tvg_id 时使用）。"""
    import re
    name = name.strip()
    for sfx in ["1080P", "720P", "超清", "高清", "标清", "极清", "FHD", "UHD", "HD", "SD", "4K", "8K"]:
        if name.endswith(sfx) and len(name) > len(sfx):
            name = name[:-len(sfx)]
            break
    m = re.match(r"(CCTV[\d]+[\+]?)", name)
    if m:
        return m.group(1)
    return name.strip()


def _build_tvg_lookup(conn) -> dict:
    """从 live_channels 表构建 channel_id → tvg_id 映射。

    优先使用 live_channels 中已设置的 tvg_id，
    否则用 display_name 归一化作为 tvg_id。

    Returns:
        {str(channel_id): str(epg_channel_id)}
    """
    c = conn.cursor()
    c.execute("SELECT channel_id, tvg_id, display_name, name FROM live_channels")
    lookup = {}
    for row in c.fetchall():
        ch_id = str(row["channel_id"])
        tvg = (row["tvg_id"] or "").strip()
        display = (row["display_name"] or row["name"] or "").strip()
        if tvg:
            lookup[ch_id] = tvg
        elif display:
            lookup[ch_id] = _normalize_epg(display)
    return lookup

# VIS schedules API 地址
VIS_SCHEDULES_BASE = "http://115.233.200.60:58000/epg/api/schedules/"
VIS_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# 请求间隔（秒）
_REQUEST_INTERVAL = 0.2
_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 3
_RETRY_BATCH_INTERVAL = 2  # 第二轮重试的延迟（秒）
_MAX_BATCH_RETRIES = 2     # 第二轮整体重试次数

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


def _fetch_schedule(channel_code: str, begintime: str, endtime: str) -> list:
    """拉取单个频道的时间段节目单（带重试）。

    Returns:
        tuple: (programs: list, error: str|None)
            - programs: resultSet 列表，空列表表示无数据（非错误）
            - error: 非空字符串表示请求失败，None 表示成功（包括返回空数据的情况）
    """
    url = f"{VIS_SCHEDULES_BASE}{channel_code}.json"
    params = {"begintime": begintime, "endtime": endtime}

    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            res = requests.get(url, params=params, headers=VIS_HEADERS, timeout=15)
            if res.status_code == 200:
                return res.json().get("resultSet", []), None
            logger.warning("[EPG Sync] schedules API HTTP %d for code=%s",
                           res.status_code, channel_code)
            return [], f"HTTP {res.status_code}"
        except requests.RequestException as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("[EPG Sync] 请求失败 (尝试 %d/%d)，%d 秒后重试: %s",
                               attempt + 1, _MAX_RETRIES, delay, e)
                time.sleep(delay)
    logger.error("[EPG Sync] 请求耗尽重试: %s", last_exc)
    return [], str(last_exc)


def _dedup_channels(code_mapping: dict) -> dict:
    """对频道按 channelCode 去重，同一 code 只保留一个。

    同频道 HD/SD 共享 channelCode，只同步一次。

    Returns:
        {channel_code: {"channel_id": str, "backTime": int, "name": str}}
    """
    dedup = {}
    for cid, info in code_mapping.items():
        code = info["code"]
        if not code:
            continue
        if code not in dedup:
            dedup[code] = {
                "channel_id": cid,
                "backTime": info["backTime"],
                "name": info["name"],
            }
    return dedup


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

    # Step 1: 获取 channelCode 映射
    code_mapping = get_channel_code_mapping(sim)
    if not code_mapping:
        _set_epg_status(running=False, last_error="获取频道编码映射失败")
        return {"channel_count": 0, "program_count": 0}

    # Step 2: 去重
    dedup = _dedup_channels(code_mapping)
    total_channels = len(dedup)
    logger.info("[EPG Sync] 去重后 %d 个唯一频道待同步", total_channels)

    _set_epg_status(total=total_channels, progress="开始同步节目数据...")

    # Step 3: 逐频道同步（跳过 EPG 已覆盖的 HD/SD 共享频道）
    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y%m%d")

    conn = get_db_connection()
    sync_time = int(time.time())
    total_programs = 0
    ok_channels = 0
    no_data_channels = 0
    skipped_shared = 0   # 因EPG共享而跳过的频道
    failed_channels = []  # 仅记录网络请求失败的频道

    # 从 live_channels 获取 channel_id → tvg_id 映射（与 M3U 同一来源，确保匹配）
    tvg_lookup = _build_tvg_lookup(conn)

    # 收集本次同步中已处理的 EPG channel ID（防止 HD/SD 双写）
    synced_epg_ids = set()

    for i, (code, info) in enumerate(dedup.items()):
        channel_name = info["name"]
        channel_id = info["channel_id"]
        back_time = info["backTime"]

        # 优先用 live_channels 的 tvg_id，保证与 M3U 完全一致
        epg_chid = tvg_lookup.get(channel_id) or _normalize_epg(channel_name)

        # 如果同一 EPG ID 已经在本轮同步过 → 跳过（HD/SD 共享）
        if epg_chid in synced_epg_ids:
            skipped_shared += 1
            logger.debug("[EPG Sync] %s: 跳过(EPG '%s' 已由其他画质版本同步)", channel_name, epg_chid)
            continue

        # 计算日期范围
        if back_time >= 7:
            begin = (today - timedelta(days=7)).strftime("%Y%m%d")
        elif back_time >= 3:
            begin = (today - timedelta(days=3)).strftime("%Y%m%d")
        else:
            begin = today_str

        _set_epg_status(
            progress=f"[{i+1}/{total_channels}] {channel_name}",
            current_channel=channel_name,
            done=i + 1,
        )

        programs, error = _fetch_schedule(code, begin, tomorrow_str)
        if error:
            # 网络请求失败 → 进入重试队列
            failed_channels.append((code, info, begin))
            logger.debug("[EPG Sync] %s: 请求失败 (%s)", channel_name, error)
        elif programs:
            count = _upsert_programs(conn, programs, channel_id, channel_name, epg_chid, sync_time)
            total_programs += count
            ok_channels += 1
            synced_epg_ids.add(epg_chid)  # 标记此 EPG ID 已覆盖
            logger.debug("[EPG Sync] %s: %d 条节目 (backTime=%d)", channel_name, count, back_time)
        else:
            # 接口正常返回但无数据（广播、4K、测试频道等）
            no_data_channels += 1

        time.sleep(_REQUEST_INTERVAL)

    logger.info("[EPG Sync] 第一轮: %d 有数据, %d 无EPG, %d EPG共享跳过, %d 请求失败",
                ok_channels, no_data_channels, skipped_shared, len(failed_channels))

    # 第二轮：只重试网络请求失败的频道
    if failed_channels:
        for retry_round in range(_MAX_BATCH_RETRIES):
            if not failed_channels:
                break
            logger.info("[EPG Sync] 第 2 轮重试 %d 个失败频道 (第 %d/%d 次)",
                        len(failed_channels), retry_round + 1, _MAX_BATCH_RETRIES)
            still_failed = []
            for code, info, begin in failed_channels:
                channel_name = info["name"]
                channel_id = info["channel_id"]
                back_time = info["backTime"]
                epg_chid = tvg_lookup.get(channel_id) or _normalize_epg(channel_name)

                time.sleep(_RETRY_BATCH_INTERVAL)
                programs, error = _fetch_schedule(code, begin, tomorrow_str)
                if error:
                    still_failed.append((code, info, begin))
                elif programs:
                    count = _upsert_programs(conn, programs, channel_id, channel_name, epg_chid, sync_time)
                    total_programs += count
                    ok_channels += 1
                    logger.info("[EPG Sync] 重试成功: %s (%d 条)", channel_name, count)
                else:
                    no_data_channels += 1  # 重试后发现确实无数据
            failed_channels = still_failed

        if failed_channels:
            names = [info["name"] for _, info, _ in failed_channels]
            logger.warning("[EPG Sync] 最终网络失败 %d 个频道: %s", len(failed_channels), ", ".join(names))

    # Step 4: 清理过期
    _clean_expired(conn)
    conn.close()

    _set_epg_status(
        running=False,
        progress="同步完成",
        last_sync_time=datetime.now().isoformat(),
        done=0, total=0,
        channel_count=ok_channels,
        program_count=total_programs,
    )

    logger.info("[EPG Sync] 完成: %d 频道有数据 (%d 条节目), %d 无EPG, %d EPG共享跳过, %d 网络失败",
                ok_channels, total_programs, no_data_channels, skipped_shared, len(failed_channels))
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
