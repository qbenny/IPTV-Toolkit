"""
TVBox 协议接口模块 - 对接 TVBox 的 API 请求，从本地 SQLite 查询数据。
"""
import base64
import json
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from src.db.crud import filter_items, search_items, get_stats, get_item_by_code
from src.utils.logger import logger

# 模拟器实例（在 main.py 启动时注入）
_simulator = None
_login_func = lambda: None


def set_simulator(sim):
    """设置全局模拟器实例（在 main.py 启动时调用）。"""
    global _simulator
    _simulator = sim


def set_login_func(login_fn):
    """设置全局登录回调函数。"""
    global _login_func
    _login_func = login_fn


def get_simulator():
    """获取全局模拟器实例。"""
    return _simulator


# ==========================================
# 过滤器配置（硬编码）
# ==========================================

# 区域和年份过滤器模板
_COUNTRY_FILTER = {
    "key": "country",
    "name": "地区",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "内地", "v": "内地"},
        {"n": "中国香港", "v": "中国香港"},
        {"n": "中国台湾", "v": "中国台湾"},
        {"n": "美国", "v": "美国"},
        {"n": "日本", "v": "日本"},
        {"n": "韩国", "v": "韩国"},
        {"n": "英国", "v": "英国"},
        {"n": "其他", "v": "其他"}
    ]
}

_YEAR_FILTER = {
    "key": "year",
    "name": "年份",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "2026", "v": "2026"},
        {"n": "2025", "v": "2025"},
        {"n": "2024", "v": "2024"},
        {"n": "2023", "v": "2023"},
        {"n": "2020-2029", "v": "2020-2029"},
        {"n": "2010-2019", "v": "2010-2019"},
        {"n": "2000-2009", "v": "2000-2009"},
        {"n": "更早", "v": "1900-1999"}
    ]
}

# 画质过滤器（基于标题中 HD/4K 前缀识别）
_QUALITY_FILTER = {
    "key": "quality",
    "name": "画质",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "4K", "v": "4K"},
        {"n": "HD", "v": "HD"},
        {"n": "SD", "v": "SD"},
    ]
}

# 纪录片分类过滤器（2548/0602/bastag11577/bastag12849 已核实命名）
_DOC_SUB_TYPE_FILTER = {
    "key": "sub_type",
    "name": "分类",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "军事", "v": "0602"},
        {"n": "自然", "v": "0604"},
        {"n": "动物", "v": "0612"},
        {"n": "美食", "v": "0615"},
        {"n": "人文", "v": "0627"},
        {"n": "历史", "v": "0603"},
        {"n": "社会", "v": "0610"},
        {"n": "探险", "v": "0606"},
        {"n": "科技", "v": "0607"},
        {"n": "灾难", "v": "2548"},
        {"n": "时政", "v": "bastag11577"},
        {"n": "央视", "v": "bastag12849"},
    ]
}

# 综艺分类过滤器
_VARIETY_SUB_TYPE_FILTER = {
    "key": "sub_type",
    "name": "分类",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "晚会盛典", "v": "0411"},
        {"n": "真人秀场", "v": "bastag2081"},
        {"n": "幽默搞笑", "v": "0402"},
        {"n": "户外竞技", "v": "0412"},
        {"n": "文化国潮", "v": "0428"},
        {"n": "情感观察", "v": "0401"},
        {"n": "推理辩论", "v": "0415"},
        {"n": "职场竞演", "v": "2547"}
    ]
}

# 新闻栏目过滤器（contentType='series' 的连续播出节目，共 19 条，最有浏览价值）
_NEWS_COLUMN_FILTER = {
    "key": "col_type",
    "name": "新闻栏目",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "新闻栏目", "v": "series"},
    ]
}

# 新闻分类过滤器（子标签 ID 来自 VIS contentBaseTags，已用真实库验证）
#   0203 时政(2778) / 0212 社会(570) 为文档 §五 已确认中文名的主要分类
_NEWS_SUB_TYPE_FILTER = {
    "key": "sub_type",
    "name": "分类",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "军事", "v": "0203"},
        {"n": "社会", "v": "0204"},
        {"n": "科技", "v": "0205"},
        {"n": "财经", "v": "0206"},
        {"n": "法治", "v": "0207"},
        {"n": "资讯", "v": "0212"},
    ]
}

# 体育分类过滤器（0512 游戏(569) / 0513 赛事(17)，真实库验证）
_SPORTS_SUB_TYPE_FILTER = {
    "key": "sub_type",
    "name": "分类",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "足球", "v": "0501"},
        {"n": "篮球", "v": "0502"},
        {"n": "网球", "v": "0503"},
        {"n": "游戏", "v": "0512"},
        {"n": "棋牌", "v": "0513"},
        {"n": "健康", "v": "bastag12413"},
        {"n": "钓鱼", "v": "bastag_18495"},
        {"n": "滑雪", "v": "bastag_18883"},
    ]
}

# 体育内容形式过滤器（contentType 精确 50/50：series 栏目 / vod 单集，真实库验证各 732 条）
_SPORTS_FORM_FILTER = {
    "key": "col_type",
    "name": "内容形式",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "栏目", "v": "series"},
        {"n": "单集", "v": "vod"},
    ]
}

# 戏曲分类过滤器（真实库验证）
_OPERA_SUB_TYPE_FILTER = {
    "key": "sub_type",
    "name": "分类",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "京剧", "v": "1501"},
        {"n": "豫剧", "v": "1507"},
        {"n": "越剧", "v": "1508"},
        {"n": "黄梅戏", "v": "1509"},
        {"n": "昆曲", "v": "bastag7295"},
        {"n": "婺剧", "v": "bastag12453"},
        {"n": "绍剧", "v": "bastag9553"},
    ]
}

# 戏曲内容形式过滤器（contentType：series 栏目(24) / vod 单集(1558)，真实库验证）
_OPERA_FORM_FILTER = {
    "key": "col_type",
    "name": "内容形式",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "栏目", "v": "series"},
        {"n": "单集", "v": "vod"},
    ]
}

# 排序过滤器
_SORT_FILTER = {
    "key": "sort",
    "name": "排序",
    "value": [
        {"n": "按最新", "v": "new"},
        {"n": "按评分", "v": "score"},
        {"n": "按年份", "v": "time"},
    ]
}

FILTER_CONFIG = {
    "movies":      [_COUNTRY_FILTER, _YEAR_FILTER, _QUALITY_FILTER, _SORT_FILTER],
    "series":      [_COUNTRY_FILTER, _YEAR_FILTER, _QUALITY_FILTER, _SORT_FILTER],
    "variety":     [_VARIETY_SUB_TYPE_FILTER, _YEAR_FILTER, _QUALITY_FILTER, _SORT_FILTER],
    "anime":       [_COUNTRY_FILTER, _YEAR_FILTER, _QUALITY_FILTER, _SORT_FILTER],
    "kids":        [_COUNTRY_FILTER, _YEAR_FILTER, _SORT_FILTER],
    "documentary": [_DOC_SUB_TYPE_FILTER, _YEAR_FILTER, _QUALITY_FILTER, _SORT_FILTER],
    # 新增大类：子标签过滤器待全量同步后实测 contentBaseTags 再补
    "opera":       [_OPERA_SUB_TYPE_FILTER, _OPERA_FORM_FILTER, _YEAR_FILTER],
    "sports":      [_SPORTS_SUB_TYPE_FILTER, _SPORTS_FORM_FILTER, _YEAR_FILTER, _SORT_FILTER],
    "news":        [_NEWS_SUB_TYPE_FILTER, _NEWS_COLUMN_FILTER, _YEAR_FILTER, _SORT_FILTER],
    "other":       [_COUNTRY_FILTER, _YEAR_FILTER, _SORT_FILTER],
}


def _parse_f_param(f_param: str) -> dict:
    """解析 TVBox 的 f 参数。
    
    TVBox 的 f 参数格式通常为：
        - Base64 编码的 JSON 字符串: {"country": "内地"}
        - 或原始 JSON 字典: {"key": "value"}
        - 或 JSON 数组: [{"key":"country","value":"内地"}]
    """
    if not f_param:
        return {}
    
    logger.info("[TVBox] Raw 'f' param: %s", f_param[:200])
    
    def _try_parse(raw: str) -> dict:
        """尝试解析一段 JSON 文本，统一转为扁平 dict。"""
        try:
            obj = json.loads(raw)
        except Exception:
            return {}
        # 数组格式: [{key, value}, ...] → {key: value, ...}
        if isinstance(obj, list):
            result = {}
            for item in obj:
                if isinstance(item, dict) and "key" in item and "value" in item:
                    result[item["key"]] = item["value"]
            return result
        # 字典格式: {"key": "value"} → 直接使用
        if isinstance(obj, dict):
            return obj
        return {}

    # 1) 尝试 Base64 解码
    try:
        padded = f_param + "=" * ((4 - len(f_param) % 4) % 4)
        decoded_str = base64.b64decode(padded).decode("utf-8")
        result = _try_parse(decoded_str)
        if result:
            logger.info("[TVBox] Parsed filters (base64→dict): %s", result)
            return result
    except Exception:
        pass

    # 2) 尝试原始 JSON
    result = _try_parse(f_param)
    if result:
        logger.info("[TVBox] Parsed filters (raw→dict): %s", result)
        return result

    logger.info("[TVBox] Could not parse 'f' param, returning empty filters")
    return {}


# ---- TVBox Config ----

async def get_tvbox_config(request: Request) -> JSONResponse:
    """返回 TVBox 配置（/zjvod 接口）。"""
    api_url = str(request.base_url) + "api/vod"

    config_data = {
        "sites": [
            {
                "key": "Telecom_VOD",
                "name": "浙江电信点播",
                "type": 1,
                "api": api_url,
                "playUrl": "json:" + str(request.base_url) + "api/play?vod_id=",
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 1,
                # categories：客户端本地过滤显示的分类（按显示名匹配 type_name）。
                # 不包含「其他」，TVBox 将自动隐藏该分类。
                "categories": [
                    "电影专区", "电视剧场", "综艺荟萃", "动漫世界",
                    "少儿天地", "纪录大观", "戏曲天地", "体育竞技", "新闻速递"
                ]
            }
        ]
    }
    return JSONResponse(content=config_data)


# ---- TVBox Request Handler ----

async def handle_tvbox_request(request: Request) -> JSONResponse:
    """TVBox 协议主入口。

    支持的参数：
        ac=list&t=xxx  — 分类列表（查 SQLite）
        ac=detail&ids=xxx  — 详情（实时解析播放地址）
        ac=list&wd=xxx  — 搜索（查 SQLite）
        f=Base64(JSON)  — 多条件过滤
    """
    ac = request.query_params.get("ac", "")
    t = request.query_params.get("t", "")
    pg = request.query_params.get("pg", "1")
    sort = request.query_params.get("sort", "new")
    wd = request.query_params.get("wd", "")
    ids = request.query_params.get("ids", "")
    f_param = request.query_params.get("f", "")

    page = int(pg) if pg.isdigit() else 1
    logger.info("[TVBox] ac=%s, t=%s, pg=%s, sort=%s, wd=%s, ids=%s, f=%s", ac, t, pg, sort, wd, ids, f_param[:100] if f_param else "")

    sim = get_simulator()
    if sim:
        from src.auth.heartbeat import ensure_authenticated
        ensure_authenticated(sim, _login_func)

    # ---------- 场景 1: 获取视频详情 ----------
    if ac == "detail" and ids:
        return await _handle_detail(ids, sim)

    # ---------- 场景 2: 搜索 ----------
    if wd:
        result = search_items(wd, page, sort=sort)
        return JSONResponse(content={"code": 1, **result})

    # ---------- 场景 3: 分类列表 ----------
    if t:
        filters = _parse_f_param(f_param)
        current_sort = filters.get("sort", sort)
        result = filter_items(t, filters, page, sort=current_sort)
        result["filters"] = {t: FILTER_CONFIG.get(t, [])}
        return JSONResponse(content={"code": 1, **result})

    # ---------- 场景 4: 初始化 - 返回顶级分类、过滤器与推荐列表 ----------
    vis_categories = []
    vis_filters = {}
    rec_list = []

    for cat_id, cat_filters in FILTER_CONFIG.items():
        name_map = {
            "movies": "电影专区", "series": "电视剧场",
            "variety": "综艺荟萃", "anime": "动漫世界", "kids": "少儿天地",
            "documentary": "纪录大观",
            "opera": "戏曲天地", "sports": "体育竞技", "news": "新闻速递", "other": "其他"
        }
        vis_categories.append({"type_id": cat_id, "type_name": name_map.get(cat_id, cat_id)})
        vis_filters[cat_id] = cat_filters

    # 首页推荐列表：按评分降序取高分内容，优先电影/电视剧/综艺/动漫
    rec_list = _build_recommend_list()

    return JSONResponse(content={
        "code": 1,
        "class": vis_categories,
        "filters": vis_filters,
        "list": rec_list
    })


def _quality_remark(name: str) -> str:
    """根据标题判断画质备注：含 4K→4K，含 HD→高清，否则→标清。"""
    if not name:
        return "标清"
    upper = name.upper()
    if "4K" in upper:
        return "4K"
    if "HD" in upper:
        return "高清"
    return "标清"


def _build_recommend_list() -> list:
    """构建首页推荐列表。

    直接复用分类列表 filter_items（含 m3u8 池屏蔽 + 低质量过滤、new 排序、
    vod_id/remarks 拼装），仅在此基础上按分类各取候选后做同名去重
    （保留最高画质 4K>高清>标清）并按配额截取，返回 TVBox 初始化接口所需的 list 格式。
    """
    import re

    # 首页主推 4 大类（TVBox 分类 id → 每类数量），顺序：电视剧/电影/综艺/纪录
    priority = [("series", 7), ("movies", 7), ("variety", 7), ("documentary", 7)]

    # 画质优先级：值越小优先级越高
    _QUALITY_ORDER = {"4K": 0, "高清": 1, "标清": 2}

    def _dedup(items):
        """同名去重，保留最高画质（4K>高清>标清），保持原出现顺序。"""
        seen = {}
        out = []
        for it in items:
            clean = re.sub(r'^(4K|HD|SD)[-–—]?\s*|\s*[-–—]?(4K|HD|SD)$', '', it["vod_name"])
            q = _QUALITY_ORDER.get(_quality_remark(it["vod_name"]), 99)
            if clean not in seen or q < seen[clean][1]:
                if clean in seen:
                    out.remove(seen[clean][0])
                seen[clean] = (it, q)
                out.append(it)
        return out

    rec = []
    for cat_id, limit in priority:
        # 复用分类列表逻辑取候选（多取以容纳去重后仍满足配额）
        result = filter_items(cat_id, filters=None, page=1, page_size=limit * 4, sort="new")
        items = result.get("list", [])
        rec.extend(_dedup(items)[:limit])

    return rec


async def _handle_detail(ids: str, sim) -> JSONResponse:
    """处理视频详情请求（实时从 EPG 解析播放地址）。被顶号自愈由 simulator 层统一处理。"""
    from src.auth.heartbeat import ensure_authenticated

    if sim is None:
        return JSONResponse(content={"code": 0, "list": []})

    id_list = ids.split(",")
    detail_list = []

    for current_id in id_list:
        parts = current_id.split("_", 1)
        if len(parts) != 2:
            continue
        item_type, item_code = parts

        try:
            ensure_authenticated(sim, _login_func)
            if not sim.state.is_authenticated:
                continue

            # A. contentCode -> vod_id（被顶号时由 simulator 层自动清状态重登录重试）
            vod_id = sim.get_vod_id_by_code(item_code)
            if not vod_id:
                continue
            vod_id = str(vod_id)

            # B. 电影资源
            if item_type == "vod":
                result_vod = sim.get_vod_info(vod_id) or {}
                play_url = vod_id
                name = result_vod.get("name") or f"{item_code} (电影)"
                content = result_vod.get("introduce") or "热播大片专区"

                db_item = get_item_by_code(item_code)
                pic_url = (db_item.get("poster") or db_item.get("icon") or "") if db_item else ""
                detail_list.append({
                    "vod_id": current_id,
                    "vod_name": name,
                    "vod_pic": pic_url,
                    "type_name": db_item.get("type", "") if db_item else "",
                    "vod_content": content,
                    "vod_year": db_item.get("year", "") if db_item else "",
                    "vod_area": db_item.get("country", "") if db_item else "",
                    "vod_actor": db_item.get("actors", "") if db_item else "",
                    "vod_director": db_item.get("director", "") if db_item else "",
                    "vod_play_from": "电信专线",
                    "vod_play_url": f"播放${play_url}",
                    "vod_remarks": _quality_remark(name)
                })

            # C. 电视剧/多集资源
            elif item_type == "series":
                series_info = sim.get_series_info(vod_id)
                # 若 EPG 返回 0 集，则该内容实际是单视频(VOD)，回退到 vod 模式
                if series_info and not series_info.get("episodes"):
                    logger.debug("[TVBox] series 无剧集，回退 vod 模式: %s", item_code)
                    series_info = None

                if not series_info:
                    # 回退为 VOD：直接用 vod_id 请求播放地址
                    db_item = get_item_by_code(item_code)
                    pic_url = (db_item.get("poster") or db_item.get("icon") or "") if db_item else ""
                    name = db_item.get("title") or item_code
                    detail_list.append({
                        "vod_id": current_id,
                        "vod_name": name,
                        "vod_pic": pic_url,
                        "type_name": db_item.get("type", "") if db_item else "",
                        "vod_content": "热播专区",
                        "vod_year": db_item.get("year", "") if db_item else "",
                        "vod_area": db_item.get("country", "") if db_item else "",
                        "vod_actor": db_item.get("actors", "") if db_item else "",
                        "vod_director": db_item.get("director", "") if db_item else "",
                        "vod_play_from": "电信专线",
                        "vod_play_url": f"播放${vod_id}",
                        "vod_remarks": _quality_remark(name)
                    })
                    continue

                name = series_info.get("name") or f"{item_code} (电视剧)"
                content = series_info.get("introduce") or "热播剧集专区"
                episode_list = series_info.get("episodes", [])

                total_count = len(episode_list)
                valid_count = sum(
                    1 for ep in episode_list
                    if ep.get("id") and str(ep.get("id")) != "缺"
                    and "缺" not in str(ep.get("id"))
                    and str(ep.get("id")).isdigit()
                )
                use_original_num = (valid_count == total_count)

                ep_play_urls = []
                display_num = 0
                for ep in episode_list:
                    ep_id = ep.get("id")
                    if not ep_id or ep_id == "缺" or "缺" in ep_id or not ep_id.isdigit():
                        continue

                    display_num += 1
                    num_str = ep.get("num") if use_original_num else str(display_num)
                    if not num_str or not num_str.isdigit():
                        num_str = str(display_num)

                    ep_play_urls.append(f"第{num_str}集${ep_id}")

                db_item = get_item_by_code(item_code)
                pic_url = (db_item.get("poster") or db_item.get("icon") or "") if db_item else ""
                detail_list.append({
                    "vod_id": current_id,
                    "vod_name": name,
                    "vod_pic": pic_url,
                    "type_name": db_item.get("type", "") if db_item else "",
                    "vod_content": content,
                    "vod_year": db_item.get("year", "") if db_item else "",
                    "vod_area": db_item.get("country", "") if db_item else "",
                    "vod_actor": db_item.get("actors", "") if db_item else "",
                    "vod_director": db_item.get("director", "") if db_item else "",
                    "vod_play_from": "电信专线",
                    "vod_play_url": "#".join(ep_play_urls),
                    "vod_remarks": (f"{_quality_remark(name)} | 更新至{len(ep_play_urls)}集"
                                    if ep_play_urls else _quality_remark(name))
                })

        except Exception as e:
            logger.error("[TVBox] 详情查询失败 for %s: %s", current_id, e)

    return JSONResponse(content={"code": 1, "list": detail_list})
