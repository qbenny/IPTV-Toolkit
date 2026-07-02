"""
IPTV-Toolkit 主入口 - 组装所有模块，启动 FastAPI 服务。
"""
import json
import os
import sys
import threading
import time

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from src.utils.logger import setup_logger, LOG_FILE, logger
from src.utils.helpers import get_lan_ip
from src.db.models import init_db
from src.db.live_crud import init_live_defaults
from src.auth.config import STBDeviceConfig
from src.auth.simulator import STBSimulator
from src.auth.heartbeat import start_heartbeat_thread

# ---- 配置加载 ----

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load_stb_config() -> dict:
    """从 data/stb_config.json 加载 STB 配置。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    config_path = os.path.join(DATA_DIR, "stb_config.json")
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


# ---- 初始化模块级对象 ----

setup_logger("IPTV-Toolkit")
init_db()
init_live_defaults()

config_data = load_stb_config()
config_stb = STBDeviceConfig(
    user_id=config_data.get("user_id", ""),
    stb_id=config_data.get("stb_id", ""),
    mac_address=config_data.get("mac_address", ""),
    ip_address=config_data.get("ip_address", ""),
    base_url=config_data.get("base_url", ""),
    des_key=config_data.get("des_key", "00000000"),
)
sim = STBSimulator(config=config_stb)

heartbeat_thread = None
heartbeat_lock = threading.Lock()


def login_sim() -> bool:
    """登录模拟器。"""
    if not sim.config.user_id or not sim.config.base_url:
        logger.warning(">>> [STB] 核心认证参数缺失，跳过自动登录")
        sim.state.is_authenticated = False
        return False
    try:
        logger.info(">>> [STB] 正在通过 STBSimulator.login() 登录...")
        success = sim.login()
        if success:
            logger.info(">>> [STB] 登录成功，EPG 网关: %s", sim.state.epg_base_url)
            _start_heartbeat()
            return True
        else:
            logger.warning(">>> [STB] 登录失败")
            sim.state.is_authenticated = False
            return False
    except Exception as e:
        logger.error(">>> [STB] 登录异常: %s", e)
        sim.state.is_authenticated = False
        return False


def _start_heartbeat():
    """启动心跳线程（线程安全）。"""
    global heartbeat_thread
    with heartbeat_lock:
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            return
        heartbeat_thread = start_heartbeat_thread(sim, login_sim)


# ---- 注入实例到各模块 ----

from src.api.tvbox import set_simulator as set_sim_tvbox
from src.api.play import set_simulator as set_sim_play
from src.api.live import set_simulator as set_sim_live
from src.web.routes import set_simulator as set_sim_web, set_login_func

set_sim_tvbox(sim)
set_sim_play(sim)
set_sim_live(sim)
set_sim_web(sim)
set_login_func(login_sim)


# ---- FastAPI 应用 ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    login_sim()
    yield
    # 关闭时


app = FastAPI(title="IPTV-Toolkit", lifespan=lifespan)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册 Web 路由
from src.web.routes import router as web_router
app.include_router(web_router)

# 注册直播频道路由
from src.api.live import router as live_router
app.include_router(live_router)

# TVBox 配置接口
from src.api.tvbox import get_tvbox_config
app.get("/zjvod")(get_tvbox_config)

# TVBox 协议接口
from src.api.tvbox import handle_tvbox_request
app.get("/api/vod")(handle_tvbox_request)

# 播放解析接口
from src.api.play import play_redirect
app.get("/api/play")(play_redirect)
app.get("/api/play.ts")(play_redirect)
app.get("/api/play/{vod_id_path}.ts")(play_redirect)


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/settings")


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: Exception):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/settings")
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


# ---- 启动 ----

if __name__ == "__main__":
    logger.info("========================================")
    host_ip = get_lan_ip()
    logger.info("IPTV-Toolkit 启动中...")
    logger.info("Web UI: http://%s:8880/settings", host_ip)
    logger.info("TVBox API: http://%s:8880/api/vod", host_ip)
    logger.info("========================================")
    uvicorn.run(app, host="0.0.0.0", port=8880, access_log=False)
