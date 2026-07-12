"""
VIS schedules API Provider — 浙江电信 VIS 节目单。

将原 epg_sync.py 中的 VIS 同步逻辑整体迁移至此，行为保持一致：
- 免登录（仅用 vis_base_url + 请求头）
- 统一窗口：过去 7 天 ~ 明天
- 线程池并发逐频道拉取，失败重试两轮
- HD/SD/4K 兄弟频道共享同一 tvg_id（epg_channel_id）
"""
import concurrent.futures
import time
from datetime import datetime, timedelta

import requests

from src.sync.epg_providers.base import Program, FetchResult, EPGProvider, _upsert_programs
from src.sync.epg_status import _set_epg_status
from src.utils.helpers import fetch_with_retry
from src.utils.logger import logger


class VisProvider(EPGProvider):
    name = "vis"
    description = "浙江电信 VIS EPG 节目单 API"

    MAX_BATCH_RETRIES = 2
    MAX_WORKERS = 5

    def __init__(self, sim):
        self._sim = sim

    def validate(self) -> bool:
        # 免登录：仅要求 VIS 基址已解析（与旧 full_sync 的 guard 一致）
        return bool(self._sim.state.vis_base_url)

    def fetch(self) -> FetchResult:
        from src.db.models import get_db_connection

        sim = self._sim
        vis_base = sim.state.vis_base_url
        if not vis_base:
            logger.error("[VisProvider] VIS 服务器地址未解析，无法同步")
            return FetchResult(self.name, [], {"error": "VIS 服务器地址未解析"})

        conn = get_db_connection()
        sync_channels = self._load_sync_channels(conn)
        conn.close()
        if not sync_channels:
            logger.warning("[VisProvider] 库内无含 channel_code 的频道，跳过")
            return FetchResult(self.name, [], {"skipped": "no channels"})

        total_channels = len(sync_channels)
        logger.info("[VisProvider] %d 个频道待同步", total_channels)
        _set_epg_status(total=total_channels, progress="开始同步 VIS 节目数据...")

        today = datetime.now()
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y%m%d")
        begin = (today - timedelta(days=7)).strftime("%Y%m%d")
        sync_time = int(time.time())
        headers = sim.config.headers

        def _fetch_one(code, channel_id, info):
            epg_chid = info.get("tvg_id") or info["name"]
            programs, error = self._fetch_schedule(vis_base, code, begin, tomorrow_str, headers)
            if error:
                return ("failed", 0, info["name"])
            if not programs:
                return ("nodata", 0, info["name"])
            progs = [
                Program(
                    channel_id=channel_id,
                    channel_name=info["name"],
                    epg_channel_id=epg_chid,
                    title=p.get("title", ""),
                    start_time=p.get("startTime", ""),
                    end_time=p.get("endTime", ""),
                    raw_data={"provider": "vis", "source": "vis"},
                )
                for p in programs
            ]
            wconn = get_db_connection()
            try:
                count = _upsert_programs(wconn, progs, sync_time)
            finally:
                wconn.close()
            return ("ok", count, info["name"])

        def _drain(executor, task_list, count_progress=True):
            nonlocal done_count
            futures = {
                executor.submit(_fetch_one, info["code"], cid, info): (cid, info)
                for cid, info in task_list
            }
            remaining = []
            for future in concurrent.futures.as_completed(futures):
                cid, info = futures[future]
                if count_progress:
                    done_count += 1
                try:
                    status, count, name = future.result()
                except Exception as e:
                    logger.warning("[VisProvider] worker 异常 (%s): %s", info["name"], e)
                    status, count, name = "failed", 0, info["name"]
                _set_epg_status(
                    progress=f"[VIS {done_count}/{total_channels}] {name}"
                             + ("" if count_progress else " (重试)"),
                    current_channel=name,
                    done=done_count,
                )
                if status == "ok":
                    stats["ok"] += 1
                    stats["programs"] += count
                elif status == "nodata":
                    stats["nodata"] += 1
                elif status == "failed":
                    remaining.append((cid, info))
            return remaining

        stats = {"ok": 0, "nodata": 0, "programs": 0}
        done_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            failed = _drain(executor, list(sync_channels.items()))

        logger.info("[VisProvider] 第一轮: %d 有数据, %d 无EPG, %d 失败",
                    stats["ok"], stats["nodata"], len(failed))

        for _ in range(self.MAX_BATCH_RETRIES):
            if not failed:
                break
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                failed = _drain(executor, failed, count_progress=False)

        if failed:
            names = [info["name"] for _, info in failed]
            logger.warning("[VisProvider] 最终网络失败 %d 个频道: %s",
                           len(failed), ", ".join(names))

        return FetchResult(self.name, [], {
            "channel_count": stats["ok"],
            "program_count": stats["programs"],
            "no_data": stats["nodata"],
            "failed": len(failed),
        })

    @staticmethod
    def _fetch_schedule(vis_base: str, channel_code: str, begintime: str,
                         endtime: str, headers: dict = None):
        """拉取单个频道的时间段节目单（带重试）。返回 (resultSet_list, error)。"""
        url = f"{vis_base}api/schedules/{channel_code}.json"
        params = {"begintime": begintime, "endtime": endtime}
        try:
            res = fetch_with_retry(url, params=params, headers=headers, timeout=15, tag="EPG Sync")
        except requests.RequestException as e:
            logger.error("[VisProvider] 请求耗尽重试: %s", e)
            return [], str(e)
        if res.status_code == 200:
            return res.json().get("resultSet", []), None
        logger.warning("[VisProvider] schedules API HTTP %d for code=%s",
                       res.status_code, channel_code)
        return [], f"HTTP {res.status_code}"

    @staticmethod
    def _load_sync_channels(conn) -> dict:
        """读 live_channels 中待同步频道（source='server'、有 channel_code、启用）。"""
        c = conn.cursor()
        c.execute(
            "SELECT channel_id, channel_code, name, tvg_id FROM live_channels "
            "WHERE source = 'server' AND channel_code != '' AND is_enabled = 1"
        )
        channels = {}
        for row in c.fetchall():
            cid = str(row["channel_id"])
            channels[cid] = {
                "code": row["channel_code"],
                "name": row["name"] or "",
                "tvg_id": (row["tvg_id"] or "").strip(),
            }
        return channels
