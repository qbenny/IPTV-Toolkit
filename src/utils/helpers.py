"""
工具函数模块 - 包含 EPG JSON 解析、IPTV IP 探测、带重试的 HTTP GET 等工具函数。
从 run_simulator.py 迁移而来。
"""
import ast
import json
import re
import subprocess
import sys
import time

import requests

from src.utils.logger import logger

_IPTV_IP_DETECT_URL = "http://192.168.1.1/iptv_ip.txt"

# HTTP 重试默认配置
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY_BASE = 3  # 秒，指数退避基数


def fetch_with_retry(
    url: str,
    params: dict = None,
    timeout: int = 15,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    headers: dict = None,
    retry_delay_base: int = _DEFAULT_RETRY_DELAY_BASE,
    tag: str = "HTTP",
) -> requests.Response:
    """带指数退避重试的 GET 请求。

    仅对网络层异常（requests.RequestException）重试；HTTP 状态码由调用方自行判断。

    Args:
        url: 请求地址
        params: 查询参数
        timeout: 单次超时秒数
        max_retries: 最大尝试次数
        headers: 请求头
        retry_delay_base: 指数退避基数（秒），第 n 次重试等待 base * 2**n
        tag: 日志标签（用于区分调用来源，如 "Sync" / "EPG Sync"）

    Returns:
        requests.Response

    Raises:
        requests.RequestException: 重试耗尽后抛出最后一次异常
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = retry_delay_base * (2 ** attempt)
                logger.warning("[%s] 请求失败 (尝试 %d/%d)，%d 秒后重试: %s",
                               tag, attempt + 1, max_retries, delay, e)
                time.sleep(delay)
    raise last_exc


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


def get_lan_ip() -> str:
    """探测本机局域网 IP（非 IPTV 专网的私有 IP）。

    优先返回非 10.x.x.x 网段的私有 IP（如 192.168.x.x），
    找不到则回退到 10.x.x.x，再找不到返回 "localhost"。

    Returns:
        IP 地址字符串，不会抛出异常
    """
    all_ips = []
    try:
        if sys.platform.startswith("win"):
            out = subprocess.check_output("ipconfig", shell=True).decode("gbk", errors="ignore")
            all_ips = re.findall(r"IPv4 地址[.\s]*:\s*([0-9.]+)", out)
            if not all_ips:
                all_ips = re.findall(r"IPv4 Address[.\s]*:\s*([0-9.]+)", out)
        else:
            out = subprocess.check_output("ip addr", shell=True).decode("utf-8", errors="ignore")
            all_ips = re.findall(r"inet\s+([0-9.]+)/", out)
    except Exception:
        pass

    # 优先选择非 10.x 的私有 IP（192.168.x.x 等）
    for ip in all_ips:
        ip = ip.strip()
        if ip and _is_private_ip(ip) and not ip.startswith("10."):
            return ip

    # 回退到 10.x.x.x
    for ip in all_ips:
        ip = ip.strip()
        if ip and ip.startswith("10."):
            return ip

    return "localhost"


def _is_private_ip(ip: str) -> bool:
    """判断是否为私有 IP 地址。"""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return False
