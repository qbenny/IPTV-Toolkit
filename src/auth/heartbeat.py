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
        import time as t_mod
        while True:
            try:
                current_time = t_mod.time()
                # 仅在最近 3 小时内有用户活跃（请求）时才进行心跳保活与失效重登录
                if current_time - simulator.state.last_active_time < 10800:
                    if simulator.state.is_authenticated:
                        simulator.keep_alive()
                    else:
                        logger.info(">>> [Heartbeat Thread] Auth state invalid, attempting re-login...")
                        login_func()
                else:
                    # 超过 3 小时无活跃，若当前依然在线，则主动释放会话进入智能休眠状态
                    if simulator.state.is_authenticated:
                        logger.info("已连续 3 小时无客户端请求，机顶盒进入智能休眠状态，释放会话 Token。")
                        simulator.state.clear_auth_state()
            except Exception as e:
                logger.error(f">>> [Heartbeat Thread] Error: {e}")
            t_mod.sleep(5)

    t = threading.Thread(target=run_heartbeat, daemon=True)
    t.start()
    return t


def ensure_authenticated(simulator, login_func):
    """确保模拟器已认证。如果未认证则自动登录。"""
    # 只要有任何接口请求，即视为活跃
    simulator.state.update_activity()
    if not simulator.state.is_authenticated:
        login_func()
