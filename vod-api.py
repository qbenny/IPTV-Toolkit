import os
import sys
import json
import re
import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

class MemoryLogBuffer:
    def __init__(self, capacity=1000):
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.log_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[([A-Z]+)\] (.*)$')

    def write_line(self, line: str, default_level: str):
        # Strip ANSI escape sequences (colors)
        line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
        line = line.strip()
        if not line:
            return
        
        # Filter out log polling, status polling, and static assets to prevent log flooding
        if "/api/logs" in line or "/api/sim-status" in line or "/static/" in line:
            return

        match = self.log_pattern.match(line)
        if match:
            log_time, log_level, log_msg = match.groups()
            self._add_log(log_time, log_level, log_msg)
        else:
            # Check for keywords to elevate level
            level = default_level
            lower_line = line.lower()
            if "error" in lower_line or "failed" in lower_line or "exception" in lower_line:
                level = "ERROR"
            elif "warn" in lower_line:
                level = "WARNING"
            elif "debug" in lower_line:
                level = "DEBUG"
            
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            self._add_log(now_str, level, line)

    def _add_log(self, log_time: str, level: str, message: str):
        with self.lock:
            self.buffer.append({
                "time": log_time,
                "level": level,
                "message": message
            })

    def get_logs(self, level_filter: str = "ALL"):
        with self.lock:
            logs = list(self.buffer)
        
        if level_filter == "ALL":
            return logs
            
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        try:
            min_idx = levels.index(level_filter)
        except ValueError:
            return logs
            
        filtered = []
        for log in logs:
            log_level = log.get("level", "INFO")
            try:
                log_idx = levels.index(log_level)
            except ValueError:
                log_idx = 1
            if log_idx >= min_idx:
                filtered.append(log)
        return filtered

    def clear(self):
        with self.lock:
            self.buffer.clear()

class InterceptStream:
    def __init__(self, original_stream, buffer_obj, default_level):
        self.original_stream = original_stream
        self.buffer_obj = buffer_obj
        self.default_level = default_level
        self._line_buffer = ""

    def write(self, s):
        try:
            self.original_stream.write(s)
        except Exception:
            pass
            
        try:
            self._line_buffer += s
            while "\n" in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split("\n", 1)
                self.buffer_obj.write_line(line, self.default_level)
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self.original_stream.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self.original_stream, name)

log_buffer = MemoryLogBuffer()
sys.stdout = InterceptStream(sys.stdout, log_buffer, "INFO")
sys.stderr = InterceptStream(sys.stderr, log_buffer, "ERROR")

from run_simulator import STBDeviceConfig, STBSimulator, load_stb_config, parse_epg_json

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    load_poster_cache()
    login_sim()
    if not _VIS_SECTIONS:
        _discover_vis_sections_dynamic()
    else:
        print(f">>> [VIS Sections] Using cached data ({len(_VIS_SECTIONS)} sections), skipping discovery")
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/settings")

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: Exception):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/settings")
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

# ==========================================
# 1. IPTV STB Simulator Initialization & Auth
# ==========================================

config_data = load_stb_config()
config_stb = STBDeviceConfig(
    user_id=config_data.get("user_id"),
    stb_id=config_data.get("stb_id"),
    mac_address=config_data.get("mac_address"),
    ip_address=config_data.get("ip_address"),
    base_url=config_data.get("base_url"),
    des_key=config_data.get("des_key")
)
sim = STBSimulator(config=config_stb)

heartbeat_thread = None
heartbeat_lock = threading.Lock()

def login_sim() -> bool:
    if not sim.config.user_id or not sim.config.base_url:
        print(">>> [STB Simulator] Core authentication parameters (user_id or base_url) are missing. Skipping automatic login.")
        sim.state.is_authenticated = False
        return False
    try:
        print(">>> [STB Simulator] Logging in via STBSimulator.login()...")
        success = sim.login()
        if success:
            print(f">>> [STB Simulator] Login successful. EPG Gateway: {sim.state.epg_base_url}")
            start_heartbeat_thread()
            return True
        else:
            print(">>> [STB Simulator] STBSimulator.login() returned False.")
            sim.state.is_authenticated = False
            return False
    except Exception as e:
        print(f">>> [STB Simulator] Login failed with exception: {e}")
        sim.state.is_authenticated = False
        return False

def ensure_authenticated():
    if not sim.state.is_authenticated:
        login_sim()

def start_heartbeat_thread():
    global heartbeat_thread
    with heartbeat_lock:
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            return
            
        # 使用默认 600 秒心跳间隔（与真实机顶盒 TVMSHeartbitInterval 一致），
        # 服务器可能在心跳响应中下发 NextCallInterval 动态调整。
        
        def run_heartbeat():
            print(">>> [Heartbeat Thread] Started.")
            while True:
                try:
                    if sim.state.is_authenticated:
                        sim.keep_alive()
                    else:
                        # Token 失效被 keep_alive 清除后，自动触发重登录
                        print(">>> [Heartbeat Thread] Auth state invalid, attempting re-login...")
                        login_sim()
                except Exception as e:
                    print(f">>> [Heartbeat Thread] Error: {e}")
                time.sleep(5)
                
        heartbeat_thread = threading.Thread(target=run_heartbeat, daemon=True)
        heartbeat_thread.start()

# ==========================================
# 2. Local Fallback Database Parsing
# ==========================================

poster_cache = {}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

POSTER_CACHE_FILE = os.path.join(DATA_DIR, "poster_cache.json")
VOD_CATEGORIES_FILE = os.path.join(DATA_DIR, "vod_categories.json")
VOD_SOURCE_TREE_CACHE_FILE = os.path.join(DATA_DIR, "vod_source_tree_cache.json")
_VIS_DOMAIN = None  # lazily resolved from sim.state.vis_base_url (set during login)

def _resolve_vis_domain():
    """获取 VIS 服务器地址（登录时已从 configUrl.min.js 解析并存入 sim.state.vis_base_url）。
    
    失败返回 None，调用方自行降级处理。
    """
    global _VIS_DOMAIN
    if _VIS_DOMAIN is not None:
        return _VIS_DOMAIN
    
    _VIS_DOMAIN = sim.state.vis_base_url
    if _VIS_DOMAIN:
        print(f">>> [VIS Domain] Using cached from login: {_VIS_DOMAIN}")
    else:
        print(">>> [VIS Domain] Not available (login may not have resolved it)")
    return _VIS_DOMAIN

# Shared state for background source-tree refresh
source_tree_refresh_status = {
    "refreshing": False,
    "last_updated": None,
    "error": None,
    "done": 0,
    "total": 0
}

def load_vod_categories():
    def clean_categories(cats):
        if not isinstance(cats, list):
            return cats
        for cat in cats:
            filters = cat.get("filters", [])
            cleaned_filters = []
            for filt in filters:
                if filt.get("key") == "sub_type":
                    cleaned_filters.append(filt)
                    break
            cat["filters"] = cleaned_filters
        return cats

    if os.path.exists(VOD_CATEGORIES_FILE):
        try:
            with open(VOD_CATEGORIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Old list format, migrate to dict format
                    migrated_categories = data
                    if len(data) == 2:
                        ids = {c.get("id") for c in data}
                        if ids == {"movies", "series"}:
                            default_path = os.path.join(DATA_DIR, "vod_categories_default.json")
                            if os.path.exists(default_path):
                                try:
                                    with open(default_path, "r", encoding="utf-8") as f_def:
                                        def_data = json.load(f_def)
                                        if isinstance(def_data, dict) and "categories" in def_data:
                                            migrated_categories = def_data["categories"]
                                except Exception as e_def:
                                    print(f"Error reading default categories template: {e_def}")
                    
                    migrated_categories = clean_categories(migrated_categories)
                    new_data = {
                        "version": 2,
                        "categories": migrated_categories
                    }
                    try:
                        with open(VOD_CATEGORIES_FILE, "w", encoding="utf-8") as f2:
                            json.dump(new_data, f2, ensure_ascii=False, indent=2)
                    except Exception as ex:
                        print(f"Error saving migrated categories: {ex}")
                    return migrated_categories
                elif isinstance(data, dict) and "categories" in data:
                    return clean_categories(data["categories"])
        except Exception as e:
            print(f"Error loading vod_categories.json: {e}")
            
    # Fresh start: load from default template, save as version 2 dict, and return
    categories = []
    default_path = os.path.join(DATA_DIR, "vod_categories_default.json")
    if os.path.exists(default_path):
        try:
            with open(default_path, "r", encoding="utf-8") as f_def:
                def_data = json.load(f_def)
                if isinstance(def_data, dict) and "categories" in def_data:
                    categories = def_data["categories"]
        except Exception as e_def:
            print(f"Error reading default categories template: {e_def}")
            
    if not categories:
        categories = [
            {
                "id": "movies",
                "name": "电影专区",
                "filters": [
                    {
                        "key": "sub_type",
                        "name": "分类",
                        "value": []
                    }
                ]
            },
            {
                "id": "series",
                "name": "电视剧场",
                "filters": [
                    {
                        "key": "sub_type",
                        "name": "分类",
                        "value": []
                    }
                ]
            }
        ]
        
    categories = clean_categories(categories)
    try:
        save_data = {
            "version": 2,
            "categories": categories
        }
        with open(VOD_CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving default categories: {e}")
        
    return categories


poster_lock = threading.Lock()

def load_poster_cache():
    global poster_cache
    if os.path.exists(POSTER_CACHE_FILE):
        try:
            with poster_lock:
                with open(POSTER_CACHE_FILE, "r", encoding="utf-8") as f:
                    poster_cache = json.load(f)
            print(f">>> [Poster Cache] Loaded {len(poster_cache)} items from disk.")
        except Exception as e:
            print(f">>> [Poster Cache] Error loading poster cache: {e}")
            poster_cache = {}
    else:
        poster_cache = {}

def save_poster_cache():
    try:
        with poster_lock:
            with open(POSTER_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(poster_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f">>> [Poster Cache] Error saving poster cache: {e}")

# ==========================================
# 2b. VOD Source Tree: Live Fetch & Cache
# ==========================================

# VIS API 通用请求 headers（无需 EPG session 认证）
_VIS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.0.3; zh-cn; EC6106V6U_pub_20_zjzdx Build/IML74K) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Safari/534.30 HuaWei;Resolution(PAL,720P,1080i)",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

# ============================================================
# VIS 源树种子分类 — 动态发现 + 缓存 + 硬编码保底
# ============================================================

# 各分类 → 优先级递减的 EPG JS 文件列表（column page 优先于 homepage）
_VIS_JS_CHAIN = {
    "电影 (Movies)":             ["pageColumnMovie.min.js"],
    "电视剧 (TV Series)":         ["pageColumnTeleplay.min.js"],
    "少儿 (Kids)":               ["pageColumnChild.min.js", "pageHomeChild.min.js"],
    "综艺 (Variety)":            ["pageColumnEntertainment.min.js", "pageHomeEntertainment.min.js"],
    "动漫 (Anime)":              ["pageColumnAnime.min.js", "pageSuperAnime.min.js"],
    "纪录 (Documentaries)":      ["pageColumnRecord.min.js", "pageHomeRecord.min.js"],
    "戏曲 (Opera)":              ["pageColumnOpera.min.js", "pageHomeOpera.min.js"],
    "新闻 (News)":               ["pageColumnNews.min.js", "pageHomeNews.min.js"],
}

_VIS_SECTIONS_CACHE_FILE = os.path.join(DATA_DIR, "vis_sections_cache.json")
_VIS_SECTIONS = None  # population deferred to _load_vis_sections()

# Unicom 专属分类 ID（JS 中混有，电信 VIS 服务器无法解析，必须排除）
# 新闻背景图 bg 虽非 Unicom 专属但也无法构建内容树，一并排除
_VIS_EXCLUDED_IDS = {
    # 少儿 (pageHomeChild.min.js — Unicom)
    "category_56054852", "category_66659050", "category_04174105",
    "category_75623086", "category_92150983", "category_01804543",
    "category_10057648", "category_98254862",
    # 综艺 (pageHomeEntertainment.min.js — Unicom)
    "category_49702008", "category_95361563",
    # 新闻 (pageHomeNews.min.js — Unicom)
    "category_49064940", "category_29569382", "category_30683848",
    "category_66480337", "category_71027701", "category_94378684",
    # 新闻 背景图 (telecom bg — 非内容分类)
    "category_51165776", "category_62465753",
    # 戏曲 (pageHomeOpera.min.js — Unicom)
    "category_61189266", "category_63417345",
}


def _load_vis_sections():
    """加载 VIS sections 缓存。"""
    global _VIS_SECTIONS
    if os.path.exists(_VIS_SECTIONS_CACHE_FILE):
        try:
            with open(_VIS_SECTIONS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    _VIS_SECTIONS = data
                    print(f">>> [VIS Sections] Loaded {len(data)} sections from cache")
                    return
        except Exception as e:
            print(f">>> [VIS Sections] Cache load failed: {e}")
    _VIS_SECTIONS = {}
    print(">>> [VIS Sections] No cache, will rely on dynamic discovery")


def _save_vis_sections_cache(sections):
    """持久化动态发现的 VIS sections 到磁盘。"""
    try:
        with open(_VIS_SECTIONS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sections, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f">>> [VIS Sections] Cache save failed: {e}")


def _discover_vis_sections_dynamic():
    """动态发现 VIS 各分类下的种子子分类（EPG JS 文件 + VIS API）。

    三级尝试（每种内容分类独立尝试）：
      1. column page JS → urlColumnList → VIS api/categorylist
      2. homepage JS   → urlColumnList/urlListColumn → VIS api/categorylist
      3. JS 文本中提取所有 category_* ID
    """
    global _VIS_SECTIONS

    if not sim.state.epg_base_url:
        print(">>> [VIS Sections] EPG not available, keeping current sections")
        return

    base = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/js/"

    def _download_js(filename):
        url = base + filename
        try:
            res = sim.state.session.get(url, headers=sim.config.headers, timeout=10)
            if res.status_code == 200:
                return res.text
        except Exception:
            pass
        return None

    def _extract_root_id(js_text):
        # 支持 urlColumnList / urlListColumn 两种变量名
        for var in ["urlColumnList", "urlListColumn"]:
            m = re.search(rf'{var}\s*[=:]\s*"api/categorylist/(category_\d+)\.json"', js_text)
            if m:
                return m.group(1)
        # columnListUrl 拼接模式
        m = re.search(r'columnListUrl[=:]\s*"api/categorylist/"\+.*?"(category_\d+)"', js_text)
        if m:
            return m.group(1)
        # 通用匹配
        m = re.search(r'api/categorylist/(category_\d+)\.json', js_text)
        if m:
            return m.group(1)
        return None

    discovered = {}

    for section_name, js_files in _VIS_JS_CHAIN.items():
        seeds = None
        for fn in js_files:
            js = _download_js(fn)
            if not js:
                continue

            root_id = _extract_root_id(js)
            if root_id:
                subcats = _vis_category_children(root_id)
                js_cats = sorted(set(re.findall(r'category_\d+', js)))
                if subcats:
                    vis_seeds = [item.get("code") for item in subcats if item.get("code")]
                    # 启发式判断：若 JS 内嵌的分类数远超 VIS 返回数（>1.5x），
                    # 说明 urlColumnList 指向的是子 tab 而非根分类（如动漫），应优先用 JS 文本提取
                    if js_cats and len(js_cats) > len(vis_seeds) and len(js_cats) > len(vis_seeds) * 1.5:
                        seeds = js_cats
                        print(f">>> [VIS Sections] {section_name}: {fn} → {root_id} VIS={len(vis_seeds)} < JS={len(js_cats)}, using JS cats")
                        break
                    elif vis_seeds:
                        seeds = vis_seeds
                        print(f">>> [VIS Sections] {section_name}: {fn} → {root_id} → {len(seeds)} seeds")
                        break
                # VIS 查询失败或 subcat 为空，降级为 JS 文本提取
                if js_cats:
                    seeds = js_cats
                    print(f">>> [VIS Sections] {section_name}: {fn} → {root_id} VIS_FAIL → {len(js_cats)} JS cats")
                    break
            else:
                cats = sorted(set(re.findall(r'category_\d+', js)))
                if cats:
                    seeds = cats
                    print(f">>> [VIS Sections] {section_name}: {fn} → {len(cats)} JS cats")
                    break

        if seeds:
            # 过滤无法通过电信线路访问的 ID（Unicom 专属 / 非内容节点）
            filtered = [s for s in seeds if s not in _VIS_EXCLUDED_IDS]
            if len(filtered) < len(seeds):
                print(f">>> [VIS Sections] {section_name}: excluded {len(seeds) - len(filtered)} unreachable IDs")
            if filtered:
                discovered[section_name] = filtered

    if discovered:
        _VIS_SECTIONS = discovered
        _save_vis_sections_cache(discovered)
        print(f">>> [VIS Sections] Dynamic discovery complete: {len(discovered)} sections")
    else:
        print(">>> [VIS Sections] Dynamic discovery yielded nothing, keeping current sections")


# 模块加载时初始化 _VIS_SECTIONS（缓存优先）
_load_vis_sections()


def _vis_get(path, params=None, max_retries=2):
    """向 VIS API 发起 GET 请求（裸 requests，不依赖 EPG session）。
    
    内置重试机制：连接超时自动重试（最多 max_retries 次），
    每次重试间隔递增（1s → 2s → 4s）。
    """
    vis_domain = _resolve_vis_domain()
    if not vis_domain:
        return None
    url = f"{vis_domain}{path}"
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            res = requests.get(url, params=params, headers=_VIS_HEADERS, timeout=15)
            res.encoding = "utf-8"
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == 200:
                    return data
            # 非 200 状态码不重试
            return None
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = 2 ** attempt  # 1s, 2s
                time.sleep(delay)
    # 所有重试耗尽才打印日志
    print(f">>> [Source Tree] VIS request failed {path}: {last_error}")
    return None


def _vis_category_name(cat_id):
    """获取 VIS 分类名称"""
    data = _vis_get(f"api/category/{cat_id}.json")
    if data:
        result = data.get("result", {}) or {}
        return result.get("title") or result.get("name") or "Unknown"
    return "Unknown"


def _vis_category_children(cat_id):
    """获取 VIS 分类的子分类列表"""
    data = _vis_get(f"api/categorylist/{cat_id}.json")
    if data:
        return data.get("resultSet", [])
    return []


def _vis_leaf_count(cat_id):
    """获取 VIS 叶子分类下的节目总数"""
    data = _vis_get(f"api/categoryitem/{cat_id}.json", params={
        "pageindex": "1",
        "size": "1",
        "userId": sim.config.user_id
    })
    if data:
        return int(data.get("pageInfo", {}).get("recordCount", 0) or 0)
    return 0


_VIS_VISITED_LOCK = threading.Lock()
_VIS_GLOBAL_VISITED = set()


def _build_vis_node_structure(cat_id, visited=None):
    """递归构建一个 VIS 分类节点及其子树（仅结构，不含计数）。
    
    支持两种模式：
      - visited=None（串行模式）：使用模块级全局 visited 集合
      - visited=set()（并行模式）：使用调用者传入的独立集合
    """
    if visited is None:
        visited = _VIS_GLOBAL_VISITED

    # 线程安全写入 visited
    with _VIS_VISITED_LOCK:
        if cat_id in visited:
            return None
        visited.add(cat_id)

    name = _vis_category_name(cat_id)
    if name == "Unknown":
        print(f">>> [Source Tree] Skipping unresolvable category: {cat_id}")
        return None

    node = {"name": name, "id": cat_id, "count": 0, "children": []}

    subcats = _vis_category_children(cat_id)
    if subcats:
        for sub in subcats:
            sub_code = sub.get("code")
            if sub_code and sub_code != cat_id:
                child = _build_vis_node_structure(sub_code, visited)
                if child:
                    node["children"].append(child)

    return node


def _build_seed_subtrees_parallel(seed_ids):
    """并行构建所有种子分类的子树。
    
    每个种子在独立线程中递归构建，共享全局 visited 集合避免重复。
    """
    results = {}
    visited_lock = _VIS_VISITED_LOCK
    global_visited = _VIS_GLOBAL_VISITED

    def build_one(seed_id):
        # 每个线程用独立 visited，但 check/write 通过全局集合排重
        node = _build_vis_node_structure(seed_id, visited=global_visited)
        return (seed_id, node)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(build_one, sid): sid for sid in seed_ids}
        for fut in futures:
            sid, node = fut.result()
            results[sid] = node

    return results


def _collect_leaf_nodes(node):
    """递归收集所有叶子节点（无子分类的节点）"""
    if not node["children"]:
        return [node]
    leaves = []
    for child in node["children"]:
        leaves.extend(_collect_leaf_nodes(child))
    return leaves


def _propagate_counts_up(node):
    """自底向上传播计数：叶子保持已有 count，父节点求和"""
    if not node["children"]:
        return node["count"]  # 叶子节点，count 已在并行阶段填入
    total = sum(_propagate_counts_up(c) for c in node["children"])
    node["count"] = total
    return total


def _fetch_all_leaf_counts_parallel(leaf_nodes):
    """使用线程池并行抓取所有叶子节点的节目计数"""
    global source_tree_refresh_status

    source_tree_refresh_status["total"] = len(leaf_nodes)
    source_tree_refresh_status["done"] = 0
    status_lock = threading.Lock()

    def fetch_one(node):
        node["count"] = _vis_leaf_count(node["id"])
        with status_lock:
            source_tree_refresh_status["done"] += 1

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(fetch_one, leaf_nodes))

    return leaf_nodes


def fetch_source_tree_structure():
    """从 VIS API 动态拉取完整目录树（含实时计数）。
    
    三阶段：
      1. 并行构建树结构（种子线程并发，5 workers）
      2. 并行抓取所有叶子节点计数（ThreadPoolExecutor, 5 workers）
      3. 自底向上传播计数
    """
    global source_tree_refresh_status, _VIS_GLOBAL_VISITED

    all_seed_ids = []
    for seeds in _VIS_SECTIONS.values():
        all_seed_ids.extend(seeds)
    total_seeds = len(all_seed_ids)

    # ---------- Phase 1: 并行构建树结构 ----------
    print(f">>> [Source Tree] Phase 1: Building tree structure ({total_seeds} seeds, parallel, 10 workers)...")
    source_tree_refresh_status["total"] = total_seeds
    source_tree_refresh_status["done"] = 0

    # 清空全局 visited 集合
    _VIS_GLOBAL_VISITED.clear()

    seed_results = _build_seed_subtrees_parallel(all_seed_ids)

    # 组装 section → seeds 映射
    tree = []
    idx = 0
    for section_name, seeds in _VIS_SECTIONS.items():
        root_id = f"root_{abs(hash(section_name))}"
        root = {"name": section_name, "id": root_id, "children": [], "count": 0}
        for seed_id in seeds:
            node = seed_results.get(seed_id)
            if node:
                root["children"].append(node)
            idx += 1
            source_tree_refresh_status["done"] = idx
        tree.append(root)

    # ---------- Phase 2: 并行抓取叶子计数 ----------
    all_leaves = []
    for root in tree:
        for child in root["children"]:
            all_leaves.extend(_collect_leaf_nodes(child))

    print(f">>> [Source Tree] Phase 2: Fetching counts for {len(all_leaves)} leaf nodes (parallel, 10 workers)...")
    _fetch_all_leaf_counts_parallel(all_leaves)

    # ---------- Phase 3: 传播计数到父节点 ----------
    print(f">>> [Source Tree] Phase 3: Propagating counts upwards...")
    for root in tree:
        for child in root["children"]:
            _propagate_counts_up(child)
        root["count"] = sum(c["count"] for c in root["children"])

    return tree


def refresh_source_tree_bg():
    """Background task: fetch full tree structure from VIS + persist cache."""
    global source_tree_refresh_status
    source_tree_refresh_status["refreshing"] = True
    source_tree_refresh_status["error"] = None
    source_tree_refresh_status["done"] = 0
    source_tree_refresh_status["total"] = 0

    try:
        _discover_vis_sections_dynamic()
        tree = fetch_source_tree_structure()

        cache_data = {"tree": tree, "updated_at": time.time()}
        with open(VOD_SOURCE_TREE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        source_tree_refresh_status["last_updated"] = cache_data["updated_at"]
        print(f">>> [Source Tree] Refresh done. {len(tree)} sections, "
              f"{source_tree_refresh_status['done']} seeds processed.")
    except Exception as e:
        source_tree_refresh_status["error"] = str(e)
        print(f">>> [Source Tree] Refresh failed: {e}")
    finally:
        source_tree_refresh_status["refreshing"] = False


# ==========================================
# 3. TVBox Config & API Handlers
# ==========================================

@app.get("/zjvod") #待修改
async def get_tvbox_config(request: Request):
    api_url = str(request.base_url) + "api/vod"
    
    cfg_ver = "1"
    if os.path.exists(VOD_CATEGORIES_FILE):
        cfg_ver = str(int(os.path.getmtime(VOD_CATEGORIES_FILE)))
        
    config_data = {
        "sites": [
            {
                "key": f"Telecom_VOD_{cfg_ver}",
                "name": "浙江电信点播",
                "type": 1,
                "api": api_url,
                "playUrl": "json:" + str(request.base_url) + "api/play?vod_id=",
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 1
            }
        ]
    }
    return JSONResponse(content=config_data)

@app.get("/api/vod")
async def handle_tvbox_request(request: Request):
    ac = request.query_params.get("ac", "")
    t = request.query_params.get("t", "")
    pg = request.query_params.get("pg", "1")
    wd = request.query_params.get("wd", "")
    ids = request.query_params.get("ids", "")

    print(f">>> [API Request] ac={ac}, t={t}, pg={pg}, wd={wd}, ids={ids}, query_params={dict(request.query_params)}")

    page = int(pg) if pg.isdigit() else 1

    # ------------------------------------------
    # 场景 1: 获取视频详情
    # ------------------------------------------
    if ac == "detail" and ids:
        id_list = ids.split(",")
        detail_list = []
        for current_id in id_list:
            # 说明是通过实时分类/实时搜索拉取到的视频 ID，格式为 {item_type}_{item_code}
            parts = current_id.split("_", 1)
            if len(parts) != 2:
                continue
            item_type, item_code = parts
            
            try:
                ensure_authenticated()
                data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
                
                # A. 根据电信 itemCode 变换出内部物理 vod_id
                params_code = {
                    "Action": "vodIdByCode",
                    "foreignSN": item_code,
                    "contentType": "0"
                }
                res_code = sim.state.session.get(data_url, params=params_code, headers=sim.config.headers, timeout=10)
                data_code = parse_epg_json(res_code.text)
                vod_id = data_code.get("result", {}).get("id")
                if not vod_id:
                    continue
                vod_id = str(vod_id)
                
                # B. 单个电影资源：使用延迟加载链接，避免一次性生成导致的高频认证失败
                if item_type == "vod":
                    result_vod = sim.get_vod_info(vod_id) or {}
                    
                    play_url = vod_id
                    name = result_vod.get("name") or f"{item_code} (电影)"
                    content = result_vod.get("introduce") or "热播大片专区"
                    
                    with poster_lock:
                        pic_url = poster_cache.get(current_id) or ""
                    detail_list.append({
                        "vod_id": current_id,
                        "vod_name": name,
                        "vod_pic": pic_url,
                        "type_name": "电影",
                        "vod_content": content,
                        "vod_play_from": "电信专线",
                        "vod_play_url": f"播放${play_url}",
                        "vod_remarks": "高清"
                    })
                    
                # C. 电视剧资源：遍历分集列表，统一封装为代理接口
                elif item_type == "series":
                    series_info = sim.get_series_info(vod_id)
                    if not series_info:
                        continue
                    
                    name = series_info.get("name") or f"{item_code} (电视剧)"
                    content = series_info.get("introduce") or "热播剧集专区"
                    episode_list = series_info.get("episodes", [])
                    
                    # Determine if any episodes will be skipped (EPG "缺" data)
                    total_count = len(episode_list)
                    valid_count = sum(
                        1 for ep in episode_list
                        if ep.get("id") and str(ep.get("id")) != "缺"
                        and "缺" not in str(ep.get("id"))
                        and str(ep.get("id")).isdigit()
                    )
                    use_original_num = (valid_count == total_count)  # no skipping → keep EPG num

                    ep_play_urls = []
                    display_num = 0
                    for ep in episode_list:
                        ep_id = ep.get("id")

                        # Skip placeholder / missing episodes (EPG returns "缺" or non-digit IDs)
                        if not ep_id or ep_id == "缺" or "缺" in ep_id or not ep_id.isdigit():
                            continue

                        display_num += 1
                        num_str = ep.get("num") if use_original_num else str(display_num)
                        if not num_str or not num_str.isdigit():
                            num_str = str(display_num)

                        telecom_code = ep.get("telecom_code", "")
                        if telecom_code:
                            play_url = f"{ep_id}${telecom_code}"
                        else:
                            play_url = ep_id
                        ep_play_urls.append(f"第{num_str}集${play_url}")
                        
                    with poster_lock:
                        pic_url = poster_cache.get(current_id) or ""
                    detail_list.append({
                        "vod_id": current_id,
                        "vod_name": name,
                        "vod_pic": pic_url,
                        "type_name": "电视剧",
                        "vod_content": content,
                        "vod_play_from": "电信专线",
                        "vod_play_url": "#".join(ep_play_urls),
                        "vod_remarks": f"更新至{len(ep_play_urls)}集" if ep_play_urls else "暂无内容"
                    })
            except Exception as e:
                print(f"动态详情查询失败 for {current_id}: {e}")
                    
        return JSONResponse(content={"code": 1, "list": detail_list})

    # ------------------------------------------
    # 场景 2: 获取分类列表 / 实时搜索
    # ------------------------------------------
    elif (ac == "list" or ac == "detail") and (t or wd):
        # 实时搜索分支 (EPG Action=search)
        if wd:
            vod_list = []
            try:
                ensure_authenticated()
                data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
                params_search = {
                    "Action": "search",
                    "keyword": wd,
                    "posterflag": "1",
                    "displayflag": "0",
                    "columnStart": "0",
                    "columnLength": "40",
                    "vodLength": "40"
                }
                res = sim.state.session.get(data_url, params=params_search, headers=sim.config.headers, timeout=10)
                if res.status_code == 200:
                    data = parse_epg_json(res.text)
                    result_set = data.get("result", [])
                    if isinstance(result_set, list):
                        for item in result_set:
                            name = item.get("name", "Unknown")
                            telecom_code = item.get("telecomCode", "")
                            item_type_val = item.get("type", "0")
                            item_type = "series" if item_type_val == "1" else "vod"
                            
                            vod_list.append({
                                "vod_id": f"{item_type}_{telecom_code}",
                                "vod_name": name,
                                "vod_pic": "",
                                "vod_remarks": "电影" if item_type == "vod" else "电视剧"
                            })
            except Exception as e:
                print(f"Live search failed: {e}")
                
            return JSONResponse(content={
                "code": 1,
                "page": page,
                "pagecount": (len(vod_list) // 20) + 1,
                "limit": 20,
                "total": len(vod_list),
                "list": vod_list
            })
            
        # 分类列表分支 (VIS api/categoryitem)
        if t:
            sub_type = request.query_params.get("sub_type", "")
            
            # TVBox standard: check if filters are passed in the 'f' query parameter
            f_param = request.query_params.get("f", "")
            if f_param:
                try:
                    import base64
                    try:
                        padded = f_param + "=" * ((4 - len(f_param) % 4) % 4)
                        decoded_str = base64.b64decode(padded).decode('utf-8')
                        f_json = json.loads(decoded_str)
                    except Exception:
                        # Try raw JSON
                        f_json = json.loads(f_param)
                        
                    if isinstance(f_json, dict):
                        if "sub_type" in f_json:
                            sub_type = f_json["sub_type"]
                        print(f">>>> [API] Parsed filters from 'f': {f_json}")
                except Exception as e:
                    print(f">>> [API] Failed to parse filter parameter 'f' ({f_param}): {e}")

            if sub_type:
                target_cat = sub_type
            else:
                # Resolve target category dynamically from user's custom categories first
                target_cat = None
                custom_cats = load_vod_categories()
                for cat in custom_cats:
                    if cat.get("id") == t:
                        filters = cat.get("filters", [])
                        for filt in filters:
                            if filt.get("key") == "sub_type":
                                vals = filt.get("value", [])
                                for val in vals:
                                    if val.get("v"):
                                        target_cat = val.get("v")
                                        break
                                if not target_cat and vals:
                                    target_cat = vals[0].get("v")
                                break
                        break
                
                if not target_cat:
                    target_cat = t
                    
            vod_list = []
            try:
                vis_domain = _resolve_vis_domain()
                if not vis_domain:
                    print(f">>> [VOD] VIS domain unavailable, skipping request for /api/vod")
                    return
                
                # Dynamic mapping from user setting - no hardcoded routing tables needed
                query_cat = target_cat
                
                url_items = f"{vis_domain}api/categoryitem/{query_cat}.json"
                params_items = {
                    "pageindex": str(page),
                    "size": "20",
                    "userId": sim.config.user_id
                }
                res = requests.get(url_items, params=params_items, headers=sim.config.headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    status = data.get("status")
                    result_set = data.get("resultSet")
                    
                    if status == 200 and isinstance(result_set, list) and (result_set or page > 1):
                        image_server = data.get("imageServer")  # VIS API returns this field
                        
                        for item in result_set:
                            item_type = item.get("itemType", "vod")
                            # Skip non-playable assets (like links or subjects) which fail to play in TVBox
                            if item_type not in ["vod", "series"]:
                                continue
                                
                            title = item.get("title", "Unknown")
                            item_code = item.get("itemCode", "")
                            icon = item.get("itemIcon") or item.get("contentPictures", {}).get("poster1") or ""
                            pic_url = f"{image_server}{icon}" if (icon and image_server and not icon.startswith("http")) else icon
                            if not pic_url:
                                pic_url = ""
                            
                            item_id = f"{item_type}_{item_code}"
                            with poster_lock:
                                poster_cache[item_id] = pic_url
                            
                            vod_list.append({
                                "vod_id": item_id,
                                "vod_name": title,
                                "vod_pic": pic_url,
                                "vod_remarks": "电影" if item_type == "vod" else "电视剧"
                             })
                        save_poster_cache()
                        
                        page_info = data.get("pageInfo", {})
                        total_count = page_info.get("recordCount", len(vod_list))
                        page_count = page_info.get("pageCount", page)
                            
                        return JSONResponse(content={
                            "code": 1,
                            "page": page,
                            "pagecount": page_count,
                            "limit": 20,
                            "total": total_count,
                            "list": vod_list
                        })
            except Exception as e:
                print(f"Live category items fetch failed for {target_cat} (routed to {query_cat if 'query_cat' in locals() else target_cat}): {e}")

            return JSONResponse(content={"code": 1, "page": page, "pagecount": 1, "limit": 20, "total": 0, "list": []})

    # ------------------------------------------
    # 场景 3: 初始化请求，返回顶级分类
    # ------------------------------------------
    else:
        custom_cats = load_vod_categories()
        
        vis_categories = []
        vis_filters = {}
        
        for cat in custom_cats:
            cat_id = cat.get("id")
            cat_name = cat.get("name")
            cat_filters = cat.get("filters", [])
            
            vis_categories.append({"type_id": cat_id, "type_name": cat_name})
            vis_filters[cat_id] = cat_filters
                
        return JSONResponse(content={"code": 1, "class": vis_categories, "filters": vis_filters})

# ==========================================
# 4. Lazy Playback Redirection Resolver
# ==========================================

@app.get("/api/play")
@app.get("/api/play.ts")
@app.get("/api/play/{vod_id_path}.ts")
async def play_redirect(request: Request, vod_id: str = None, url: str = None, vod_id_path: str = None):
    if vod_id_path:
        vod_id = vod_id_path
        
    # Extract original parameters if wrapped by TVBox playUrl prefixing
    if vod_id and "api/play" in vod_id:
        if "url=" in vod_id:
            url = vod_id.split("url=", 1)[1]
            vod_id = None
        elif "vod_id=" in vod_id:
            vod_id = vod_id.split("vod_id=", 1)[1]

    # Clean empty strings to None
    if url == "":
        url = None
    if vod_id == "":
        vod_id = None

    if not url and not vod_id:
        return JSONResponse(content={"error": "Missing vod_id or url parameter"}, status_code=400)

    # Passthrough direct playback URLs
    target_url = None
    if url:
        target_url = url
    elif vod_id and (vod_id.startswith("http://") or vod_id.startswith("https://") or vod_id.startswith("rtsp://")):
        target_url = vod_id
        
    if target_url:
        if target_url.startswith("rtsp://"):
            target_url = target_url.replace("rtsp://", "http://")
        print(f">>> [Resolver] Passthrough play URL (rewritten to HTTP): {target_url}")
        return JSONResponse(content={
            "parse": 0,
            "url": target_url,
            "header": {
                "User-Agent": "CTC-2k/1.0 EPG/3.0 STB"
            }
        })
        
    # Otherwise, resolve the dynamic EPG vod_id
    if vod_id:
        try:
            ensure_authenticated()
            media_url = sim.get_vod_play_url(vod_id)
            if media_url:
                target_url = media_url.split("?")[0]
        except Exception as e:
            print(f">>> [Resolver] Error resolving play URL for vod_id {vod_id}: {e}")
            
    if target_url:
        if target_url.startswith("rtsp://"):
            target_url = target_url.replace("rtsp://", "http://")
        print(f">>> [Resolver] Resolved EPG play URL (rewritten to HTTP): {target_url}")
        return JSONResponse(content={
            "parse": 0,
            "url": target_url,
            "header": {
                "User-Agent": sim.config.headers.get("User-Agent", "CTC-2k/1.0 EPG/3.0 STB")
            }
        })
        
    return JSONResponse(content={"error": "Play URL resolution failed"}, status_code=404)




# ==========================================
# 4. Web Dashboard API Endpoints
# ==========================================

@app.get("/settings")
async def get_settings():
    return RedirectResponse(url="/static/index.html")

@app.get("/api/stb-config")
async def get_stb_config():
    return load_stb_config()

@app.get("/api/sim-status")
async def get_sim_status():
    """Return the current authentication status, JSESSIONID and UserToken of the STB simulator."""
    jsessionid = sim.state.session.cookies.get("JSESSIONID", None)
    return {
        "is_authenticated": sim.state.is_authenticated,
        "epg_base_url": sim.state.epg_base_url or None,
        "user_token": sim.state.user_token or None,
        "jsessionid": jsessionid,
    }

@app.post("/api/stb-config")
async def save_stb_config(config_in: dict):
    try:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        config_path = os.path.join(data_dir, "stb_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_in, f, ensure_ascii=False, indent=2)
        
        # Reinitialize active simulator configuration
        global config_stb, sim
        config_stb = STBDeviceConfig(
            user_id=config_in.get("user_id"),
            stb_id=config_in.get("stb_id"),
            mac_address=config_in.get("mac_address"),
            ip_address=config_in.get("ip_address"),
            base_url=config_in.get("base_url"),
            des_key=config_in.get("des_key")
        )
        sim.config = config_stb
        sim.state.is_authenticated = False # Force re-auth
        
        # Automatically test login if core parameters are present
        if config_stb.user_id and config_stb.base_url:
            login_success = login_sim()
            if login_success:
                return {"status": "success", "message": "配置保存成功，且模拟登录验证成功！", "ip": config_stb.ip_address}
            else:
                return {"status": "warning", "message": "配置保存成功，但模拟登录失败，请检查参数或网络连通性。", "ip": config_stb.ip_address}
        
        return {"status": "success", "message": "配置保存成功！核心参数为空，暂未运行登录测试。", "ip": config_stb.ip_address}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"保存配置失败: {str(e)}"}, status_code=500)

@app.get("/api/vod-categories")
async def get_vod_categories():
    return load_vod_categories()

@app.post("/api/vod-categories")
async def save_vod_categories(request: Request):
    try:
        categories = await request.json()
        
        # Clean up: only keep the "sub_type" filter for each category
        for cat in categories:
            filters = cat.get("filters", [])
            cleaned_filters = []
            for filt in filters:
                if filt.get("key") == "sub_type":
                    cleaned_filters.append(filt)
                    break
            cat["filters"] = cleaned_filters

        save_data = {
            "version": 2,
            "categories": categories
        }
        with open(VOD_CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        return {"status": "success", "message": "分类配置已保存！"}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"保存失败: {str(e)}"}, status_code=500)

@app.get("/api/vod-filters")
async def get_vod_filters():
    cats = load_vod_categories()
    movies_filters = []
    series_filters = []
    for cat in cats:
        if cat.get("id") == "movies":
            movies_filters = cat.get("filters", [])
        elif cat.get("id") == "series":
            series_filters = cat.get("filters", [])
    return {
        "movies": movies_filters,
        "series": series_filters
    }

@app.post("/api/vod-filters")
async def save_vod_filters(filters: dict):
    try:
        movies_filters = filters.get("movies", [])
        series_filters = filters.get("series", [])
        
        cats = load_vod_categories()
        for cat in cats:
            if cat.get("id") == "movies":
                cat["filters"] = movies_filters
            elif cat.get("id") == "series":
                cat["filters"] = series_filters
                
        # Clean up: only keep the "sub_type" filter for each category
        for cat in cats:
            filters = cat.get("filters", [])
            cleaned_filters = []
            for filt in filters:
                if filt.get("key") == "sub_type":
                    cleaned_filters.append(filt)
                    break
            cat["filters"] = cleaned_filters

        save_data = {
            "version": 2,
            "categories": cats
        }
        with open(VOD_CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
            
        return {"status": "success", "message": "分类配置已保存！"}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"保存失败: {str(e)}"}, status_code=500)

@app.get("/api/vod-source-tree")
async def get_vod_source_tree():
    """Return cached category tree (with counts)."""
    if os.path.exists(VOD_SOURCE_TREE_CACHE_FILE):
        try:
            with open(VOD_SOURCE_TREE_CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            return JSONResponse(content={
                "tree": cache_data["tree"],
                "refreshing": source_tree_refresh_status["refreshing"],
                "done": source_tree_refresh_status["done"],
                "total": source_tree_refresh_status["total"],
                "last_updated": cache_data.get("updated_at"),
                "cached": True
            })
        except Exception as e:
            print(f">>> [Source Tree] Cache read error: {e}")

    return JSONResponse(content={
        "tree": [],
        "refreshing": source_tree_refresh_status["refreshing"],
        "done": source_tree_refresh_status["done"],
        "total": source_tree_refresh_status["total"],
        "last_updated": None,
        "cached": False
    })


@app.post("/api/vod-source-tree/refresh")
async def trigger_source_tree_refresh():
    """Kick off a background refresh from the Telecom VIS server."""
    if source_tree_refresh_status["refreshing"]:
        return JSONResponse(content={
            "status": "already_refreshing",
            "message": f"正在抓取中… ({source_tree_refresh_status['done']}/{source_tree_refresh_status['total']})",
            "done": source_tree_refresh_status["done"],
            "total": source_tree_refresh_status["total"]
        })

    thread = threading.Thread(target=refresh_source_tree_bg, daemon=True)
    thread.start()
    return {"status": "started", "message": "已开始从电信服务器抓取分类数据，请稍候查看进度…"}


@app.get("/api/vod-source-tree/status")
async def get_source_tree_status():
    """Return current refresh progress."""
    return JSONResponse(content=source_tree_refresh_status)


@app.get("/api/logs")
async def get_api_logs(level: str = "ALL"):
    return log_buffer.get_logs(level)

@app.post("/api/logs/clear")
async def clear_api_logs():
    log_buffer.clear()
    return {"status": "success", "message": "日志已清空"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8880)