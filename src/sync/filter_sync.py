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


def sync_filter_data(simulator, type_name: str, orderby: int = 2, max_pages: int = None) -> int:
    """分页拉取 filter.json 数据并写入数据库。

    Args:
        simulator: STBSimulator 实例（需已登录）
        type_name: 内容类型名称（电视剧/电影/综艺/动漫/少儿）
        orderby: 排序方式（2=评分降序）
        max_pages: 最大拉取页数（None=全部）

    Returns:
        成功同步的条目数
    """
    vis_domain = simulator.state.vis_base_url
    if not vis_domain:
        logger.error("[Sync] VIS 服务器地址未解析，跳过同步 %s", type_name)
        return 0

    page = 0
    size = 50
    sync_time = int(time.time())
    total_synced = 0

    while True:
        params = {
            "type": type_name,
            "size": size,
            "pageindex": page,
            "orderby": orderby,
            "userId": simulator.config.user_id,
        }

        try:
            url = f"{vis_domain}api/search/filter.json"
            res = requests.get(url, params=params, headers=VIS_HEADERS, timeout=15)
            if res.status_code != 200:
                logger.warning("[Sync] 同步 %s 第 %d 页失败: HTTP %d", type_name, page + 1, res.status_code)
                break

            data = res.json()
            items = data.get("resultSet", [])
            if not items:
                break

            # 写入数据库
            count = bulk_upsert_items(items, type_name)
            total_synced += count
            logger.info("[Sync] %s: 第 %d 页，写入 %d 条", type_name, page + 1, count)

            # 更新进度
            _set_sync_status(
                progress=f"{type_name} 第 {page + 1} 页 ({total_synced} 条)",
                current_type=type_name,
                done=total_synced,
            )

            # 检查是否还有下一页
            page_info = data.get("pageInfo", {})
            if max_pages and page + 1 >= max_pages:
                break
            if page + 1 >= page_info.get("pageCount", 0):
                break

            page += 1
            time.sleep(1)  # 避免请求过快

        except Exception as e:
            logger.error("[Sync] 同步 %s 异常: %s", type_name, e)
            _set_sync_status(last_error=str(e))
            break

    logger.info(">>> [Sync] %s 同步完成，共 %d 条", type_name, total_synced)
    return total_synced


def full_sync(simulator) -> dict:
    """全量同步所有类型的 filter.json 数据到 SQLite。

    Args:
        simulator: STBSimulator 实例（需已登录）

    Returns:
        {"type_name": count, ...}
    """
    types = ["电视剧", "电影", "综艺", "动漫", "少儿"]
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
    for i, t in enumerate(types):
        _set_sync_status(current_type=t)
        count = sync_filter_data(simulator, t)
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
