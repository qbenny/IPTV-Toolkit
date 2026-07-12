"""
EPG Provider 插件基类与共享工具。

- Program / FetchResult：统一的数据结构，所有 Provider 产出此格式。
- EPGProvider：数据源抽象基类。
- parse_time_based_programs / is_channel_covered：凤凰 / 电视猫等基于
  {HH:MM, title} 列表的 Provider 共用的转换与覆盖检查工具。
- _upsert_programs / _clean_expired：统一写入 / 清理逻辑，供调度器与各
  Provider 复用（放在 base 层可避免 epg_sync ↔ provider 循环 import）。
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List

from src.utils.logger import logger


@dataclass
class Program:
    """统一的节目数据结构，所有 Provider 返回此格式。"""
    channel_id: str          # live_channels.channel_id（用于 DB 关联 / 去重）
    channel_name: str        # 频道显示名
    epg_channel_id: str      # EPG 节目单频道 ID（tvg_id），用于 XMLTV 输出
    title: str
    start_time: str          # "YYYYMMDDHHMMSS"
    end_time: str            # "YYYYMMDDHHMMSS"
    raw_data: dict = field(default_factory=dict)  # 原始数据（溯源）


@dataclass
class FetchResult:
    """Provider.fetch() 的返回结构。"""
    provider: str
    programs: List[Program]
    stats: dict


class EPGProvider(ABC):
    """EPG 数据源抽象基类。

    每个 Provider 负责：从特定数据源获取节目、转换为统一的 Program 格式、
    返回 FetchResult。批量化 Provider 可直接写库（见 _upsert_programs），
    此时 FetchResult.programs 可为空，仅用 stats 汇报计数。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 唯一标识，如 'vis', 'phoenix'。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """人类可读的描述。"""
        ...

    @abstractmethod
    def validate(self) -> bool:
        """预检查：Provider 是否具备执行条件（如 VIS 基址已解析）。"""
        ...

    @abstractmethod
    def fetch(self) -> FetchResult:
        """执行数据抓取，返回统一格式的节目列表与统计。"""
        ...

    def __str__(self):
        return f"{self.name}({self.description})"


# ---- 共享工具 ----

def parse_time_based_programs(date_str: str, time_title_pairs: List[dict],
                              db_info: dict, provider_name: str) -> List[Program]:
    """将 {time:"HH:MM", title:"..."} 列表转为 Program 列表（结束时间相邻推算）。

    Args:
        date_str: "YYYY-MM-DD"
        time_title_pairs: [{"time": "06:00", "title": "有盼头"}, ...] 按时间升序
        db_info: {"channel_id", "name", "tvg_id"}
        provider_name: 来源标识，写入 raw_data
    """
    programs: List[Program] = []
    n = len(time_title_pairs)
    for i, item in enumerate(time_title_pairs):
        t = (item.get("time") or "").strip()
        title = (item.get("title") or "").strip()
        if not t or not title or ":" not in t:
            continue
        hh, mm = t.split(":")
        start = f"{date_str.replace('-', '')}{hh}{mm}00"
        if i + 1 < n:
            nt = (time_title_pairs[i + 1].get("time") or "").strip()
            if ":" in nt:
                nhh, nmm = nt.split(":")
                end = f"{date_str.replace('-', '')}{nhh}{nmm}00"
            else:
                end = f"{date_str.replace('-', '')}235959"
        else:
            end = f"{date_str.replace('-', '')}235959"
        programs.append(Program(
            channel_id=str(db_info.get("channel_id", "")),
            channel_name=db_info.get("name", ""),
            epg_channel_id=db_info.get("tvg_id") or db_info.get("name", ""),
            title=title,
            start_time=start,
            end_time=end,
            raw_data={"provider": provider_name, "source": provider_name},
        ))
    return programs


def is_channel_covered(conn, channel_id: str, check_days: int = 3,
                       threshold_per_day: int = 8) -> bool:
    """判断某频道近期是否已有足够节目数据（补充 Provider 据此跳过已覆盖频道）。

    Args:
        conn: DB 连接
        channel_id: live_channels.channel_id
        check_days: 检查最近 N 天
        threshold_per_day: 单日节目数达到此值视为已覆盖
    """
    cutoff = (datetime.now() - timedelta(days=check_days)).strftime("%Y-%m-%d 00:00:00")
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) AS cnt FROM epg_programs "
        "WHERE channel_id = ? AND start_time >= ?",
        (str(channel_id), cutoff),
    )
    row = c.fetchone()
    cnt = row["cnt"] if row else 0
    return cnt >= threshold_per_day * check_days


# ---- 统一写入 / 清理 ----

def _upsert_programs(conn, programs: List[Program], sync_time: int) -> int:
    """批量 UPSERT Program 列表到 epg_programs。返回写入条数。"""
    if not programs:
        return 0
    c = conn.cursor()
    count = 0
    for prog in programs:
        title = prog.title
        start = prog.start_time
        end = prog.end_time
        if not title or len(start) < 14 or len(end) < 14:
            continue
        program_date = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]} {start[8:10]}:{start[10:12]}:{start[12:14]}"
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]} {end[8:10]}:{end[10:12]}:{end[12:14]}"
        raw_json = json.dumps(prog.raw_data, ensure_ascii=False)
        try:
            c.execute("""
                INSERT INTO epg_programs
                    (channel_id, channel_name, title, start_time, end_time,
                     program_date, epg_channel_id, raw_data_json, synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, start_time, title) DO UPDATE SET
                    end_time=excluded.end_time,
                    raw_data_json=excluded.raw_data_json,
                    synced_at=excluded.synced_at
            """, (prog.channel_id, prog.channel_name, title, start_fmt, end_fmt,
                  program_date, prog.epg_channel_id, raw_json, sync_time, sync_time))
            count += 1
        except Exception as e:
            logger.warning("[EPG] 写入节目失败 (%s/%s): %s", prog.channel_name, title, e)
    conn.commit()
    return count


def _clean_expired(conn, keep_days: int = 9):
    """清理超过保留天数的过期节目数据。"""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d 00:00:00")
    c = conn.cursor()
    c.execute("DELETE FROM epg_programs WHERE end_time < ?", (cutoff,))
    deleted = c.rowcount
    conn.commit()
    if deleted:
        logger.info("[EPG] 清理 %d 条超过 %d 天的过期节目", deleted, keep_days)
