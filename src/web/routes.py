"""
Web UI 路由模块 - STB 配置、同步状态、日志查看等 API 端点。
"""
import json
import os
import threading
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse

from src.utils.logger import logger, LOG_FILE
from src.db.crud import get_stats as get_db_stats

# 模拟器实例（在 main.py 启动时注入）
_simulator = None
_login_func = None

router = APIRouter()


def set_simulator(sim):
    """设置全局模拟器实例。"""
    global _simulator
    _simulator = sim


def set_login_func(func):
    """设置全局登录函数。"""
    global _login_func
    _login_func = func


def _load_stb_config() -> dict:
    """从文件加载 STB 配置。"""
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    config_path = os.path.join(data_dir, "stb_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("读取 stb_config.json 失败: %s", e)
    return {
        "user_id": "",
        "stb_id": "",
        "mac_address": "",
        "ip_address": "",
        "base_url": "",
        "des_key": ""
    }


def _save_stb_config(config_in: dict):
    """保存 STB 配置到文件。"""
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    config_path = os.path.join(data_dir, "stb_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_in, f, ensure_ascii=False, indent=2)


# ---- 路由 ----

@router.get("/settings")
async def get_settings():
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "static", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@router.get("/api/stb-config")
async def get_stb_config():
    return _load_stb_config()


@router.get("/api/sim-status")
async def get_sim_status():
    """返回 STB 模拟器认证状态。"""
    sim = _simulator
    if sim is None:
        return {"is_authenticated": False, "epg_base_url": None, "user_token": None}
    jsessionid = sim.state.session.cookies.get("JSESSIONID", None)
    return {
        "is_authenticated": sim.state.is_authenticated,
        "epg_base_url": sim.state.epg_base_url or None,
        "user_token": sim.state.user_token or None,
        "jsessionid": jsessionid,
        "ip_address": sim.config.ip_address,
    }


@router.post("/api/stb-config")
async def save_stb_config(config_in: dict):
    """保存 STB 配置并测试登录。"""
    try:
        _save_stb_config(config_in)

        # 重新初始化模拟器
        from src.auth.config import STBDeviceConfig
        from src.auth.simulator import STBSimulator

        sim = _simulator
        new_config = STBDeviceConfig(
            user_id=config_in.get("user_id", ""),
            stb_id=config_in.get("stb_id", ""),
            mac_address=config_in.get("mac_address", ""),
            ip_address=config_in.get("ip_address", ""),
            base_url=config_in.get("base_url", ""),
            des_key=config_in.get("des_key", "00000000"),
        )
        sim.config = new_config
        sim.state.is_authenticated = False

        # 自动测试登录
        if new_config.user_id and new_config.base_url and _login_func:
            login_success = _login_func()
            if login_success:
                return {"status": "success", "message": "配置保存成功，且模拟登录验证成功！", "ip": new_config.ip_address}
            else:
                return {"status": "warning", "message": "配置保存成功，但模拟登录失败，请检查参数或网络连通性。", "ip": new_config.ip_address}

        return {"status": "success", "message": "配置保存成功！核心参数为空，暂未运行登录测试。", "ip": new_config.ip_address}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"保存配置失败: {str(e)}"}, status_code=500)


@router.post("/api/sync/start")
async def trigger_sync():
    """触发后台同步任务。"""
    from src.sync.filter_sync import sync_status, start_sync_background

    if sync_status["running"]:
        return JSONResponse(content={
            "status": "already_running",
            "message": f"正在同步中… {sync_status['progress']}"
        })

    if _simulator is None or not _simulator.state.is_authenticated:
        return JSONResponse(content={"status": "error", "message": "模拟器未认证，请先配置并登录 STB"})

    start_sync_background(_simulator, _login_func)
    return {"status": "started", "message": "已开始同步数据，请稍候查看进度..."}


@router.get("/api/sync/status")
async def get_sync_status():
    """获取同步进度。"""
    from src.sync.filter_sync import sync_status
    return JSONResponse(content=sync_status)


@router.get("/api/sync/stats")
async def get_sync_stats():
    """获取数据库统计信息。"""
    try:
        stats = get_db_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/logs")
async def get_api_logs(lines: int = 200, level: str = "ALL"):
    """获取日志文件内容。"""
    try:
        if not os.path.exists(LOG_FILE):
            return []

        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        # 取最后 N 行
        recent = all_lines[-lines:]

        # 级别过滤
        if level != "ALL":
            level_order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            try:
                min_idx = level_order.index(level)
                filtered = []
                for line in recent:
                    for lvl in level_order[min_idx:]:
                        if f"[{lvl}]" in line:
                            filtered.append(line.strip())
                            break
                return filtered
            except ValueError:
                pass

        return [line.strip() for line in recent]
    except Exception as e:
        logger.error("读取日志失败: %s", e)
        return [f"读取日志失败: {e}"]


@router.post("/api/logs/clear")
async def clear_api_logs():
    """清空日志文件。"""
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
        return {"status": "success", "message": "日志已清空"}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)
