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


def set_simulator(sim):
    """设置全局模拟器实例（在 main.py 启动时调用）。"""
    global _simulator
    _simulator = sim


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

_IS_FINISHED_FILTER = {
    "key": "isfinished",
    "name": "状态",
    "value": [
        {"n": "全部", "v": ""},
        {"n": "更新中", "v": "0"},
        {"n": "已完结", "v": "1"},
    ]
}

FILTER_CONFIG = {
    "movies":      [_COUNTRY_FILTER, _YEAR_FILTER],
    "series":      [_COUNTRY_FILTER, _YEAR_FILTER, _IS_FINISHED_FILTER],
    "variety":     [_COUNTRY_FILTER, _YEAR_FILTER, _IS_FINISHED_FILTER],
    "anime":       [_COUNTRY_FILTER, _YEAR_FILTER, _IS_FINISHED_FILTER],
    "kids":        [_COUNTRY_FILTER, _YEAR_FILTER],
    "documentary": [_COUNTRY_FILTER, _YEAR_FILTER],
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
    cfg_ver = str(int(time.time()))

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
    sort = request.query_params.get("sort", "score")
    wd = request.query_params.get("wd", "")
    ids = request.query_params.get("ids", "")
    f_param = request.query_params.get("f", "")

    page = int(pg) if pg.isdigit() else 1
    logger.info("[TVBox] ac=%s, t=%s, pg=%s, sort=%s, wd=%s, ids=%s, f=%s", ac, t, pg, sort, wd, ids, f_param[:100] if f_param else "")

    sim = get_simulator()

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
        result = filter_items(t, filters, page, sort=sort)
        result["filters"] = {t: FILTER_CONFIG.get(t, [])}
        return JSONResponse(content={"code": 1, **result})

    # ---------- 场景 4: 初始化 - 返回顶级分类和过滤器 ----------
    vis_categories = []
    vis_filters = {}

    for cat_id, cat_filters in FILTER_CONFIG.items():
        name_map = {
            "movies": "电影专区", "series": "电视剧场",
            "variety": "综艺荟萃", "anime": "动漫世界", "kids": "少儿天地",
            "documentary": "纪录大观"
        }
        vis_categories.append({"type_id": cat_id, "type_name": name_map.get(cat_id, cat_id)})
        vis_filters[cat_id] = cat_filters

    return JSONResponse(content={"code": 1, "class": vis_categories, "filters": vis_filters})


async def _handle_detail(ids: str, sim) -> JSONResponse:
    """处理视频详情请求（实时从 EPG 解析播放地址）。"""
    from src.utils.helpers import parse_epg_json
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
            ensure_authenticated(sim, lambda: None)  # 登录由外层保证
            if not sim.state.is_authenticated:
                continue

            data_url = f"{sim.state.epg_base_url}/EPG/jsp/gdhdpublic/Ver.2/common/data.jsp"

            # A. contentCode -> vod_id
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

            # B. 电影资源
            if item_type == "vod":
                result_vod = sim.get_vod_info(vod_id) or {}
                play_url = vod_id
                name = result_vod.get("name") or f"{item_code} (电影)"
                content = result_vod.get("introduce") or "热播大片专区"

                db_item = get_item_by_code(item_code)
                pic_url = (db_item.get("icon") or db_item.get("poster") or "") if db_item else ""
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

            # C. 电视剧资源
            elif item_type == "series":
                series_info = sim.get_series_info(vod_id)
                if not series_info:
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

                    telecom_code = ep.get("telecom_code", "")
                    if telecom_code:
                        play_url = f"{ep_id}${telecom_code}"
                    else:
                        play_url = ep_id
                    ep_play_urls.append(f"第{num_str}集${play_url}")

                db_item = get_item_by_code(item_code)
                pic_url = (db_item.get("icon") or db_item.get("poster") or "") if db_item else ""
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
            logger.error("[TVBox] 详情查询失败 for %s: %s", current_id, e)

    return JSONResponse(content={"code": 1, "list": detail_list})
