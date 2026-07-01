"""
心跳线程管理模块 - 从 vod-api.py 迁移。
"""
import threading
import time

from src.utils.logger import logger


def start_heartbeat_thread(simulator, login_func) -> threading.Thread:
    """启动心跳后台线程。

    Args:
        simulator: STBSimulator 实例
        login_func: 登录函数（用于 Token 失效后重登录）

    Returns:
        心跳线程实例
    """

    def run_heartbeat():
        logger.info(">>> [Heartbeat Thread] Started.")
        while True:
            try:
                if simulator.state.is_authenticated:
                    simulator.keep_alive()
                else:
                    logger.info(">>> [Heartbeat Thread] Auth state invalid, attempting re-login...")
                    login_func()
            except Exception as e:
                logger.error(f">>> [Heartbeat Thread] Error: {e}")
            time.sleep(5)

    t = threading.Thread(target=run_heartbeat, daemon=True)
    t.start()
    return t


def ensure_authenticated(simulator, login_func):
    """确保模拟器已认证。如果未认证则自动登录。"""
    if not simulator.state.is_authenticated:
        login_func()
