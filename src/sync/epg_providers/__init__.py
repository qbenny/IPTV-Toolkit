"""EPG Provider 插件包。

各 EPG 数据源（VIS / 凤凰 / 电视猫 …）实现统一接口 EPGProvider，
由 epg_sync 调度器顺序编排。
"""
from src.sync.epg_providers.base import (
    Program,
    FetchResult,
    EPGProvider,
    parse_time_based_programs,
    is_channel_covered,
)

__all__ = [
    "Program",
    "FetchResult",
    "EPGProvider",
    "parse_time_based_programs",
    "is_channel_covered",
]
