"""
凤凰卫视节目单 Provider — phtv.ifeng.com。

数据来自凤凰官网的 periodList JSON 接口（边缘 CDN 节点，由官网 HTML 的
allData.__nd__ 动态给出）。接口免登录，按日期区间返回各频道的节目
（{time:"HH:MM", title} 列表），结束时间相邻推算。

频道码（源自官网 JS）：
  phtvChinese = 中文台（凤凰中文）
  phtvNews    = 资讯台（凤凰资讯）
  phtvHK      = 香港台（凤凰香港）

凤凰频道 VIS 无数据，故本 Provider 为这些频道的权威数据源，始终写库。
"""
import re
import time
from datetime import datetime, timedelta

import requests

from src.sync.epg_providers.base import (
    FetchResult, EPGProvider,
    parse_time_based_programs, _upsert_programs,
)
from src.sync.epg_status import _set_epg_status
from src.utils.logger import logger

# 官网 HTML 中 allData.__nd__ 提供的边缘节点宿主（兜底常量）
_KNOWN_HOST = "ne883dbn.ifeng.com"
_HOST_CACHE = None

# DB 频道名子串 -> 凤凰接口频道码（按优先级匹配）
_CHANNEL_CODE_MAP = (
    ("中文", "phtvChinese"),
    ("资讯", "phtvNews"),
    ("香港", "phtvHK"),
)


class PhoenixProvider(EPGProvider):
    name = "phoenix"
    description = "凤凰卫视官网节目单 (phtv.ifeng.com)"

    def __init__(self, sim=None):
        self._sim = sim

    # ---- 对外接口 ----
    def validate(self) -> bool:
        # 外部公开源，无需登录 / VIS；始终可尝试
        return True

    def fetch(self) -> FetchResult:
        from src.db.models import get_db_connection

        conn = get_db_connection()
        try:
            channels = self._load_phoenix_channels(conn)
        finally:
            conn.close()

        if not channels:
            logger.warning("[PhoenixProvider] 库内未找到凤凰频道（name 含'凤凰'），跳过")
            return FetchResult(self.name, [], {
                "skipped": "no phoenix channels",
                "channel_count": 0, "program_count": 0,
                "no_data": 0, "failed": 0,
            })

        today = datetime.now()
        frm = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        host = self._resolve_host()
        url = f"https://{host}/phtvperiodlist"
        params = {"from": frm, "to": to}
        logger.info("[PhoenixProvider] 拉取 %s (%s ~ %s)，命中 %d 个凤凰频道",
                    host, frm, to, len(channels))
        _set_epg_status(progress=f"同步凤凰节目单 ({len(channels)} 频道)...")

        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.error("[PhoenixProvider] 请求 periodList 失败: %s", e)
            return FetchResult(self.name, [], {
                "error": str(e),
                "channel_count": 0, "program_count": 0,
                "no_data": 0, "failed": len(channels),
            })

        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            logger.warning("[PhoenixProvider] periodList 无数据返回: %s", payload)
            return FetchResult(self.name, [], {
                "error": "empty data",
                "channel_count": 0, "program_count": 0,
                "no_data": len(channels), "failed": 0,
            })

        sync_time = int(time.time())
        stats = {"channel_count": 0, "program_count": 0, "no_data": 0, "failed": 0}
        all_programs = []

        for ch in channels:
            api_code = ch["api_code"]
            db_info = {
                "channel_id": ch["channel_id"],
                "name": ch["name"],
                "tvg_id": ch["tvg_id"],
            }
            progs = []
            for date_str, day_channels in data.items():
                if not isinstance(day_channels, dict):
                    continue
                items = day_channels.get(api_code)
                if items:
                    progs.extend(parse_time_based_programs(
                        date_str, items, db_info, self.name))
            if progs:
                all_programs.extend(progs)
                stats["channel_count"] += 1
            else:
                stats["no_data"] += 1
                logger.info("[PhoenixProvider] %s 无节目数据", ch["name"])

        if all_programs:
            wconn = get_db_connection()
            try:
                stats["program_count"] = _upsert_programs(wconn, all_programs, sync_time)
            finally:
                wconn.close()

        logger.info("[PhoenixProvider] 完成: %d 频道有数据, %d 条节目, %d 无EPG",
                    stats["channel_count"], stats["program_count"], stats["no_data"])
        return FetchResult(self.name, [], stats)

    # ---- 内部 ----
    @staticmethod
    def _headers():
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://phtv.ifeng.com/programme",
        }

    def _resolve_host(self):
        """从官网 HTML 动态解析边缘节点宿主（CDN 节点可能轮换），失败用常量兜底。"""
        global _HOST_CACHE
        if _HOST_CACHE:
            return _HOST_CACHE
        try:
            r = requests.get("https://phtv.ifeng.com/programme",
                             headers=self._headers(), timeout=15)
            m = re.search(r'__nd__"\s*:\s*"([^"]+)"', r.text)
            if m and m.group(1):
                _HOST_CACHE = m.group(1)
                return _HOST_CACHE
        except Exception as e:
            logger.warning("[PhoenixProvider] 解析边缘节点失败，用兜底宿主: %s", e)
        _HOST_CACHE = _KNOWN_HOST
        return _HOST_CACHE

    @staticmethod
    def _load_phoenix_channels(conn) -> list:
        """读 live_channels 中启用的凤凰频道，并映射到接口频道码。"""
        c = conn.cursor()
        c.execute(
            "SELECT channel_id, name, tvg_id FROM live_channels "
            "WHERE is_enabled = 1 AND name LIKE '%凤凰%'"
        )
        result = []
        for row in c.fetchall():
            name = row["name"] or ""
            api_code = None
            for sub, code in _CHANNEL_CODE_MAP:
                if sub in name:
                    api_code = code
                    break
            if not api_code:
                logger.warning("[PhoenixProvider] 频道 '%s' 无法匹配凤凰接口码，跳过", name)
                continue
            result.append({
                "channel_id": str(row["channel_id"]),
                "name": name,
                "tvg_id": (row["tvg_id"] or "").strip(),
                "api_code": api_code,
            })
        return result
