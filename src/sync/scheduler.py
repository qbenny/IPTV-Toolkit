"""
定时同步调度器（A+E+B+D）。

- 直播同步：每天 live_sync_hour（默认 0）触发；若进程已登录且今天尚未同步，
  则首次登录即顺带完成（零额外顶号）；全天无登录则到 live_sync_hour 强制登录兜底。
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
from src.api.live import run_live_sync, live_sync_status, get_live_configs
from src.sync.filter_sync import start_sync_background, sync_status
from src.sync.epg_sync import start_epg_sync, epg_sync_status

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
    """启动调度器后台线程（幂等）。"""
    global _scheduler_thread, _sim, _login_func, _stop
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _sim = sim
    _login_func = login_func
    _stop = False
    _scheduler_thread = threading.Thread(target=_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("[Scheduler] 定时同步调度器已启动（live=%s, vod=%s, epg=%s）",
                _cfg_int("live_sync_hour", 0), _cfg_int("vod_sync_hour", 1), _cfg_int("epg_sync_hour", 1))


def stop_scheduler():
    """停止调度器。"""
    global _stop
    _stop = True


# ---- 配置读取 ----

def _cfg_int(key: str, default: int) -> int:
    try:
        return int(get_live_configs().get(key, default))
    except (ValueError, TypeError):
        return default


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

    now = datetime.now()
    live_hour = _cfg_int("live_sync_hour", 0)
    vod_hour = _cfg_int("vod_sync_hour", 1)
    epg_hour = _cfg_int("epg_sync_hour", 1)

    _check_live(now, live_hour)
    _check_vod(now, today, vod_hour)
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
    if _sim.state.is_authenticated:
        # 首次登录顺带触发：今天未跑就立即跑（不限钟点）
        _trigger_live(force_login=False)
        return
    # 未登录：等到 live_sync_hour 才强制登录兜底
    if now.hour < hour:
        return
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
    last_ts = status.get("last_sync_time", 0)
    if _date_of(last_ts) == today:
        st["done_today"] = True
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
            "live_sync_hour": _cfg_int("live_sync_hour", 0),
            "vod_sync_hour": _cfg_int("vod_sync_hour", 1),
            "epg_sync_hour": _cfg_int("epg_sync_hour", 1),
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
        return sync_status.get("last_sync_time", 0)
    if name == "epg":
        return epg_sync_status.get("last_sync_time", 0)
    return 0
