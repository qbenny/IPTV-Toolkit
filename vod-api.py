import re
import os
import json
import ast
import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, FileResponse
import uvicorn
import sys
import threading
import time
from contextlib import asynccontextmanager

from run_simulator import STBDeviceConfig, STBSimulator, load_stb_config

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    parse_vod_tree_db()
    load_poster_cache()
    login_sim()
    start_heartbeat_thread()
    yield

app = FastAPI(lifespan=lifespan)

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

def login_sim() -> bool:
    try:
        print(">>> [STB Simulator] Logging in via STBSimulator.login()...")
        success = sim.login()
        if success:
            print(f">>> [STB Simulator] Login successful. EPG Gateway: {sim.state.epg_base_url}")
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
    sim.state.heartbeat_interval = 60  # Keep session alive every 60 seconds
    
    def run_heartbeat():
        print(">>> [Heartbeat Thread] Started.")
        while True:
            try:
                if sim.state.is_authenticated:
                    sim.keep_alive()
            except Exception as e:
                print(f">>> [Heartbeat Thread] Error: {e}")
            time.sleep(5)
            
    thread = threading.Thread(target=run_heartbeat, daemon=True)
    thread.start()

# ==========================================
# 2. Local Fallback Database Parsing
# ==========================================

# Movie category IDs from vod_catalog_tree.txt
MOVIE_CATEGORY_IDS = {
    # parent and subcategories of 全部 (category_89204884)
    "category_89204884", "category_30256026", "category_77478703", "category_60856427", "category_92966448",
    "category_47958925", "category_90890337", "category_99283926", "category_48291795", "category_25096544",
    "category_12515419", "category_54556779",
    # parent and subcategories of 按地区 (category_00823570)
    "category_00823570", "category_91911554", "category_53156750", "category_81595755", "category_77050778", "category_24621713",
    # parent and subcategories of 系列电影1080P (category_26400459)
    "category_26400459", "category_51059430", "category_09384769", "category_46498124108575", "category_30137146",
    "category_27458932", "category_07174128", "category_03328978", "category_01196180", "category_00899862",
    "category_17315740", "category_60573590", "category_64313723", "category_83158034", "category_51157494",
    "category_40067906", "category_00592185", "category_87758064", "category_81127999", "category_09547213",
    "category_43528529", "category_69098399", "category_12964109", "category_80333058", "category_29656553",
    "category_71435388",
    # parent and subcategories of 焦点影人1080P (category_45105530)
    "category_45105530", "category_44886379", "category_95945974", "category_57409386", "category_29357127",
    "category_27993957", "category_63619927", "category_82300480", "category_85915433", "category_28165396",
    "category_40590600", "category_28614035", "category_88927882", "category_37098254", "category_97599969",
    "category_29833447", "category_91815732", "category_87376174", "category_87527976", "category_07462344",
    "category_76696250", "category_79418040", "category_39594154", "category_15463717", "category_70457117",
    "category_67035772", "category_83536621", "category_85573602", "category_78918820", "category_88176352",
    "category_61970730", "category_57376966", "category_95491792", "category_99760210", "category_76864342",
    "category_96275013", "category_19110348", "category_01432705", "category_55606702", "category_47745731",
    "category_72709849", "category_38732278", "category_46796922",
    # direct movie categories
    "category_82023212", "category_96791097", "category_80454289", "category_18147166", "movies"
}

# Series category IDs from vod_catalog_tree.txt
SERIES_CATEGORY_IDS = {
    # parent and subcategories of 全部剧集 (category_32798195)
    "category_32798195", "category_19092137", "category_82205358", "category_86585701", "category_33770041",
    "category_42484986", "category_52215058", "category_88074906", "category_94892066", "category_54399439",
    "category_88274503", "category_77510266",
    # direct series categories
    "category_36870794", "category_92376156", "category_53224763", "category_18008093", "category_69357102", "series", "latest",
    # Anime category IDs
    "category_77371402", "category_03824847", "category_19100400", "category_82215916", "category_36201848", "category_97885024", "category_82114042", "anime",
    # Documentary category IDs
    "category_88700231", "category_28343770", "category_10329781", "category_29507778", "category_25293790", "category_72107823", "category_15966618", "category_33701197", "category_26813153", "category_78669433", "category_23729098", "documentary"
}

categories_fallback = []
vods_fallback = {}
list_by_cat_fallback = {}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

POSTER_CACHE_FILE = os.path.join(DATA_DIR, "poster_cache.json")
MOVIES_FILTERS_FILE = os.path.join(DATA_DIR, "movies_filters.json")
SERIES_FILTERS_FILE = os.path.join(DATA_DIR, "series_filters.json")

def load_poster_cache():
    global poster_cache
    if os.path.exists(POSTER_CACHE_FILE):
        try:
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
        with open(POSTER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(poster_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f">>> [Poster Cache] Error saving poster cache: {e}")

def parse_vod_tree_db():
    global categories_fallback, vods_fallback, list_by_cat_fallback
    tree_path = os.path.join("research_and_debug", "vis_comprehensive_vod_tree.txt")
    if not os.path.exists(tree_path):
        print(f"Warning: {tree_path} not found. Local database fallback disabled.")
        return
        
    cat_header_pattern = re.compile(r"^==================== \[分类\] (.+?) \(ID: (.+?)\) ====================")
    series_pattern = re.compile(r"^  - \[(电视剧|少儿|动漫|综艺|新闻|戏曲|中国蓝|纪录)\] (.+?) \(ID: (.+?)\)")
    movie_pattern = re.compile(r"^  - \[(电影)\] (.+?) \| (.+)")
    episode_pattern = re.compile(r"^      \* (.+?) \| (.+)")
    
    current_cat_id = None
    current_cat_name = None
    current_series = None
    
    try:
        with open(tree_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                    
                cat_match = cat_header_pattern.match(line)
                if cat_match:
                    current_cat_name, current_cat_id = cat_match.groups()
                    categories_fallback.append({"type_id": current_cat_id, "type_name": current_cat_name})
                    list_by_cat_fallback[current_cat_id] = []
                    current_series = None
                    continue
                    
                if not current_cat_id:
                    continue
                    
                movie_match = movie_pattern.match(line)
                if movie_match:
                    m_type, name, play_url = movie_match.groups()
                    url_digits = re.findall(r"\d+", play_url)
                    m_id = f"movie_{url_digits[-1]}" if url_digits else f"movie_{hash(name + current_cat_id)}"
                    
                    vod_item = {
                        "vod_id": m_id,
                        "vod_name": name,
                        "vod_pic": "http://115.233.200.61:58001/pics/default_poster.jpg",
                        "type_name": m_type,
                        "vod_content": f"分类: {current_cat_name}",
                        "vod_play_from": "电信专线",
                        "vod_play_url": f"播放${play_url}" if play_url else "",
                        "vod_remarks": "高清" if "HD" in name or "4K" not in name else "4K"
                    }
                    
                    vods_fallback[m_id] = vod_item
                    list_by_cat_fallback[current_cat_id].append(vod_item)
                    current_series = None
                    continue
                    
                series_match = series_pattern.match(line)
                if series_match:
                    s_type, name, s_id = series_match.groups()
                    current_series = {
                        "vod_id": s_id,
                        "vod_name": name,
                        "vod_pic": "http://115.233.200.61:58001/pics/default_poster.jpg",
                        "type_name": s_type,
                        "vod_content": f"分类: {current_cat_name}",
                        "vod_play_from": "电信专线",
                        "episodes": [],
                        "vod_remarks": "电视剧"
                    }
                    vods_fallback[s_id] = current_series
                    list_by_cat_fallback[current_cat_id].append(current_series)
                    continue
                    
                if current_series:
                    ep_match = episode_pattern.match(line)
                    if ep_match:
                          ep_name, ep_url = ep_match.groups()
                          current_series["episodes"].append(f"{ep_name}${ep_url}")
                          
        for vod_id, item in vods_fallback.items():
            if "episodes" in item:
                if item["episodes"]:
                    item["vod_play_url"] = "#".join(item["episodes"])
                    item["vod_remarks"] = f"更新至{len(item['episodes'])}集"
                else:
                    item["vod_play_url"] = ""
                    item["vod_remarks"] = "暂无内容"
                del item["episodes"]
                
        # Deduplicated merge of movie subcategories under category_89204884 (全部电影)
        movie_cats = ["category_82023212", "category_96791097", "category_80454289", "category_18147166"]
        merged_movies = []
        seen_movie_ids = set()
        for cat in movie_cats:
            if cat in list_by_cat_fallback:
                for item in list_by_cat_fallback[cat]:
                    if item["vod_id"] not in seen_movie_ids:
                        seen_movie_ids.add(item["vod_id"])
                        merged_movies.append(item)
        list_by_cat_fallback["category_89204884"] = merged_movies

        # Deduplicated merge of series subcategories under category_32798195 (全部剧集)
        series_cats = ["category_36870794", "category_92376156", "category_53224763", "category_18008093", "category_69357102"]
        merged_series = []
        seen_series_ids = set()
        for cat in series_cats:
            if cat in list_by_cat_fallback:
                for item in list_by_cat_fallback[cat]:
                    if item["vod_id"] not in seen_series_ids:
                        seen_series_ids.add(item["vod_id"])
                        merged_series.append(item)
        list_by_cat_fallback["category_32798195"] = merged_series

        print(f">>> [Database] Parsed fallback database loaded successfully: {len(vods_fallback)} items. Merged movies: {len(merged_movies)}, Merged series: {len(merged_series)}.")
    except Exception as e:
        print(f">>> [Database] Error parsing fallback database: {e}")

def parse_epg_json(text):
    try:
        return json.loads(text)
    except:
        pass
    try:
        cleaned = text.strip()
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = cleaned[1:-1].strip()
        return ast.literal_eval(cleaned)
    except:
        return {}



# ==========================================
# 3. TVBox Config & API Handlers
# ==========================================

@app.get("/config")
async def get_tvbox_config(request: Request):
    api_url = str(request.base_url) + "api/vod"
    config_data = {
        "sites": [
            {
                "key": "Telecom_VOD",
                "name": "🔥 浙江电信点播",
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
            # 优先从本地已缓存/静态导出的库中返回 (动态将本地播放地址重写为 HTTP 代理格式)
            if current_id in vods_fallback:
                item_copy = dict(vods_fallback[current_id])
                item_copy["vod_pic"] = poster_cache.get(current_id) or (str(request.base_url) + "pics/default_poster.jpg")
                orig_play_url = item_copy.get("vod_play_url", "")
                if orig_play_url:
                    parts = orig_play_url.split("#")
                    rewritten_parts = []
                    for part in parts:
                        sub_parts = part.split("$", 1)
                        if len(sub_parts) == 2:
                            ep_name, ep_url = sub_parts
                            ep_url = ep_url.strip()
                            if ep_url.startswith("rtsp://"):
                                proxy_url = ep_url.replace("rtsp://", "http://")
                            elif ep_url.startswith("http://"):
                                proxy_url = ep_url
                            else:
                                proxy_url = str(request.base_url) + f"api/play?url={ep_url}"
                            rewritten_parts.append(f"{ep_name}${proxy_url}")
                    item_copy["vod_play_url"] = "#".join(rewritten_parts)
                detail_list.append(item_copy)
                continue
                
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
                    # 查询电影的真实名字和内容介绍
                    params_vod = {
                        "Action": "vodInfoById",
                        "vodId": vod_id
                    }
                    res_vod = sim.state.session.get(data_url, params=params_vod, headers=sim.config.headers, timeout=10)
                    vod_data = parse_epg_json(res_vod.text)
                    result_vod = vod_data.get("result", {})
                    
                    play_url = vod_id
                    name = result_vod.get("name") or f"{item_code} (电影)"
                    content = result_vod.get("introduce") or "热播大片专区"
                    
                    pic_url = poster_cache.get(current_id) or (str(request.base_url) + "pics/default_poster.jpg")
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
                    params_series = {
                        "Action": "seriesInfoById",
                        "seriseId": vod_id,
                        "posterflag": "2",
                        "displayflag": "1",
                        "posteridx": "1"
                    }
                    res_s = sim.state.session.get(data_url, params=params_series, headers=sim.config.headers, timeout=10)
                    s_data = parse_epg_json(res_s.text)
                    result_series = s_data.get("result", {})
                    
                    name = result_series.get("name") or f"{item_code} (电视剧)"
                    content = result_series.get("introduce") or "热播剧集专区"
                    episode_list = result_series.get("episodeList", [])
                    
                    ep_play_urls = []
                    for idx, ep in enumerate(episode_list):
                        ep_id = ep.get("id")
                        num_str = ep.get("num")
                        if num_str and num_str.isdigit():
                            ep_num = int(num_str)
                        else:
                            ep_num = idx + 1
                        
                        # Handle placeholder / missing episodes (EPG returns "缺" or non-digit IDs)
                        if not ep_id or ep_id == "缺" or "缺" in ep_id or not ep_id.isdigit():
                            ep_play_urls.append(f"第{ep_num}集(缺)$")
                        else:
                            play_url = ep_id
                            ep_play_urls.append(f"第{ep_num}集${play_url}")
                        
                    pic_url = poster_cache.get(current_id) or (str(request.base_url) + "pics/default_poster.jpg")
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
                if current_id in vods_fallback:
                    item_copy = dict(vods_fallback[current_id])
                    item_copy["vod_pic"] = poster_cache.get(current_id) or (str(request.base_url) + "pics/default_poster.jpg")
                    orig_play_url = item_copy.get("vod_play_url", "")
                    if orig_play_url:
                        parts = orig_play_url.split("#")
                        rewritten_parts = []
                        for part in parts:
                            sub_parts = part.split("$", 1)
                            if len(sub_parts) == 2:
                                ep_name, ep_url = sub_parts
                                ep_url = ep_url.strip()
                                if not ep_url:
                                    proxy_url = ""
                                elif ep_url.startswith("rtsp://"):
                                    proxy_url = ep_url.replace("rtsp://", "http://")
                                elif ep_url.startswith("http://"):
                                    proxy_url = ep_url
                                else:
                                    proxy_url = str(request.base_url) + f"api/play?url={ep_url}"
                                rewritten_parts.append(f"{ep_name}${proxy_url}")
                        item_copy["vod_play_url"] = "#".join(rewritten_parts)
                    detail_list.append(item_copy)
                    
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
                                "vod_pic": str(request.base_url) + "pics/default_poster.jpg",
                                "vod_remarks": "电影" if item_type == "vod" else "电视剧"
                            })
            except Exception as e:
                print(f"Live search failed: {e}. Falling back to local parsed DB search.")
                
            # 搜索兜底：在本地 600 个点播项中进行模糊名查找
            if not vod_list:
                results_local = []
                for item_id, item in vods_fallback.items():
                    if wd.lower() in item["vod_name"].lower():
                        results_local.append({
                            "vod_id": item_id,
                            "vod_name": item["vod_name"],
                            "vod_pic": str(request.base_url) + "pics/default_poster.jpg",
                            "vod_remarks": item["vod_remarks"]
                        })
                start_idx = (page - 1) * 20
                end_idx = page * 20
                vod_list = results_local[start_idx:end_idx]
                
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
            genre = request.query_params.get("genre", "")
            region = request.query_params.get("region", "")
            series_tag = request.query_params.get("series_tag", "")
            actor = request.query_params.get("actor", "")
            series_genre = request.query_params.get("series_genre", "")
            
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
                        if "sub_type" in f_json: sub_type = f_json["sub_type"]
                        if "genre" in f_json: genre = f_json["genre"]
                        if "region" in f_json: region = f_json["region"]
                        if "series_tag" in f_json: series_tag = f_json["series_tag"]
                        if "actor" in f_json: actor = f_json["actor"]
                        if "series_genre" in f_json: series_genre = f_json["series_genre"]
                        print(f">>>> [API] Parsed filters from 'f': {f_json}")
                except Exception as e:
                    print(f">>> [API] Failed to parse filter parameter 'f' ({f_param}): {e}")

            # Sub-filters take precedence over top-level sub_type
            resolved_sub = ""
            for val in [genre, region, series_tag, actor, series_genre]:
                if val:
                    resolved_sub = val
                    break
                    
            sub_filter_allowed_parents = {
                "", 
                "category_32798195",  # 全部剧集
                "category_89204884",  # 全部电影
                "category_00823570",  # 按地区
                "category_26400459",  # 系列电影1080P
                "category_45105530",  # 焦点影人1080P
            }
            if resolved_sub and sub_type in sub_filter_allowed_parents:
                target_cat = resolved_sub
            elif sub_type:
                target_cat = sub_type
            elif resolved_sub:
                target_cat = resolved_sub
            else:
                # Default mappings for top-level category IDs
                if t == "latest":
                    target_cat = "category_36870794"
                elif t == "movies":
                    target_cat = "category_89204884"
                elif t == "series":
                    target_cat = "category_32798195"
                elif t == "kids":
                    target_cat = "category_28090754"
                elif t == "variety":
                    target_cat = "category_99062130"
                elif t == "anime":
                    target_cat = "category_77371402"
                elif t == "documentary":
                    target_cat = "category_88700231"
                elif t == "zhejiang":
                    target_cat = "category_97521600"
                elif t == "opera":
                    target_cat = "category_85214466"
                else:
                    target_cat = t
                    
            vod_list = []
            try:
                vis_domain = "http://115.233.200.59:58007/epg/"
                
                # Route the live query for container nodes to their first child subcategory
                live_cat_route = {
                    "category_89204884": "category_96791097",  # 全部电影 -> 院线首映
                    "category_32798195": "category_19092137",  # 全部剧集 -> 电视剧子分类全部
                    "category_00823570": "category_91911554",  # 按地区 -> 华语
                    "category_26400459": "category_51059430",  # 系列电影1080P -> 变形金刚系列
                    "category_45105530": "category_44886379",  # 焦点影人1080P -> 王家卫
                    "category_99062130": "category_62468956",  # 热门综艺 -> Z视介/芒果综艺
                    "category_97521600": "category_34625597",  # 中国蓝专区 -> 浙江精选
                    "category_85214466": "category_20619150",  # 综合戏曲 -> 越剧专区
                    "category_88700231": "category_28343770",  # 纪录片库高清 -> 自然
                }
                query_cat = live_cat_route.get(target_cat, target_cat)
                
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
                        image_server = data.get("imageServer", "http://115.233.200.61:58001/pics")
                        
                        for item in result_set:
                            item_type = item.get("itemType", "vod")
                            # Skip non-playable assets (like links or subjects) which fail to play in TVBox
                            if item_type not in ["vod", "series"]:
                                continue
                                
                            title = item.get("title", "Unknown")
                            item_code = item.get("itemCode", "")
                            icon = item.get("itemIcon") or item.get("contentPictures", {}).get("poster1") or ""
                            pic_url = f"{image_server}{icon}" if icon and not icon.startswith("http") else icon
                            if not pic_url:
                                pic_url = str(request.base_url) + "pics/default_poster.jpg"
                            
                            item_id = f"{item_type}_{item_code}"
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
                print(f"Live category items fetch failed for {target_cat} (routed to {query_cat if 'query_cat' in locals() else target_cat}): {e}. Falling back to local parsed DB.")
                
            # 本地提取分类兜底
            fallback_cat = target_cat
            if fallback_cat not in list_by_cat_fallback:
                if fallback_cat in MOVIE_CATEGORY_IDS:
                    fallback_cat = "category_89204884"
                elif fallback_cat in SERIES_CATEGORY_IDS:
                    fallback_cat = "category_32798195"
                    
            if fallback_cat in list_by_cat_fallback:
                local_list = list_by_cat_fallback[fallback_cat]
                start_idx = (page - 1) * 20
                end_idx = page * 20
                page_items = local_list[start_idx:end_idx]
                return JSONResponse(content={
                    "code": 1,
                    "page": page,
                    "pagecount": (len(local_list) // 20) + 1,
                    "limit": 20,
                    "total": len(local_list),
                    "list": [{
                        "vod_id": item["vod_id"],
                        "vod_name": item["vod_name"],
                        "vod_pic": str(request.base_url) + "pics/default_poster.jpg",
                        "vod_remarks": item["vod_remarks"]
                    } for item in page_items]
                })

            return JSONResponse(content={"code": 1, "page": page, "pagecount": 1, "limit": 20, "total": 0, "list": []})

    # ------------------------------------------
    # 场景 3: 初始化请求，返回顶级分类
    # ------------------------------------------
    else:
        vis_categories = [
            {"type_id": "latest", "type_name": "最新推荐"},
            {"type_id": "movies", "type_name": "电影专区"},
            {"type_id": "series", "type_name": "电视剧场"},
            {"type_id": "kids", "type_name": "少儿动漫"},
            {"type_id": "variety", "type_name": "综艺娱乐"},
            {"type_id": "anime", "type_name": "动漫世界"},
            {"type_id": "documentary", "type_name": "纪录片场"},
            {"type_id": "zhejiang", "type_name": "中国蓝专区"},
            {"type_id": "opera", "type_name": "戏曲专栏"}
        ]
        
        movies_filters = []
        try:
            if os.path.exists(MOVIES_FILTERS_FILE):
                with open(MOVIES_FILTERS_FILE, "r", encoding="utf-8") as f:
                    movies_filters = json.load(f)
        except Exception as e:
            print(f"Error loading movies_filters.json: {e}")
            
        if not movies_filters:
            movies_filters = [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部电影", "v": ""},
                        {"n": "4K大片", "v": "category_82023212"},
                        {"n": "院线首映", "v": "category_96791097"},
                        {"n": "好莱坞巨制", "v": "category_80454289"},
                        {"n": "按地区", "v": "category_00823570"},
                        {"n": "系列电影", "v": "category_26400459"},
                        {"n": "焦点影人", "v": "category_45105530"},
                        {"n": "重磅推荐", "v": "category_18147166"}
                    ]
                }
            ]
            
        series_filters = []
        try:
            if os.path.exists(SERIES_FILTERS_FILE):
                with open(SERIES_FILTERS_FILE, "r", encoding="utf-8") as f:
                    series_filters = json.load(f)
        except Exception as e:
            print(f"Error loading series_filters.json: {e}")
            
        if not series_filters:
            series_filters = [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部剧集", "v": ""},
                        {"n": "最新上线", "v": "category_36870794"},
                        {"n": "卫视同步", "v": "category_92376156"},
                        {"n": "高分好剧", "v": "category_53224763"},
                        {"n": "1080", "v": "category_18008093"},
                        {"n": "古装", "v": "category_82205358"},
                        {"n": "谍战", "v": "category_86585701"},
                        {"n": "偶像", "v": "category_33770041"},
                        {"n": "都市", "v": "category_42484986"},
                        {"n": "TVB", "v": "category_52215058"},
                        {"n": "年代", "v": "category_88074906"},
                        {"n": "罪案", "v": "category_94892066"},
                        {"n": "亚洲", "v": "category_54399439"},
                        {"n": "欧美", "v": "category_88274503"},
                        {"n": "其它", "v": "category_77510266"}
                    ]
                }
            ]
            
        vis_filters = {
            "movies": movies_filters,
            "series": series_filters,
            "kids": [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "全部少儿", "v": "category_28090754"},
                        {"n": "宝贝推荐", "v": "category_96441951"},
                        {"n": "热门动画", "v": "category_99426513"},
                        {"n": "精彩专题", "v": "category_16723568"},
                        {"n": "宝贝学堂", "v": "category_82672692"},
                        {"n": "口碑少儿栏目", "v": "category_21267526"}
                    ]
                }
            ],
            "variety": [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "热门综艺", "v": "category_62468956"},
                        {"n": "独家策划", "v": "category_12738781"},
                        {"n": "精彩看点", "v": "category_93176058"}
                    ]
                }
            ],
            "anime": [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "全部动漫", "v": "category_77371402"},
                        {"n": "新番上线", "v": "category_03824847"},
                        {"n": "热播动漫", "v": "category_19100400"},
                        {"n": "国漫精选", "v": "category_82215916"},
                        {"n": "日漫经典", "v": "category_36201848"},
                        {"n": "其他动漫", "v": "category_97885024"},
                        {"n": "动漫IP", "v": "category_82114042"}
                    ]
                }
            ],
            "documentary": [
                {
                    "key": "sub_type",
                    "name": "题材",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "自然", "v": "category_28343770"},
                        {"n": "动物", "v": "category_10329781"},
                        {"n": "美食", "v": "category_29507778"},
                        {"n": "人文", "v": "category_25293790"},
                        {"n": "历史", "v": "category_72107823"},
                        {"n": "社会", "v": "category_15966618"},
                        {"n": "新时代", "v": "category_33701197"},
                        {"n": "探险", "v": "category_26813153"},
                        {"n": "科技", "v": "category_78669433"},
                        {"n": "免费栏目", "v": "category_23729098"}
                    ]
                }
            ],
            "zhejiang": [
                {
                    "key": "sub_type",
                    "name": "分类",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "中国蓝专区", "v": "category_97521600"}
                    ]
                }
            ],
            "opera": [
                {
                    "key": "sub_type",
                    "name": "戏种",
                    "value": [
                        {"n": "全部", "v": ""},
                        {"n": "越剧", "v": "category_20619150"},
                        {"n": "京剧", "v": "category_67364460"},
                        {"n": "昆剧", "v": "category_11928373"},
                        {"n": "黄梅戏", "v": "category_34419661"}
                    ]
                }
            ]
        }
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
            data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"
            
            # 1. 模拟鉴权动作 (Action=serviceAuth)
            params_auth = {
                "Action": "serviceAuth",
                "progId": vod_id,
                "contentType": "1"
            }
            sim.state.session.get(data_url, params=params_auth, headers=sim.config.headers, timeout=10)
            
            # 2. 获取真正的单播播放地址 (Action=vodInfoById)
            params_info = {
                "Action": "vodInfoById",
                "vodId": vod_id
            }
            res = sim.state.session.get(data_url, params=params_info, headers=sim.config.headers, timeout=10)
            data = parse_epg_json(res.text)
            media_url = data.get("result", {}).get("mediaUrl")
            
            if media_url:
                clean_url = media_url.split("?")[0]
                target_url = clean_url
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

@app.get("/pics/default_poster.jpg")
async def get_default_poster():
    file_path = "default_poster.png"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="image/png")
    return Response(status_code=404)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)