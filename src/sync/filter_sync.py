"""
数据同步模块 - filter.json -> SQLite 同步逻辑。
从 VIS API 分页拉取全量数据并写入本地数据库。
"""
import threading
import time

from src.db.crud import bulk_upsert_items, clean_old_data
from src.utils.helpers import fetch_with_retry
from src.utils.logger import logger

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
        res = fetch_with_retry(url, params, headers=simulator.config.headers, tag="Sync")
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
    # VIS API 支持的类型（可按需增删）：
    #  电影(001)  电视剧(002)  新闻(003)  少儿(004)  综艺(005)  体育(006)  纪录(007)  戏曲(016)  动漫(type113)  其他(type87)
    types = ["电视剧", "电影", "综艺", "动漫", "少儿", "纪录", "新闻", "体育", "戏曲", "其他"]
    # 注：新闻/体育/戏曲/其他为新增同步类型；子标签过滤器待全量同步后实测 contentBaseTags 再完善
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
