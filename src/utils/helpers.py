"""
工具函数模块 - 包含 EPG JSON 解析、IPTV IP 探测等工具函数。
从 run_simulator.py 迁移而来。
"""
import ast
import json
import re
import subprocess
import sys

import requests

_IPTV_IP_DETECT_URL = "http://192.168.1.1/iptv_ip.txt"


def parse_epg_json(text: str) -> dict:
    """解析 EPG 服务器非标准 JSON 格式（例如单引号键值、被圆括号包裹等）。

    Args:
        text: 原始响应文本

    Returns:
        解析后的字典，失败返回空字典
    """
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        cleaned = text.strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1].strip()
        return ast.literal_eval(cleaned)
    except Exception:
        return {}


def get_iptv_local_ip() -> str:
    """真实 IP 模式下探测本机 IPTV 出网 IP。

    方法 1：扫描本地网卡接口，寻找 10.x.x.x 网段的 IPTV 专网 IP。
    方法 2：HTTP GET _IPTV_IP_DETECT_URL，读取响应体文本作为 IP。
    两种都失败则报错。

    Returns:
        IP 地址字符串

    Raises:
        RuntimeError: 无法探测到 IPTV 出网 IP
    """
    # 方法 1：扫描本地网卡
    ips = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.check_output("ipconfig", shell=True).decode("gbk", errors="ignore")
            ips = re.findall(r"IPv4 地址[.\s]*:\s*([0-9.]+)", out)
            if not ips:
                ips = re.findall(r"IPv4 Address[.\s]*:\s*([0-9.]+)", out)
        else:
            out = subprocess.check_output("ip addr", shell=True).decode("utf-8", errors="ignore")
            ips = re.findall(r"inet\s+([0-9.]+)/", out)
        for ip in ips:
            ip = ip.strip()
            if ip and ip.startswith("10."):
                return ip
    except Exception:
        pass

    # 方法 2：HTTP GET 固定 URL
    try:
        resp = requests.get(_IPTV_IP_DETECT_URL, timeout=5)
        resp.raise_for_status()
        ip = resp.text.strip()
        if ip:
            return ip
    except Exception:
        pass

    raise RuntimeError(
        "无法探测 IPTV 出网 IP：本地网卡未找到 10.x.x.x 网段，"
        f"且 {_IPTV_IP_DETECT_URL} 不可达"
    )
