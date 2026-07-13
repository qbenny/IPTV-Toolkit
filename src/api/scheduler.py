"""定时同步调度器状态与配置接口。

定时同步相关的「配置读写 + 启停副作用」内聚在此，避免让 live.py 去调 scheduler 造成循环依赖。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.sync.scheduler_engine import get_scheduler_state, save_scheduler_config
from src.db.config_store import cfg_get_all

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

# 定时模块相关配置 key（GET /config 仅返回定时子集，避免泄露直播/VOD 配置）
_SCHEDULER_KEYS = (
    "scheduler_enabled", "live_sync_hour", "live_sync_enabled",
    "vod_sync_hour", "vod_sync_enabled", "epg_sync_hour", "epg_sync_enabled",
)


@router.get("/config")
async def get_scheduler_config():
    """获取定时同步配置（钟点 + 各分开关）。"""
    all_cfg = cfg_get_all("scheduler_config")
    return {k: all_cfg.get(k) for k in _SCHEDULER_KEYS}


@router.put("/config")
async def update_scheduler_config(new_configs: dict):
    """更新定时同步配置，并按总开关动态启停调度器。"""
    save_scheduler_config(new_configs)
    return {"status": "success"}


@router.get("/status")
async def scheduler_status():
    """获取定时同步调度器状态：各任务今日完成/重试/上次同步时间，及配置钟点。"""
    return JSONResponse(content=get_scheduler_state())
