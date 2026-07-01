"""
数据同步模块 - filter.json -> SQLite 同步逻辑。
从 VIS API 分页拉取全量数据并写入本地数据库。
"""
import threading
import time

import requests

from src.db.crud import bulk_upsert_items, clean_old_data
from src.utils.logger import logger

# VIS API 通用请求 headers
VIS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.0.3; zh-cn)",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# 请求重试配置
_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 3  # 秒，指数退避基数


def _fetch_with_retry(url: str, params: dict, timeout: int = 15) -> requests.Response:
    """带指数退避重试的 GET 请求。

    Args:
        url: 请求地址
        params: 查询参数
        timeout: 单次超时秒数

    Returns:
        requests.Response

    Raises:
        requests.RequestException: 重试耗尽后抛出最后一次异常
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return requests.get(url, params=params, headers=VIS_HEADERS, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("[Sync] 请求失败 (尝试 %d/%d)，%d 秒后重试: %s",
                               attempt + 1, _MAX_RETRIES, delay, e)
                time.sleep(delay)
    raise last_exc

# 同步状态（供 Web UI 查询）
sync_status = {
    "running": False,
    "progress": "",
    "current_type": "",
    "done": 0,
    "total": 0,
    "last_sync_time": None,
    "last_error": None,
    "results": {},
}


def _set_sync_status(**kwargs):
    """线程安全地更新同步状态。"""
    global sync_status
    for k, v in kwargs.items():
        sync_status[k] = v


def sync_filter_data(simulator, type_name: str, sync_time: int, orderby: int = 2) -> int:
    """单次请求拉取 filter.json 全量数据并写入数据库。

    Args:
        simulator: STBSimulator 实例（需已登录）
        type_name: 内容类型名称（电视剧/电影/综艺/动漫/少儿）
        sync_time: 本次同步的统一时间戳，由 full_sync 统一传入
        orderby: 排序方式（2=评分降序）

    Returns:
        成功同步的条目数
    """
    vis_domain = simulator.state.vis_base_url
    if not vis_domain:
        logger.error("[Sync] VIS 服务器地址未解析，跳过同步 %s", type_name)
        return 0

    logger.info("[Sync] 开始同步 %s (sync_time=%d)", type_name, sync_time)

    params = {
        "type": type_name,
        "size": 50000,  # 一次拉取全量，API 分页已废弃
        "pageindex": 0,
        "orderby": orderby,
        "userId": simulator.config.user_id,
    }

    try:
        url = f"{vis_domain}api/search/filter.json"
        res = _fetch_with_retry(url, params)
        if res.status_code != 200:
            logger.warning("[Sync] 同步 %s 失败: HTTP %d", type_name, res.status_code)
            return 0

        data = res.json()
        items = data.get("resultSet", [])
        if not items:
            logger.warning("[Sync] %s 返回空数据", type_name)
            return 0

        count = bulk_upsert_items(items, type_name, sync_time)
        logger.info("[Sync] %s: 写入 %d 条", type_name, count)

        _set_sync_status(
            progress=f"{type_name} ({count} 条)",
            current_type=type_name,
            done=count,
        )

    except Exception as e:
        logger.error("[Sync] 同步 %s 异常: %s", type_name, e)
        _set_sync_status(last_error=str(e))
        return 0

    logger.info(">>> [Sync] %s 同步完成，共 %d 条", type_name, count)
    return count


def full_sync(simulator) -> dict:
    """全量同步所有类型的 filter.json 数据到 SQLite。

    Args:
        simulator: STBSimulator 实例（需已登录）

    Returns:
        {"type_name": count, ...}
    """
    # VIS API 支持的类型（可根据需要增删）：
    #  电影(001)  电视剧(002)  新闻(003)  少儿(004)  综艺(005)  纪录(007)  戏曲(016)  动漫(type113)
    types = ["电视剧", "电影", "综艺", "动漫", "少儿", "纪录"]
    # 未启用的类型（有数据，按需加入上方列表即可）："新闻", "戏曲"
    sync_time = int(time.time())

    _set_sync_status(
        running=True,
        progress="开始全量同步...",
        done=0,
        total=len(types),
        last_error=None,
        current_type="",
        results={},
    )

    results = {}
    for t in types:
        _set_sync_status(current_type=t)
        count = sync_filter_data(simulator, t, sync_time)
        results[t] = count

    # 所有类型同步完成后，清理过期数据
    _set_sync_status(progress="清理旧数据...", current_type="清理中")
    clean_old_data(sync_time)

    _set_sync_status(
        running=False,
        progress="同步完成",
        last_sync_time=sync_time,
        current_type="",
        done=0,
        total=0,
        results=results,
    )

    logger.info(">>> [Sync] 全量同步完成: %s", results)
    return results


def start_sync_background(simulator):
    """在后台线程中启动同步任务。

    Args:
        simulator: STBSimulator 实例
    """
    global sync_status
    if sync_status["running"]:
        logger.warning("[Sync] 同步任务已在运行中，跳过")
        return

    def _run():
        try:
            full_sync(simulator)
        except Exception as e:
            logger.error("[Sync] 同步任务异常: %s", e, exc_info=True)
            _set_sync_status(running=False, last_error=str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
