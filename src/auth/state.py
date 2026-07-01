"""
STB 运行状态类 - 保存生命周期中随交互改变的 Token、Cookie 和心跳时间戳。
从 run_simulator.py 迁移。
"""
import time
from typing import Optional

import requests


class STBRuntimeState:
    """动态运行状态类。"""

    def __init__(self):
        self.session: requests.Session = requests.Session()
        self.epg_base_url: str = ""                         # 重定向后的真正 EPG 主机地址
        self.user_token: Optional[str] = None               # 验证成功后的正式通行 Token
        self.encrypt_token: Optional[str] = None            # 临时 EncryptToken
        self.is_authenticated: bool = False                 # 认证通过标志

        # 心跳控制
        self.heartbeat_interval: int = 600                  # 心跳间隔（秒）
        self.last_heartbeat_time: float = 0.0
        self.heartbeat_fail_count: int = 0

        # VIS 相关
        self.vis_base_url: Optional[str] = None             # VIS VOD 服务器地址
        self.operator: Optional[str] = None                 # 运营商: "telecom" 或 "unicom"

    def update_heartbeat_timer(self):
        """心跳成功后更新计时器。"""
        self.last_heartbeat_time = time.time()
        self.heartbeat_fail_count = 0

    def clear_auth_state(self):
        """清除认证状态。"""
        self.encrypt_token = None
        self.user_token = None
        self.is_authenticated = False
        self.session.cookies.clear()
