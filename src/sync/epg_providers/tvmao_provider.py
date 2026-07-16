"""
电视猫节目单 Provider — tvmao.com。

电视猫是公开 EPG 站点，覆盖大量卫视与属地频道，可补齐 VIS 未覆盖的本地频道
（如宁波/温州/杭州各地频道）。其结构特点：

- 频道页：``/program/{code}``，叶子频道 code 形如 ``ZJTV1``、``NBTV-NBTV2``，
  带日期的请求 ``/program/{code}/{YYYY-MM-DD}`` 直接返回该日节目。
- 每个频道页 ``<meta name="description">`` 含完整别名列表
  （如 ``宁波电视台新闻综合频道又名：宁波新闻综合,宁波一套,宁波1套,...,NBTV1``），
  用于把本地频道名精确映射到电视猫 code。
- 频道树通过页面内 ``/program/...`` 链接相互连通，可 BFS 爬全量映射并缓存。

本 Provider 为**补充源**：仅给 VIS（及凤凰）未覆盖的频道补节目单，且对
"仅由电视猫/凤凰覆盖"的频道每次同步都刷新，避免数据老化后缺口重现。

匹配策略：本地频道归一化名（含别名）精确匹配；未命中再尝试"唯一包含"模糊兜底。
"""
import os
import re
import json
import time
import threading
import unicodedata
from datetime import datetime, timedelta
from queue import Queue, Empty

import concurrent.futures
import requests

from src.sync.epg_providers.base import (
    FetchResult, EPGProvider,
    parse_time_based_programs, _upsert_programs,
)
from src.sync.epg_status import _set_epg_status
from src.utils.logger import logger
from src.utils.normalize import normalize_epg

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data",
)
_MAP_PATH = os.path.join(_DATA_DIR, "tvmao_channels.json")
_MAP_TTL_DAYS = 7
_SLEEP_PER_REQ = 0.08  # 爬取/抓取时的礼貌延时（秒）
_MAX_CRAWL_PAGES = 10000     # 频道页预算：仅"频道页"计入预算，索引/分类页不占，确保覆盖上海/北京等排在后部的省份
_MAX_TOTAL_PAGES = 40000     # 安全上限：防止异常时无限爬取（含索引/分类页）
# 仅爬取以下"频道组/分类入口"，避免全量 34 省爆炸式爬取。
# 每个入口是电视猫的一个地区/分类目录，BFS 仅在该入口及其子频道范围内展开；
# _ALLOWED_PREFIXES 用于严格限制链接跟随，防止从组内入口跳到其它省份。
#   - 浙江 ZJTV / 上海 SHHAI / 北京 BTV / 央视 CCTV 走 /program/<CODE>
#   - 数字付费走 /program_digital/，卫视走 /program_satellite/
# 数字付费/卫视额外以用户给出的周页作为种子，确保即使目录页不存在也能展开。
_SEED_URLS = [
    "https://www.tvmao.com/program/ZJTV",          # 浙江
    "https://www.tvmao.com/program/SHHAI",         # 上海
    "https://www.tvmao.com/program/BTV",           # 北京
    "https://www.tvmao.com/program/CCTV",          # 央视
    "https://www.tvmao.com/program_digital",       # 数字付费
    "https://www.tvmao.com/program_digital/CETV1-w7.html",
    "https://www.tvmao.com/program_satellite",     # 卫视
    "https://www.tvmao.com/program_satellite/AHTV1-w7.html",
]
_ALLOWED_PREFIXES = [
    "https://www.tvmao.com/program/ZJTV",
    "https://www.tvmao.com/program/SHHAI",
    "https://www.tvmao.com/program/BTV",
    "https://www.tvmao.com/program/CCTV",
    "https://www.tvmao.com/program_digital",
    "https://www.tvmao.com/program_satellite",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class TvmaoProvider(EPGProvider):
    name = "tvmao"
    description = "电视猫节目单 (tvmao.com)"
    MAX_WORKERS = 8  # 并行抓取电视猫页面的最大线程数（保留礼貌延时）

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
            targets = self._load_target_channels(conn)
        finally:
            conn.close()

        if not targets:
            logger.info("[TvmaoProvider] 无需要补充的频道（VIS 已全覆盖或无可匹配频道）")
            return FetchResult(self.name, [], {
                "channel_count": 0, "program_count": 0,
                "no_data": 0, "failed": 0, "skipped": "no gap",
            })

        name_to_code = self._load_map()
        if not name_to_code:
            logger.error("[TvmaoProvider] 频道映射构建失败，跳过")
            return FetchResult(self.name, [], {
                "channel_count": 0, "program_count": 0,
                "no_data": 0, "failed": len(targets),
                "error": "map build failed",
            })

        # 匹配本地频道 -> 电视猫 code
        matched = []        # (db_info, tvmao_code)
        unmatched = []
        for ch in targets:
            code = self._match_channel(ch, name_to_code)
            if code:
                matched.append((ch, code))
            else:
                unmatched.append(ch["name"])
        if unmatched:
            logger.info("[TvmaoProvider] %d 个频道未能匹配电视猫: %s",
                        len(unmatched), ", ".join(unmatched))

        if not matched:
            logger.info("[TvmaoProvider] 无匹配频道，跳过")
            return FetchResult(self.name, [], {
                "channel_count": 0, "program_count": 0,
                "no_data": len(targets), "failed": 0,
            })

        today = datetime.now()
        frm = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        # 电视猫子频道提供本周(w1~w7)+下周(w8~w14)共 14 天，向未来延伸到
        # 下周日，确保子频道能把整周（含下一周）数据都抓到。VIS 已覆盖的
        # 频道电视猫本就跳过，窗口扩大对 VIS/凤凰无影响。
        monday = today.date() - timedelta(days=today.isoweekday() - 1)
        next_sunday = monday + timedelta(days=13)
        to = next_sunday.strftime("%Y-%m-%d")
        dates = self._date_range(frm, to)
        logger.info("[TvmaoProvider] 匹配 %d 个频道，拉取窗口 %s ~ %s（%d 天，并行 %d 线程）",
                    len(matched), frm, to, len(dates), self.MAX_WORKERS)
        _set_epg_status(progress=f"同步电视猫节目单 ({len(matched)} 频道)...")

        sync_time = int(time.time())
        stats = {"channel_count": 0, "program_count": 0, "no_data": 0, "failed": 0}

        # 并行抓取：每个 (频道, 日期) 一个任务，线程池并发，保留礼貌延时。
        # 仅 IO（HTTP GET）并行；解析在主线程串行完成，避免共享可变状态。
        name_to_dbinfo = {}
        tasks = []
        for ch, code in matched:
            di = {
                "channel_id": ch["channel_id"],
                "name": ch["name"],
                "tvg_id": ch["tvg_id"] or ch["name"],
            }
            name_to_dbinfo[ch["name"]] = di
            for d in dates:
                tasks.append((di, code, d))

        def _work(di, code, d):
            day_progs = self._fetch_day(code, d)
            time.sleep(_SLEEP_PER_REQ)
            return di["name"], code, d, day_progs

        raw = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = [executor.submit(_work, di, c, d) for (di, c, d) in tasks]
            done = 0
            for future in concurrent.futures.as_completed(futures):
                done += 1
                try:
                    name, code, d, day_progs = future.result()
                except Exception as e:
                    logger.warning("[TvmaoProvider] 抓取任务异常: %s", e)
                    continue
                _set_epg_status(progress=f"[电视猫 {done}/{len(tasks)}]")
                raw.append((name, code, d, day_progs))

        # 主线程解析 + 按频道归并
        by_channel = {}
        for name, code, d, day_progs in raw:
            if not day_progs:
                continue
            progs = parse_time_based_programs(d, day_progs, name_to_dbinfo[name], self.name)
            if progs:
                slot = by_channel.setdefault(name, {"code": code, "progs": []})
                slot["progs"].extend(progs)

        all_programs = []
        for name, info in by_channel.items():
            all_programs.extend(info["progs"])
            stats["channel_count"] += 1

        matched_names = set(name_to_dbinfo)
        for name in matched_names:
            if name not in by_channel:
                stats["no_data"] += 1
                logger.info("[TvmaoProvider] %s 无节目数据", name)

        if all_programs:
            wconn = get_db_connection()
            try:
                stats["program_count"] = _upsert_programs(wconn, all_programs, sync_time)
            finally:
                wconn.close()

        logger.info("[TvmaoProvider] 完成: %d 频道有数据, %d 条节目, %d 无EPG",
                    stats["channel_count"], stats["program_count"], stats["no_data"])
        return FetchResult(self.name, [], stats)

    # ---- 频道选择：仅 VIS（及凤凰）未覆盖的频道 ----
    @staticmethod
    def _load_target_channels(conn) -> list:
        """返回需要补充的本地频道：VIS 未覆盖，或仅由非 VIS 源覆盖（需刷新）。

        HD/SD/4K 兄弟频道共享同一 tvg_id：只要该 tvg_id 已有 VIS 节目（任一
        兄弟频道写入），电视猫就不再为其下任何频道抓数据，避免同一 tvg_id
        下出现 VIS + 电视猫双份重复节目。
        """
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d 00:00:00")
        c.execute(
            "SELECT channel_id, name, tvg_id FROM live_channels "
            "WHERE is_enabled = 1"
        )
        rows = c.fetchall()
        result = []
        for row in rows:
            cid = str(row["channel_id"])
            tvg_id = (row["tvg_id"] or "").strip()
            if TvmaoProvider._is_covered_by_vis(conn, tvg_id, cid, cutoff):
                continue  # tvg_id 已被 VIS 权威覆盖（含 4K 等兄弟频道），跳过
            result.append({
                "channel_id": cid,
                "name": (row["name"] or "").strip(),
                "tvg_id": tvg_id,
            })
        return result

    @staticmethod
    def _is_covered_by_vis(conn, tvg_id: str, channel_id: str, cutoff: str) -> bool:
        """该频道的 tvg_id（或自身 channel_id）近 3 天是否有 VIS 写入的节目。

        HD/SD/4K 共享 tvg_id，任一兄弟已覆盖即视为该 tvg_id 已权威覆盖，
        电视猫不覆盖，从根源避免重复。tvg_id 为空时退回仅按 channel_id 判定。
        """
        c = conn.cursor()
        if tvg_id:
            c.execute(
                "SELECT raw_data_json FROM epg_programs "
                "WHERE (epg_channel_id = ? OR channel_id = ?) AND start_time >= ? LIMIT 50",
                (tvg_id, channel_id, cutoff),
            )
        else:
            c.execute(
                "SELECT raw_data_json FROM epg_programs "
                "WHERE channel_id = ? AND start_time >= ? LIMIT 50",
                (channel_id, cutoff),
            )
        for row in c.fetchall():
            raw = row["raw_data_json"] or ""
            if '"provider":"vis"' in raw or '"provider": "vis"' in raw:
                return True
        return False

    # ---- 匹配 ----
    @staticmethod
    def _norm_key(s: str) -> str:
        """分隔符/大小写/重音归一化键：小写 + 去空格/连字符/下划线 + 去重音符号。

        用于跨"CGTN西班牙语"(本地) 与 "CGTN-西班牙语"/"CGTN 法语"(电视猫别名)
        这类仅分隔符或重音差异的精确匹配。
        """
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = s.lower()
        s = re.sub(r"[\s\-_]+", "", s)
        return s

    @staticmethod
    def _match_channel(ch: dict, name_to_code: dict):
        """本地频道名 -> 电视猫 code。

        只保留两层精确匹配，不依赖宽松的子串兜底：
        1. 归一化精确匹配（normalize_epg 去掉画质后缀后的频道名）
        2. 分隔符/大小写/重音归一化精确匹配（解决本地规范名与电视猫
           别名仅分隔符差异的问题，如 CGTN西班牙语 ↔ CGTN-西班牙语）。

        其余匹配不到的频道由用户通过"频道别名映射表"补齐规范名即可。
        """
        cand_names = [ch["name"], ch["tvg_id"]]
        # 1) 归一化精确匹配
        for n in cand_names:
            if not n:
                continue
            key = normalize_epg(n)
            if key in name_to_code:
                return name_to_code[key]
        # 2) 分隔符/大小写/重音归一化精确匹配
        for n in cand_names:
            if not n:
                continue
            ln = TvmaoProvider._norm_key(normalize_epg(n))
            if len(ln) < 3:
                continue
            codes = {code for k, code in name_to_code.items()
                     if TvmaoProvider._norm_key(k) == ln}
            if len(codes) == 1:
                return codes.pop()
        return None

    # ---- 抓取 ----
    @staticmethod
    def _normalize_code(code: str) -> str:
        """第1套频道用组级 code（如 NBTV-NBTV1 -> NBTV），其余不变。"""
        m = re.match(r"^([A-Za-z]+)-\1(\d+)$", code)
        return m.group(1) if (m and m.group(2) == "1") else code

    def _fetch_day(self, code: str, date_str: str) -> list:
        """抓取单日节目，返回 [{time:'HH:MM', title:'...'}]。

        组级 / 第1套频道走日期路径 /program/{code}/{date}（完整窗口）；
        子类频道（第2套起）电视猫按周页提供：w1~w7 = 本周一~周日，
        w8~w14 = 下周一到周日，按目标日期映射到对应的 wN 页抓取。

        code 可能带路径前缀："section|CODE"（数字付费/卫视）。注意：
        section（program_satellite / program_digital）只是爬取时的分类导航入口，
        电视猫所有频道节目单实际都挂在 /program/ 命名空间下，直接用 section 拼
        抓取路径（如 /program_satellite/SANSHATV/2026-07-12）会 404。因此抓取时
        一律优先用 /program/，仅在 404 或空结果时回退原 section（防御性）。
        """
        # code 可能带路径前缀："section|CODE"（数字付费/卫视），否则默认 program
        if "|" in code:
            section, code_only = code.split("|", 1)
        else:
            section, code_only = "program", code
        norm = self._normalize_code(code_only)
        # 候选段：优先 program（真实节目单所在），其次原 section（仅作兜底）
        sections = ["program", section] if section != "program" else [section]
        if "-" not in norm:
            for sec in sections:
                url = f"https://www.tvmao.com/{sec}/{norm}/{date_str}"
                try:
                    r = requests.get(url, headers=_HEADERS, timeout=20)
                    if r.status_code != 200:
                        continue
                    progs = self._parse_programs(TvmaoProvider._response_text(r))
                    if progs:
                        return progs
                except Exception as e:
                    logger.warning("[TvmaoProvider] 抓取 %s %s 失败: %s", code, date_str, e)
            return []
        else:
            # 子类频道：本周(w1~w7)+下周(w8~w14)
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.now().date()
            monday = today - timedelta(days=today.isoweekday() - 1)
            delta = (target - monday).days  # 本周一=0 … 下周日=13
            if not (0 <= delta <= 13):
                return []  # 超出本周+下周范围，子频道无数据
            for sec in sections:
                url = f"https://www.tvmao.com/{sec}/{code_only}-w{delta + 1}.html"
                try:
                    r = requests.get(url, headers=_HEADERS, timeout=20)
                    if r.status_code != 200:
                        continue
                    progs = self._parse_programs(TvmaoProvider._response_text(r))
                    if progs:
                        return progs
                except Exception as e:
                    logger.warning("[TvmaoProvider] 抓取 %s %s 失败: %s", code, date_str, e)
            return []

    @staticmethod
    def _parse_programs(html: str) -> list:
        """从节目页解析 {time, title} 列表（按出现顺序）。

        电视猫节目项存在三种形态，按优先级依次尝试：
        1. ``<span class="p_show"><a title="标题">...</a></span>``
        2. ``<span class="p_show"><a>链接文字</a></span>``
        3. 部分属地频道（如嘉兴/丽水）直接把标题作为纯文本放在
           ``<span class="p_show">标题</span>`` 而无 <a> 标签，需兜底取文本。
        """
        out = []
        for li in re.findall(r"<li[^>]*>(.*?)</li>", html, re.S):
            tm = re.search(r'class="(?:am|pm)"[^>]*>\s*(\d{1,2}:\d{2})', li)
            if not tm:
                continue
            title = ""
            # 1. a 的 title 属性
            ti = re.search(r'class="p_show"[^>]*>.*?<a[^>]*title="([^"]+)"', li, re.S)
            if ti:
                title = ti.group(1).strip()
            # 2. a 的链接文字
            if not title:
                ti2 = re.search(r'class="p_show"[^>]*>.*?>\s*([^<]+?)\s*</a>', li, re.S)
                title = ti2.group(1).strip() if ti2 else ""
            # 3. p_show 内纯文本兜底（去掉残留标签）
            if not title:
                ti3 = re.search(r'class="p_show"[^>]*>\s*(.*?)\s*</span>', li, re.S)
                if ti3:
                    title = re.sub(r"<[^>]+>", "", ti3.group(1)).strip()
            if not title:
                continue
            out.append({"time": tm.group(1).strip(), "title": title})
        return out

    # ---- 频道映射（BFS 爬取 + 缓存）----
    # CGTN 语言频道仅用词差异的别名补全（如 俄语<->俄罗斯语、阿拉伯语<->阿语）
    _CGTN_LANG_SYNONYMS = [
        ("俄语", "俄罗斯语"),
        ("阿拉伯语", "阿语"),
        ("西班牙语", "西语"),
    ]

    @staticmethod
    def _expand_cgtn_aliases(mapping: dict) -> dict:
        """补全 CGTN 语言频道"仅用词差异"的别名。

        电视猫页面常只用缩写字（俄语/阿语/西语），而本地 tvg_id 用全称
        （俄罗斯语/阿拉伯语/西班牙语），导致归一化精确匹配失败。这里把
        成对同义词互相补全进映射，避免漏配/错配。
        """
        additions = {}
        for a, b in TvmaoProvider._CGTN_LANG_SYNONYMS:
            for ta, tb in ((a, b), (b, a)):
                for prefix in ("CGTN", "CGTN-"):
                    ka, kb = prefix + ta, prefix + tb
                    if ka in mapping and kb not in mapping:
                        additions[kb] = mapping[ka]
                    if kb in mapping and ka not in mapping:
                        additions[ka] = mapping[kb]
        mapping.update(additions)
        return mapping

    def _load_map(self) -> dict:
        """加载/构建 归一化名 -> 电视猫 code 映射（含别名）。"""
        cached = self._read_cache()
        if cached is not None:
            return self._expand_cgtn_aliases(cached)
        logger.info("[TvmaoProvider] 构建电视猫频道映射（BFS 爬取）...")
        built = self._crawl_map()
        if built:
            self._write_cache(built)
            logger.info("[TvmaoProvider] 映射构建完成：%d 个归一化名", len(built))
        return self._expand_cgtn_aliases(built) if built else built

    def _read_cache(self):
        try:
            if not os.path.exists(_MAP_PATH):
                return None
            age = (time.time() - os.path.getmtime(_MAP_PATH)) / 86400
            if age > _MAP_TTL_DAYS:
                return None
            with open(_MAP_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("[TvmaoProvider] 读取映射缓存失败: %s", e)
            return None

    def _write_cache(self, mapping: dict):
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_MAP_PATH, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("[TvmaoProvider] 写入映射缓存失败: %s", e)

    def _crawl_map(self) -> dict:
        """BFS 遍历电视猫频道树，建立 归一化名 -> code（多线程并行爬取）。

        使用线程池并发 HTTP 请求（复用与节目抓取相同的礼貌延时），索引/分类页
        不计入频道页预算（仅统计含节目项的频道页）。仅以 _SEED_URLS 指定的地区/
        分类入口（浙江/上海/北京/央视/数字付费/卫视）作为 BFS 种子，并通过
        _ALLOWED_PREFIXES 严格限制链接跟随范围，避免全量 34 省爆炸式爬取。
        数字付费/卫视code 带 program_digital/program_satellite 前缀，供后续按
        正确路径抓节目。对 /program/<CODE> 类地区组页（浙江/上海/北京/央视），
        额外动态解析其 .tvs-list 下拉菜单，把本省/市所有下属电视台入口加为种子
        + 放行前缀，实现"爬全下属内容"（下拉菜单里"还有很多"的台一并覆盖），
        且不扩散到其它省份。爬取过程每 500 页打印一次进度，避免"看似卡死"。
        """
        name_to_code = {}
        visited = set()
        vlock = threading.Lock()   # 保护 visited（入队即标记，避免重复入队）
        mlock = threading.Lock()   # 保护 name_to_code 写
        counter = {"pages": 0, "channel_pages": 0}
        clock = threading.Lock()
        stop = threading.Event()
        # 动态扩展：对 /program/<CODE> 类地区组页（浙江/上海/北京/央视），
        # 解析其 .tvs-list 下拉菜单，把本省/市所有下属电视台入口加为种子 +
        # 放行前缀，实现"爬全下属内容"。数字付费/卫视分类页不参与扩展。
        group_re = re.compile(r"^https://www\.tvmao\.com/program/([A-Za-z0-9\-]+)/?$")
        seed_urls = list(_SEED_URLS)
        allowed = list(_ALLOWED_PREFIXES)
        expanded = 0
        for url in _SEED_URLS:
            gm = group_re.match(url)
            if not gm:
                continue  # 跳过数字付费/卫视分类页及 -wN 周页种子
            try:
                r = requests.get(url, headers=_HEADERS, timeout=20)
                if r.status_code != 200:
                    continue
                codes = TvmaoProvider._extract_dropdown_codes(
                    TvmaoProvider._response_text(r)
                )
            except Exception as e:
                logger.warning("[TvmaoProvider] 解析下拉菜单失败 %s: %s", url, e)
                continue
            for c in codes:
                sub_url = f"https://www.tvmao.com/program/{c}"
                if sub_url not in seed_urls:
                    seed_urls.append(sub_url)
                prefix = f"https://www.tvmao.com/program/{c}"
                if prefix not in allowed:
                    allowed.append(prefix)
                expanded += 1
        logger.info(
            "[TvmaoProvider] 下拉菜单扩展: 新增 %d 个下属台入口, 种子总数 %d, 放行前缀 %d",
            expanded, len(seed_urls), len(allowed),
        )

        work: "Queue[str]" = Queue()
        for url in seed_urls:
            visited.add(url)   # 入队即标记，保证每个 URL 只入队/处理一次
            work.put(url)

        def _worker():
            while not stop.is_set():
                try:
                    url = work.get(timeout=5)
                except Empty:
                    break  # 队列空且 5s 无新任务：爬取自然结束
                # URL 入队时已标记 visited（唯一），直接处理，无需再次检查
                try:
                    r = requests.get(url, headers=_HEADERS, timeout=20)
                    if r.status_code != 200:
                        work.task_done()
                        continue
                    html = TvmaoProvider._response_text(r)
                except Exception as e:
                    logger.warning("[TvmaoProvider] 爬取 %s 失败: %s", url, e)
                    work.task_done()
                    continue

                is_channel = self._looks_like_channel(html)
                if is_channel:
                    section, code = self._code_from_url(url)
                    if code:
                        names = self._channel_names(html)
                        norm_code = self._normalize_code(code)
                        value = f"{section}|{norm_code}"
                        with mlock:
                            for nm in names:
                                nk = normalize_epg(nm)
                                if not nk:
                                    continue
                                # 防御：跳过任何含西里尔字母的归一化 key（编码异常兜底）
                                if any(0x0400 <= ord(c) <= 0x04FF for c in nk):
                                    continue
                                name_to_code[nk] = value

                # 入队子链接（兼容 /program/CODE 与 /program/CODE-wN.html，
                # 以及 /program_digital/、/program_satellite/ 数字付费/卫视路径），
                # 仅保留落在 _ALLOWED_PREFIXES 范围内的链接，严格限制爬取地域。
                for link in re.findall(
                    r'href="(/program(?:_digital|_satellite)?/[A-Za-z0-9\-]+?(?:-w\d+)?(?:\.html)?)"',
                    html,
                ):
                    sub = "https://www.tvmao.com" + link
                    if not any(sub.startswith(p) for p in allowed):
                        continue
                    # 同一频道 w1~w14 周页的频道名/别名完全一致，归一到单一
                    # 代表页（-w7）避免重复抓取，页数与耗时大幅下降。
                    sub = re.sub(r"-w\d+\.html$", "-w7.html", sub)
                    with vlock:
                        if sub not in visited:
                            visited.add(sub)
                            work.put(sub)

                with clock:
                    counter["pages"] += 1
                    if is_channel:
                        counter["channel_pages"] += 1
                    p = counter["pages"]
                    cp = counter["channel_pages"]
                if p % 500 == 0:
                    logger.info(
                        "[TvmaoProvider] 爬取进度: 总页 %d / 频道页 %d / 收录 %d key",
                        p, cp, len(name_to_code),
                    )
                    with mlock:
                        self._write_cache(name_to_code)
                # 预算检查：超限即停止（安全上限 + 频道页预算）
                if p >= _MAX_TOTAL_PAGES or cp >= _MAX_CRAWL_PAGES:
                    stop.set()
                time.sleep(_SLEEP_PER_REQ)
                work.task_done()

        workers = []
        for _ in range(self.MAX_WORKERS):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            workers.append(t)
        for t in workers:
            t.join()

        self._write_cache(name_to_code)
        return name_to_code

    @staticmethod
    def _looks_like_channel(html: str) -> bool:
        # 频道节目页特征：含周选择条 week-menu，或节目表 am/pm/p_show 标记。
        # 纯索引/分类页（/program、/program_digital、/program_satellite）无
        # week-menu，不会被误判为频道页。部分县级台（如义乌/余姚）节目表用
        # tt_one_list 而非 am/pm，仅靠 week-menu 命中，避免漏收。
        return ('week-menu' in html) or ('class="am"' in html) or (
            'class="p_show"' in html) or ('class="pm"' in html)

    @staticmethod
    def _extract_dropdown_codes(html: str):
        """从频道组页的 .tvs-list 下拉菜单提取所有下属电视台 code。

        电视猫每个地区组页（如 /program/ZJTV）在该容器内列出本省/市全部
        下属电视台入口（浙江台、杭州台、宁波台、温州台…），与顶部"全国省份
        导航"是不同的容器。返回去重后的 code 列表（去 .html 与 -wN 周页）。
        """
        m = re.search(r'<div class="tvs-list">(.*?)</div>', html, re.S)
        if not m:
            return []
        block = m.group(1)
        codes = re.findall(
            r'href="/program/([A-Za-z0-9\-]+?)(?:-w\d+)?(?:\.html)?"', block
        )
        if not codes:  # 兜底：无 .html 后缀写法
            codes = re.findall(r'href="/program/([A-Za-z0-9\-]+)/?"', block)
        return sorted(set(codes))

    @staticmethod
    def _code_from_url(url: str):
        """从频道页 URL 提取 (section, code)。

        section ∈ {program, program_digital, program_satellite}，用于后续
        按正确路径抓取节目（数字付费/卫视的 URL 前缀不同）。
        """
        for section in ("program_digital", "program_satellite", "program"):
            m = re.search(rf"/{section}/([A-Za-z0-9\-]+?)(?:-w\d+)?\.html", url)
            if not m:
                m = re.search(rf"/{section}/([A-Za-z0-9\-]+)/?$", url)
            if m:
                return section, m.group(1)
        return "program", ""

    @staticmethod
    def _channel_names(html: str) -> list:
        """从频道页提取名称 + 别名列表（逐个拆分成独立 key）。"""
        names = []
        # 1) <title> 形如 "宁波新闻综合节目表,宁波新闻综合节目预告_电视猫"
        mt = re.search(r"<title>(.*?)</title>", html, re.S)
        if mt:
            t = mt.group(1)
            head = t.split(",")[0] if "," in t else t
            head = head.replace("节目表", "").replace("节目预告", "").replace("节目单", "")
            head = head.replace("_电视猫", "").strip()
            if head:
                names.append(head)
        # 2) <meta description> 别名："...又名：a,b,c"（半角/全角逗号均可能）
        md = re.search(r'name="description"[^>]*content="([^"]+)"', html)
        if md:
            content = md.group(1)
            # description 形如：
            #   "上海广播电视台新闻综合频道最新一周节目时间表。上海广播电视台新闻综合频道又名：上视新闻综合,..."
            # 频道名位于 "最新一周节目时间表" 之前；若无此前缀，退回按逗号/又名切分。
            name_seg = re.split(r"最新一周节目时间表", content)[0].strip()
            if not name_seg:
                name_seg = content.split("，")[0].split("又名：")[0].strip()
            name_seg = name_seg.rstrip("。").strip()
            if name_seg:
                names.append(name_seg)
            alias_part = content.split("又名：")
            if len(alias_part) > 1:
                for a in re.split(r"[，,]", alias_part[1]):
                    a = a.strip().rstrip("。").strip()
                    if a:
                        names.append(a)
        # 去重；并为含"频道"后缀的名称生成去后缀变体，提升匹配率
        seen = set()
        out = []
        for n in names:
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(n)
            if n.endswith("频道"):
                v = n[:-2].strip()
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
        return out

    @staticmethod
    def _response_text(r) -> str:
        """统一解码响应体：优先用 <meta charset>，否则按 UTF-8（电视猫全站 UTF-8）。

        避免 requests 的 apparent_encoding 把 UTF-8 页面误判成 Latin-1/Cyrillic 等，
        从而产生西里尔字母乱码 key。
        """
        enc = r.encoding  # requests 已按 Content-Type 设好（若有）
        if not enc:
            m = re.search(r'<meta[^>]+charset=["\']?\s*([\w-]+)',
                          r.content[:2048].decode("ascii", "ignore"), re.I)
            enc = m.group(1) if m else "utf-8"
        r.encoding = enc
        return r.text

    @staticmethod
    def _date_range(frm: str, to: str) -> list:
        d = datetime.strptime(frm, "%Y-%m-%d")
        end = datetime.strptime(to, "%Y-%m-%d")
        out = []
        while d <= end:
            out.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return out
