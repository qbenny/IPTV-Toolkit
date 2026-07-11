"""
数据同步模块 - filter.json -> SQLite 同步逻辑。
从 VIS API 分页拉取全量数据并写入本地数据库。
"""
import concurrent.futures
import threading
import time

from src.auth.heartbeat import ensure_authenticated
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
        t_net = time.perf_counter()
        res = fetch_with_retry(url, params, headers=simulator.config.headers, tag="Sync")
        if res.status_code != 200:
            logger.warning("[Sync] 同步 %s 失败: HTTP %d", type_name, res.status_code)
            return 0
        dt_net = time.perf_counter() - t_net

        t_parse = time.perf_counter()
        data = res.json()
        items = data.get("resultSet", [])
        if not items:
            logger.warning("[Sync] %s 返回空数据 (网络=%.2fs)", type_name, dt_net)
            return 0
        dt_parse = time.perf_counter() - t_parse

        t_write = time.perf_counter()
        count = bulk_upsert_items(items, type_name, sync_time)
        dt_write = time.perf_counter() - t_write
        logger.info("[Sync] %s: 写入 %d 条 | 网络=%.2fs 解析=%.2fs 写库=%.2fs",
                    type_name, count, dt_net, dt_parse, dt_write)

    except Exception as e:
        logger.error("[Sync] 同步 %s 异常: %s", type_name, e)
        _set_sync_status(last_error=str(e))
        return 0

    logger.info(">>> [Sync] %s 同步完成，共 %d 条", type_name, count)
    return count


def full_sync(simulator, login_func=None) -> dict:
    """全量同步所有类型的 filter.json 数据到 SQLite。

    各类型并发拉取（线程池），墙钟耗时由最慢单类决定；写库各自独立连接（WAL），
    并发安全。并发前仅做一次认证，避免 10 个线程各自触发登录。

    Args:
        simulator: STBSimulator 实例
        login_func: 登录函数（用于并发前确保已认证；为 None 时跳过）

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

    # 并发前确保已认证（单次；若已认证则 ensure_authenticated 直接返回，无额外开销）
    if login_func:
        try:
            ensure_authenticated(simulator, login_func)
        except Exception as e:
            logger.error("[Sync] 同步前认证失败: %s", e)
            _set_sync_status(running=False, last_error=str(e))
            return {}

    results = {}
    done_total = 0
    # 并发拉取 10 个类型：墙钟由最慢单类（电视剧）决定，5 路已足够且减轻服务器压力
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_type = {
            executor.submit(sync_filter_data, simulator, t, sync_time): t
            for t in types
        }
        for future in concurrent.futures.as_completed(future_to_type):
            t = future_to_type[future]
            try:
                count = future.result()
            except Exception as e:
                logger.error("[Sync] %s 同步异常: %s", t, e)
                count = 0
            results[t] = count
            done_total += count
            _set_sync_status(
                current_type=t,
                done=done_total,
                progress=f"{t} 完成 ({count} 条)",
            )

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


def start_sync_background(simulator, login_func=None):
    """在后台线程中启动同步任务。

    Args:
        simulator: STBSimulator 实例
        login_func: 登录函数（透传给 full_sync，用于并发前确保已认证）
    """
    global sync_status
    if sync_status["running"]:
        logger.warning("[Sync] 同步任务已在运行中，跳过")
        return

    def _run():
        try:
            full_sync(simulator, login_func)
        except Exception as e:
            logger.error("[Sync] 同步任务异常: %s", e, exc_info=True)
            _set_sync_status(running=False, last_error=str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
