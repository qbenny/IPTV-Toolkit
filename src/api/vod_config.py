"""VOD 配置接口（第 4 步从 live_config 拆分到 vod_config）。

负责 VOD 同步相关配置读写（当前为过滤开关，后续可扩展分类设置等），不含模拟器依赖。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.db.config_store import cfg_get_all, cfg_bulk_set

router = APIRouter(prefix="/api/vod-config", tags=["vod-config"])

# VOD 领域配置 key（GET /config 仅返回此子集，避免泄露直播/定时配置）
_VOD_KEYS = ("low_quality_filter", "m3u8_filter")


@router.get("/config")
async def get_vod_config():
    """获取 VOD 过滤配置。"""
    all_cfg = cfg_get_all("vod_config")
    return {k: all_cfg.get(k) for k in _VOD_KEYS}


@router.put("/config")
async def update_vod_config(new_configs: dict):
    """更新 VOD 过滤配置（落到 vod_config）。"""
    cfg_bulk_set(new_configs, "vod_config")
    return {"status": "success"}
