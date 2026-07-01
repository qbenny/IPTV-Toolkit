"""
播放地址解析模块 - 处理 contentCode -> vod_id -> play_url 转换链路。
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from src.utils.logger import logger

# 模拟器实例引用（在 main.py 启动时注入）
_simulator = None


def set_simulator(sim):
    """设置全局模拟器实例（在 main.py 启动时调用）。"""
    global _simulator
    _simulator = sim


def get_simulator():
    """获取全局模拟器实例。"""
    return _simulator


async def play_redirect(request: Request, vod_id: str = None, url: str = None, vod_id_path: str = None):
    """播放地址解析接口。

    支持多种参数传递方式：
        ?vod_id=xxx
        ?url=xxx
        /api/play/{vod_id}.ts
    """
    sim = get_simulator()

    if vod_id_path:
        vod_id = vod_id_path

    # 提取被 TVBox playUrl 前缀包装的参数
    if vod_id and "api/play" in vod_id:
        if "url=" in vod_id:
            url = vod_id.split("url=", 1)[1]
            vod_id = None
        elif "vod_id=" in vod_id:
            vod_id = vod_id.split("vod_id=", 1)[1]

    if url == "":
        url = None
    if vod_id == "":
        vod_id = None

    if not url and not vod_id:
        return JSONResponse(content={"error": "Missing vod_id or url parameter"}, status_code=400)

    # 直通播放 URL
    target_url = None
    if url:
        target_url = url
    elif vod_id and (vod_id.startswith("http://") or vod_id.startswith("https://") or vod_id.startswith("rtsp://")):
        target_url = vod_id

    if target_url:
        if target_url.startswith("rtsp://"):
            target_url = target_url.replace("rtsp://", "http://")
        logger.info("[Play] Passthrough play URL: %s", target_url)
        return JSONResponse(content={
            "parse": 0,
            "url": target_url,
            "header": {"User-Agent": "CTC-2k/1.0 EPG/3.0 STB"}
        })

    # 解析 EPG vod_id → play_url
    if vod_id and sim:
        try:
            from src.auth.heartbeat import ensure_authenticated
            ensure_authenticated(sim, lambda: None)
            media_url = sim.get_vod_play_url(vod_id)
            if media_url:
                target_url = media_url.split("?")[0]
        except Exception as e:
            logger.error("[Play] Error resolving play URL for vod_id %s: %s", vod_id, e)

    if target_url:
        if target_url.startswith("rtsp://"):
            target_url = target_url.replace("rtsp://", "http://")
        logger.info("[Play] Resolved EPG play URL: %s", target_url)
        return JSONResponse(content={
            "parse": 0,
            "url": target_url,
            "header": {"User-Agent": sim.config.headers.get("User-Agent", "CTC-2k/1.0 EPG/3.0 STB")}
        })

    return JSONResponse(content={"error": "Play URL resolution failed"}, status_code=404)
