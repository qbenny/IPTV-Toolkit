"""
频道名称归一化工具模块。
提供两套归一化逻辑：
- normalize_epg(): 用于 tvg_id / tvg_name，去掉所有画质后缀和频道内容描述
- normalize_logo(): 用于 logo_url，保留 4K/8K 画质标识
"""

import re

# 画质后缀（按长度降序，避免短词误匹配）
_QUALITY_SUFFIXES_ALL = [
    "1080P", "720P",
    "超清", "高清", "标清", "极清",
    "FHD", "UHD", "HD", "SD",
    "4K", "8K",
]

# Logo 用画质后缀（不含 4K/8K，保留它们以匹配独立 Logo）
_QUALITY_SUFFIXES_LOGO = [
    "1080P", "720P",
    "超清", "高清", "标清", "极清",
    "FHD", "UHD", "HD", "SD",
]


def strip_suffixes(name: str, suffixes: list) -> str:
    """按顺序尝试去掉名称末尾的指定后缀（仅匹配一次）。"""
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def extract_cctv_number(name: str) -> str | None:
    """提取 CCTV 频道号，如 CCTV1、CCTV5+、CCTV4K。

    Returns:
        提取到的频道号，如 "CCTV1"；不匹配则返回 None
    """
    m = re.match(r"(CCTV[\d]+[\+]?)", name)
    return m.group(1) if m else None


def normalize_epg(name: str) -> str:
    """归一化频道名用于 EPG 匹配（tvg_id / tvg_name）。

    处理逻辑：
    1. 去掉所有画质后缀（含 4K/8K）
    2. CCTV 频道提取纯台号（CCTV1、CCTV5+ 等）

    Examples:
        normalize_epg("CCTV1综合高清") → "CCTV1"
        normalize_epg("CCTV5+高清")    → "CCTV5+"
        normalize_epg("浙江卫视高清")   → "浙江卫视"
        normalize_epg("北京卫视4K")     → "北京卫视"
    """
    name = name.strip()

    # Step 1: 去掉画质后缀
    name = strip_suffixes(name, _QUALITY_SUFFIXES_ALL)

    # Step 2: CCTV 频道提取纯台号
    cctv = extract_cctv_number(name)
    if cctv:
        return cctv

    return name.strip()


def normalize_logo(name: str) -> str:
    """归一化频道名用于 Logo 文件名匹配。

    与 normalize_epg 的区别：保留 4K/8K 后缀（因为 4K 台有独立 Logo）。

    Examples:
        normalize_logo("CCTV1综合高清") → "CCTV1"
        normalize_logo("北京卫视4K")      → "北京卫视4K"
        normalize_logo("浙江卫视高清")    → "浙江卫视"
    """
    name = name.strip()

    # Step 1: 去掉画质后缀（不含 4K/8K）
    name = strip_suffixes(name, _QUALITY_SUFFIXES_LOGO)

    # Step 2: CCTV 频道提取纯台号（CCTV 频道统一只用台号，不需要 4K 后缀）
    cctv = extract_cctv_number(name)
    if cctv:
        return cctv

    return name.strip()
