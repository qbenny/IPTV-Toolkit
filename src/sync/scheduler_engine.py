"""
定时同步调度器（A+E+B+D）。

- 直播同步：每天 live_sync_hour（默认 0）触发；已到钟点且进程已登录则顺带完成
  （零额外顶号），未登录则到 live_sync_hour 强制登录兜底。严格按配置钟点执行，
  不会在 00:00（新的一天）因 token 仍有效而提前触发。
- VOD / EPG 同步：免登录（login_func=None，只打公共接口），分别在 vod_sync_hour /
  epg_sync_hour（默认均为 1）触发；二者经互斥门顺序执行（VOD 先于 EPG），
  同一时刻只有一个线程池在跑，避免并发压服务器。
- 失败处理（D）：每小时重试一次，当日重试次数封顶（VOD/EPG=4，直播登录=3）后放弃当天。
- 可配置（B）：三个钟点存于 live_config，调度器每次轮询动态读取。

进程重启后必然触发一次启动登录（main.py lifespan），故 vis_base_url 不丢失；
即便启动登录失败，VIS 不可达期间定时同步会按退避重试，待恢复后自动补齐。
"""
import threading
import time
from datetime import date, datetime

from src.db.models import get_db_connection
from src.utils.logger import logger
from src.api.live import run_live_sync, live_sync_status
from src.db.config_store import cfg_get, cfg_bulk_set
from src.sync.filter_sync import start_sync_background, sync_status
from src.sync.epg_sync import start_epg_sync
from src.sync.epg_status import epg_sync_status

# 当日重试上限
LIVE_LOGIN_CAP = 3     # 直播“强制登录兜底”的登录尝试上限（防 VIS 长时间不可达被 BAN）
LIVE_RETRY_CAP = 4     # 直播同步（含登录后）总尝试上限
VOD_RETRY_CAP = 4      # VOD 同步总尝试上限
EPG_RETRY_CAP = 4      # EPG 同步总尝试上限

_scheduler_thread = None
_stop = False
_sim = None
_login_func = None
_last_day = None

_state = {
    "live": {
        "done_today": False, "gave_up": False, "attempts": 0,
        "login_attempts": 0, "retrying": False, "next_retry_hour": None,
        "pending": False, "saw_running": False, "last_result": None,
    },
    "vod": {
        "done_today": False, "gave_up": False, "attempts": 0,
        "retrying": False, "next_retry_hour": None,
        "pending": False, "saw_running": False,
    },
    "epg": {
        "done_today": False, "gave_up": False, "attempts": 0,
        "retrying": False, "next_retry_hour": None,
        "pending": False, "saw_running": False,
    },
}


def start_scheduler(sim, login_func):
    """启动调度器后台线程（幂等）。总开关关闭时整个定时模块不启动。"""
    global _scheduler_thread, _sim, _login_func, _stop
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _sim = sim
    _login_func = login_func
    # 总开关关闭则整个定时模块不启动（进程重启时若 DB 为关闭状态也不启动线程）
    if not _cfg_bool("scheduler_enabled", True):
        logger.info("[Scheduler] 总开关关闭，调度器不启动")
        return
    _stop = False
    _scheduler_thread = threading.Thread(target=_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("[Scheduler] 定时同步调度器已启动（live=%s, vod=%s, epg=%s）",
                _cfg_int("live_sync_hour", 0), _cfg_int("vod_sync_hour", 1), _cfg_int("epg_sync_hour", 1))


def stop_scheduler():
    """停止调度器。"""
    global _stop
    _stop = True


def apply_scheduler_enabled(enabled: bool):
    """根据总开关动态启停调度器线程（整个定时模块开关）。

    开启 -> start_scheduler 启动线程；关闭 -> stop_scheduler 线程退出。
    """
    if enabled:
        start_scheduler(_sim, _login_func)
    else:
        stop_scheduler()


def save_scheduler_config(configs: dict):
    """写入定时模块配置，并按总开关动态启停调度器。

    供 PUT /api/scheduler/config 调用。配置仍落在 live_config（第 3 步拆表前）。
    """
    cfg_bulk_set(configs, "scheduler_config")
    if "scheduler_enabled" in configs:
        enabled = str(configs["scheduler_enabled"]).strip().lower() in ("1", "true", "yes", "on", "y")
        apply_scheduler_enabled(enabled)


# ---- 配置读取 ----

def _cfg_int(key: str, default: int) -> int:
    try:
        return int(cfg_get(key, default, "scheduler_config"))
    except (ValueError, TypeError):
        return default


def _cfg_bool(key: str, default: bool = True) -> bool:
    """读取 live_config 中的布尔开关，默认开启（True）。

    取值 '1'/'true'/'yes'/'on'/'y'（不区分大小写）视为开启，其余为关闭；
    缺省时返回 default，保证未配置时行为与旧版一致（全部开启）。
    """
    v = cfg_get(key, None, "scheduler_config")
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


# ---- 当日完成判定 ----

def _live_synced_today() -> bool:
    """读 live_channels.MAX(synced_at) 判断今天是否已直播同步（兼容手动触发）。"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT MAX(synced_at) AS m FROM live_channels WHERE source = 'server'")
        row = c.fetchone()
        conn.close()
        ts = row["m"] if row and row["m"] else 0
        return date.fromtimestamp(ts) == date.today() if ts else False
    except Exception:
        return False


def _db_sync_time(name: str) -> int:
    """查 DB 中某类型最近一次成功同步的时间戳（vod/epg 全量同步会写入对应时间列）。"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if name == "vod":
            c.execute("SELECT MAX(syncedAt) AS m FROM vod_items")
        elif name == "epg":
            c.execute("SELECT MAX(synced_at) AS m FROM epg_programs")
        else:
            conn.close()
            return 0
        row = c.fetchone()
        conn.close()
        return row["m"] if row and row["m"] else 0
    except Exception:
        return 0


def _date_of(ts: int) -> date:
    return date.fromtimestamp(ts) if ts else None


# ---- 主循环 ----

def _loop():
    while not _stop:
        try:
            _tick()
        except Exception as e:
            logger.error("[Scheduler] tick 异常: %s", e, exc_info=True)
        # 每分钟轮询（可被 _stop 中断）
        for _ in range(60):
            if _stop:
                break
            time.sleep(1)


def _tick():
    global _last_day
    today = date.today()
    if _last_day != today:
        _reset_day()
        _last_day = today

    # 总开关：定时同步整体关闭时直接跳过（无需重启进程，开关即时生效）
    if not _cfg_bool("scheduler_enabled", True):
        return

    now = datetime.now()
    live_hour = _cfg_int("live_sync_hour", 0)
    vod_hour = _cfg_int("vod_sync_hour", 1)
    epg_hour = _cfg_int("epg_sync_hour", 1)

    if _cfg_bool("live_sync_enabled", True):
        _check_live(now, live_hour)
    if _cfg_bool("vod_sync_enabled", True):
        _check_vod(now, today, vod_hour)
    if _cfg_bool("epg_sync_enabled", True):
        _check_epg(now, today, epg_hour)


def _reset_day():
    for name in ("live", "vod", "epg"):
        st = _state[name]
        st["done_today"] = False
        st["gave_up"] = False
        st["attempts"] = 0
        st["login_attempts"] = 0
        st["retrying"] = False
        st["next_retry_hour"] = None
        st["pending"] = False
        st["saw_running"] = False
        if name == "live":
            st["last_result"] = None


# ---- 互斥门：保证同一时刻只有一个“重同步”在跑（避免 VOD+EPG 线程池叠加压服务器）----

def _sync_busy() -> bool:
    """是否有同步任务正在进行（live 运行中 / vod / epg 运行中或已触发待完成）。"""
    return (
        live_sync_status["running"]
        or sync_status["running"]
        or epg_sync_status["running"]
        or _state["vod"]["pending"]
        or _state["epg"]["pending"]
    )


# ---- 直播 ----

def _check_live(now, hour):
    st = _state["live"]
    if _live_synced_today():
        st["done_today"] = True
    if st["done_today"] or st["gave_up"]:
        return
    if live_sync_status["running"]:
        return
    if _sync_busy():
        return  # 等 VOD/EPG 同步结束后再顺序执行

    # 未到设定钟点：即便已登录也不提前触发，严格等到 live_sync_hour 再跑。
    # 否则连续运行的进程在 00:00（新的一天）因 token 仍有效会立刻同步，
    # 无视用户配置的钟点（如 8 点）。
    if now.hour < hour:
        return

    # 已到钟点（也覆盖进程晚于钟点才启动的情形）
    if _sim.state.is_authenticated:
        # 已登录：顺带触发，无需额外顶号
        _trigger_live(force_login=False)
        return
    # 未登录：强制登录兜底（受重试上限约束）
    if st["retrying"] and now.hour < (st["next_retry_hour"] or 0):
        return
    _trigger_live(force_login=True)


def _trigger_live(force_login: bool):
    st = _state["live"]
    if force_login:
        st["login_attempts"] += 1
        if st["login_attempts"] > LIVE_LOGIN_CAP:
            st["gave_up"] = True
            st["retrying"] = False
            logger.warning("[Scheduler] 直播同步今日登录尝试已达上限，放弃当天")
            return
    st["attempts"] += 1
    result = run_live_sync(_sim, _login_func)
    st["last_result"] = result
    if result.get("status") == "success":
        st["done_today"] = True
        st["retrying"] = False
        st["next_retry_hour"] = None
        logger.info("[Scheduler] 直播同步完成（%s）", result.get("message", ""))
    else:
        logger.warning("[Scheduler] 直播同步失败: %s（将在下个钟点重试）", result.get("message", ""))
        if force_login and st["login_attempts"] >= LIVE_LOGIN_CAP:
            st["gave_up"] = True
            st["retrying"] = False
        elif st["attempts"] >= LIVE_RETRY_CAP:
            st["gave_up"] = True
            st["retrying"] = False
        else:
            st["retrying"] = True
            st["next_retry_hour"] = (datetime.now().hour + 1) % 24


# ---- VOD / EPG（异步后台，靠 pending + saw_running 判定完成）----

def _check_async(name, now, today, hour, status, cap):
    st = _state[name]
    # 今天是否已同步：DB 已落库（重启后仍可判定）或本次进程内内存记录，取较新者
    last_ts = max(_db_sync_time(name), status.get("last_sync_time") or 0)
    if _date_of(last_ts) == today:
        st["done_today"] = True
        # 已确认今天完成：清除 pending，否则 _sync_busy() 会一直为真、挡住另一个任务
        st["pending"] = False
        st["retrying"] = False
    if st["done_today"] or st["gave_up"]:
        return
    if st["pending"]:
        if status.get("running"):
            st["saw_running"] = True
        elif st["saw_running"]:
            # 后台任务已结束
            if _date_of(status.get("last_sync_time", 0)) == today:
                st["done_today"] = True
                st["pending"] = False
                st["retrying"] = False
                logger.info("[Scheduler] %s 同步完成", name.upper())
            else:
                _on_async_fail(st, now, cap)
        # 否则（未运行且尚未观察到运行）等待，避免误判
        return
    if status.get("running"):
        return
    if now.hour < hour:
        return
    if st["retrying"] and now.hour < (st["next_retry_hour"] or 0):
        return
    if _sync_busy():
        return  # 另一个同步任务进行中，顺序执行，避免线程池叠加压服务器
    # 触发
    st["attempts"] += 1
    st["pending"] = True
    st["saw_running"] = False
    if name == "vod":
        start_sync_background(_sim, None)
    else:
        start_epg_sync(_sim)
    logger.info("[Scheduler] 已触发 %s 同步（第 %d 次尝试）", name.upper(), st["attempts"])


def _on_async_fail(st, now, cap):
    st["pending"] = False
    if st["attempts"] >= cap:
        st["gave_up"] = True
        st["retrying"] = False
        logger.warning("[Scheduler] %s 同步今日重试已达上限，放弃当天", st.get("_name", "任务"))
    else:
        st["retrying"] = True
        st["next_retry_hour"] = (now.hour + 1) % 24


def _check_vod(now, today, hour):
    _state["vod"]["_name"] = "VOD"
    _check_async("vod", now, today, hour, sync_status, VOD_RETRY_CAP)


def _check_epg(now, today, hour):
    _state["epg"]["_name"] = "EPG"
    _check_async("epg", now, today, hour, epg_sync_status, EPG_RETRY_CAP)


# ---- 状态查询（供 /api/scheduler/status）----

def get_scheduler_state() -> dict:
    return {
        "running": bool(_scheduler_thread and _scheduler_thread.is_alive()),
        "config": {
            "scheduler_enabled": _cfg_bool("scheduler_enabled", True),
            "live_sync_hour": _cfg_int("live_sync_hour", 0),
            "live_sync_enabled": _cfg_bool("live_sync_enabled", True),
            "vod_sync_hour": _cfg_int("vod_sync_hour", 1),
            "vod_sync_enabled": _cfg_bool("vod_sync_enabled", True),
            "epg_sync_hour": _cfg_int("epg_sync_hour", 1),
            "epg_sync_enabled": _cfg_bool("epg_sync_enabled", True),
        },
        "tasks": {
            name: {
                "done_today": _state[name]["done_today"],
                "gave_up": _state[name]["gave_up"],
                "attempts": _state[name]["attempts"],
                "retrying": _state[name]["retrying"],
                "last_sync_time": _task_last_sync_time(name),
                "last_result": _state[name].get("last_result"),
            }
            for name in ("live", "vod", "epg")
        },
    }


def _task_last_sync_time(name: str):
    if name == "live":
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT MAX(synced_at) AS m FROM live_channels WHERE source = 'server'")
            row = c.fetchone()
            conn.close()
            return row["m"] if row and row["m"] else 0
        except Exception:
            return 0
    if name == "vod":
        return max(_db_sync_time("vod"), sync_status.get("last_sync_time") or 0)
    if name == "epg":
        return max(_db_sync_time("epg"), epg_sync_status.get("last_sync_time") or 0)
    return 0
