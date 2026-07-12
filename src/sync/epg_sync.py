"""
EPG 数据同步调度器 — 编排所有 EPGProvider，统一写入 epg_programs。

VIS 为主源；凤凰 / 电视猫等补充 Provider 仅补齐 VIS 未覆盖的频道
（电视猫在内部通过 _is_covered_by_vis() 跳过 VIS 已权威覆盖的频道）。
"""
import threading
import time

from src.db.models import get_db_connection
from src.sync.epg_providers.base import _clean_expired
from src.sync.epg_providers.phoenix_provider import PhoenixProvider
from src.sync.epg_providers.tvmao_provider import TvmaoProvider
from src.sync.epg_providers.vis_provider import VisProvider
from src.sync.epg_status import epg_sync_status, _set_epg_status
from src.utils.logger import logger


def _get_providers(sim) -> list:
    """构建当前可用的 Provider 列表（顺序执行，VIS 优先）。"""
    providers = []
    vis = VisProvider(sim)
    if vis.validate():
        providers.append(vis)
    # 补充源：凤凰官网（VIS 无数据的凤凰频道）——始终尝试
    phoenix = PhoenixProvider(sim)
    if phoenix.validate():
        providers.append(phoenix)
    # 补充源：电视猫（补齐 VIS 未覆盖的属地/卫视频道）
    tvmao = TvmaoProvider(sim)
    if tvmao.validate():
        providers.append(tvmao)
    return providers


def full_sync(sim) -> dict:
    """全量同步 EPG 数据（多 Provider 顺序编排）。

    1. 构建 Provider 列表（VIS 优先，补充 Provider 仅补缺口）
    2. 逐个调用 provider.fetch()（各自写库）
    3. 清理过期数据
    4. 汇总统计到 epg_sync_status

    Returns:
        {"channel_count": int, "program_count": int}
    """
    _set_epg_status(
        running=True,
        progress="初始化 Provider...",
        done=0, total=0,
        last_error=None,
        channel_count=0, program_count=0,
        providers={},
    )

    conn = get_db_connection()
    providers = _get_providers(sim)
    if not providers:
        conn.close()
        _set_epg_status(running=False, last_error="无可用 EPG Provider（VIS 未就绪）")
        return {"channel_count": 0, "program_count": 0}

    total_programs = 0
    total_channels = 0
    provider_results = {}

    for provider in providers:
        _set_epg_status(progress=f"正在同步: {provider.description}")
        try:
            result = provider.fetch()
            provider_results[provider.name] = result.stats
            total_programs += result.stats.get("program_count", 0)
            total_channels += result.stats.get("channel_count", 0)
        except Exception as e:
            logger.error("[EPG Sync] Provider '%s' 异常: %s", provider.name, e, exc_info=True)
            provider_results[provider.name] = {"error": str(e)}

    _clean_expired(conn)
    conn.close()

    _set_epg_status(
        running=False,
        progress="同步完成",
        last_sync_time=int(time.time()),
        done=0, total=0,
        channel_count=total_channels,
        program_count=total_programs,
        providers=provider_results,
    )

    logger.info("[EPG Sync] 完成: %d 频道有数据 (%d 条节目), Providers=%s",
                total_channels, total_programs, provider_results)
    return {"channel_count": total_channels, "program_count": total_programs}


def start_epg_sync(sim):
    """在后台线程中启动 EPG 同步任务。"""
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
