"""EPG 同步状态共享模块。

为避免 epg_sync 调度器与各 Provider 子包之间的循环 import，
将同步状态字典与更新函数集中到此独立模块，供双方 import。
"""
from typing import Any

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
    "providers": {},
}


def _set_epg_status(**kwargs: Any) -> None:
    """线程安全地更新 EPG 同步状态。"""
    global epg_sync_status
    for k, v in kwargs.items():
        epg_sync_status[k] = v
