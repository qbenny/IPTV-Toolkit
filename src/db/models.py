""" 
数据库模型模块 - 表结构定义与初始化。
"""
import os
import sqlite3
import time

from src.utils.logger import logger

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "iptv.db")


def get_db_connection() -> sqlite3.Connection:
    """获取 SQLite 数据库连接。

    Returns:
        sqlite3.Connection 实例，row_factory 设置为 sqlite3.Row
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # timeout=30：并发写（同步线程 + API 写）时把写锁等待从默认 5s 提升到 30s，
    # 减少偶发 "database is locked"；check_same_thread 默认 True（连接不跨线程），安全。
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """创建数据库表结构和索引。如果表已存在则跳过创建。

    应在应用启动时调用一次。
    """
    conn = get_db_connection()
    c = conn.cursor()

    # 点播内容主表
    c.execute("""
        CREATE TABLE IF NOT EXISTS vod_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contentCode     TEXT UNIQUE NOT NULL,
            title           TEXT DEFAULT '',
            type            TEXT DEFAULT '',
            contentType     TEXT DEFAULT '',
            year            TEXT DEFAULT '',
            country         TEXT DEFAULT '',
            actors          TEXT DEFAULT '',
            director        TEXT DEFAULT '',
            score           REAL DEFAULT 0.0,
            icon            TEXT DEFAULT '',
            poster          TEXT DEFAULT '',
            isFinished      INTEGER DEFAULT 0,
            episodeTotal    INTEGER DEFAULT 0,
            contentBaseType TEXT DEFAULT '',
            contentBaseTags TEXT DEFAULT '',
            subTitle       TEXT DEFAULT '',
            searchName     TEXT DEFAULT '',
            still          TEXT DEFAULT '',
            syncedAt        INTEGER DEFAULT 0,
            first_seen_at   INTEGER DEFAULT 0
        )
    """)

    # 点播索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_title ON vod_items(title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_type ON vod_items(type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_country ON vod_items(country)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_year ON vod_items(year)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_score ON vod_items(score)")

    # 直播频道分类表
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            sort_index  INTEGER DEFAULT 0,
            color       TEXT DEFAULT '',
            is_visible  INTEGER DEFAULT 1,
            created_at  INTEGER DEFAULT 0
        )
    """)

    # 直播频道主表
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_channels (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source            TEXT NOT NULL DEFAULT 'server',
            channel_id        TEXT NOT NULL DEFAULT '',
            user_channel_id   TEXT DEFAULT '',
            name              TEXT DEFAULT '',
            display_name      TEXT DEFAULT '',
            tvg_id            TEXT DEFAULT '',
            tvg_name          TEXT DEFAULT '',
            logo_url          TEXT DEFAULT '',
            category_id       INTEGER DEFAULT 0,
            sort_index        INTEGER DEFAULT 0,
            is_enabled        INTEGER DEFAULT 1,
            multicast_url     TEXT DEFAULT '',
            unicast_url       TEXT DEFAULT '',
            unicast_url_full  TEXT DEFAULT '',
            timeshift_enabled INTEGER DEFAULT 0,
            timeshift_length  INTEGER DEFAULT 0,
            timeshift_url     TEXT DEFAULT '',
            is_hd             INTEGER DEFAULT 0,
            channel_type      TEXT DEFAULT '',
            channel_sdp       TEXT DEFAULT '',
            channel_url_raw   TEXT DEFAULT '',
            channel_locked    INTEGER DEFAULT 0,
            preview_enabled   INTEGER DEFAULT 0,
            fcc_enabled       INTEGER DEFAULT 0,
            fcc_ip            TEXT DEFAULT '',
            fcc_port          TEXT DEFAULT '',
            fec_port          TEXT DEFAULT '',
            raw_fields_json   TEXT DEFAULT '',
            back_time         INTEGER DEFAULT 0,
            channel_code      TEXT DEFAULT '',
            synced_at         INTEGER DEFAULT 0,
            created_at        INTEGER DEFAULT 0
        )
    """)

    # 直播配置表
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_config (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)

    # 第 3 步：拆表准备 —— 各领域独立配置表（与 live_config 同结构 KV）
    # live_config 已清理为纯直播配置（其余 key 已分流到 vod_config/scheduler_config/epg_config），本步仅新增表并把数据分流过去。
    for tbl in ("vod_config", "scheduler_config", "epg_config"):
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {tbl} (
                key   TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            )
        """)


    # 频道别名映射表
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_channel_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT UNIQUE NOT NULL,
            target_name TEXT NOT NULL
        )
    """)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_source ON live_channel_aliases(source_name)")

    # 数据库迁移：为旧库添加 display_name 列
    try:
        c.execute("SELECT display_name FROM live_channels LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE live_channels ADD COLUMN display_name TEXT DEFAULT ''")
        logger.info("[DB] 迁移：已添加 live_channels.display_name 列")

    # 数据库迁移：为旧库添加 back_time 列
    try:
        c.execute("SELECT back_time FROM live_channels LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE live_channels ADD COLUMN back_time INTEGER DEFAULT 0")
        logger.info("[DB] 迁移：已添加 live_channels.back_time 列")

    # 数据库迁移：为旧库添加 channel_code 列（EPG 同步免登录：存储 VIS channelCode）
    try:
        c.execute("SELECT channel_code FROM live_channels LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE live_channels ADD COLUMN channel_code TEXT DEFAULT ''")
        logger.info("[DB] 迁移：已添加 live_channels.channel_code 列")

    # 数据库迁移：为旧库添加 first_seen_at 列（记录内容首次出现时间）
    try:
        c.execute("SELECT first_seen_at FROM vod_items LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE vod_items ADD COLUMN first_seen_at INTEGER DEFAULT 0")
        logger.info("[DB] 迁移：已添加 vod_items.first_seen_at 列")

    # 数据库迁移：为旧库添加 subTitle / searchName / still 列（filter.json 未用字段）
    for col in ("subTitle", "searchName", "still"):
        try:
            c.execute(f"SELECT {col} FROM vod_items LIMIT 1")
        except sqlite3.OperationalError:
            c.execute(f"ALTER TABLE vod_items ADD COLUMN {col} TEXT DEFAULT ''")
            logger.info(f"[DB] 迁移：已添加 vod_items.{col} 列")

    # 直播索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_source ON live_channels(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_category ON live_channels(category_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_enabled ON live_channels(is_enabled)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_channel_id ON live_channels(channel_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_multicast ON live_channels(multicast_url)")

    # 首次建表插入预置分类
    categories = [
        "央视高清", "央视标清", "卫视高清", "卫视标清",
        "地方高清", "地方标清", "4K超高清", "国际",
        "付费高清", "广播", "其他"
    ]
    now_time = int(time.time())
    for idx, cat_name in enumerate(categories):
        c.execute("""
            INSERT OR IGNORE INTO live_categories (name, sort_index, created_at)
            VALUES (?, ?, ?)
        """, (cat_name, (idx + 1) * 10, now_time))

    # 首次建表插入默认配置
    default_configs = {
        "udpxy_enabled": "1",
        "udpxy_address": "",
        "fcc_global_enabled": "0",
        "timeshift_enabled": "1",
        "logo_base_url": "/static/logo/",
        "m3u_dual_line": "0",
        "low_quality_filter": "1",  # 低质量视频过滤开关（长标题+无评分=垃圾）
        "m3u8_filter": "1",         # m3u8 内容池过滤开关（JHT/YANHUA/YANKUM）
        "live_sync_hour": "0",      # 定时直播同步触发钟点（0-23），首次登录也会顺带触发
        "vod_sync_hour": "1",       # 定时 VOD 同步触发钟点（0-23，免登录）
        "epg_sync_hour": "1",       # 定时 EPG 同步触发钟点（0-23，免登录）
    }
    for k, v in default_configs.items():
        c.execute("""
            INSERT OR IGNORE INTO live_config (key, value)
            VALUES (?, ?)
        """,         (k, v))

    # EPG 节目单表
    c.execute("""CREATE TABLE IF NOT EXISTS epg_programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL,
        channel_name TEXT DEFAULT '', title TEXT DEFAULT '',
        start_time TEXT NOT NULL, end_time TEXT NOT NULL,
        program_date TEXT NOT NULL, epg_channel_id TEXT DEFAULT '',
        raw_data_json TEXT DEFAULT '', synced_at INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT 0)""")
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_epg_dedup
        ON epg_programs(channel_id, start_time, title)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_epg_channel ON epg_programs(channel_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_epg_date ON epg_programs(program_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_epg_epg_ch ON epg_programs(epg_channel_id)")
    # ---- 第 3 步：把 live_config 中各领域 key 分流到新表（幂等，真实值优先）----
    # 顺序：先迁移 live_config 真实值 -> 新表（INSERT OR IGNORE 不覆盖已有），
    #       再补各领域默认值（仅缺失时）。
    _config_migrate_keys = {
        "vod_config": ("low_quality_filter", "m3u8_filter"),
        "scheduler_config": (
            "live_sync_hour", "vod_sync_hour", "epg_sync_hour",
            "scheduler_enabled", "live_sync_enabled",
            "vod_sync_enabled", "epg_sync_enabled",
        ),
        "epg_config": ("epg_auto_sync", "epg_url"),
    }
    _config_defaults = {
        "vod_config": {"low_quality_filter": "1", "m3u8_filter": "1"},
        "scheduler_config": {
            "live_sync_hour": "0", "vod_sync_hour": "1", "epg_sync_hour": "1",
            "scheduler_enabled": "1", "live_sync_enabled": "1",
            "vod_sync_enabled": "1", "epg_sync_enabled": "1",
        },
        "epg_config": {"epg_auto_sync": "1", "epg_url": ""},
    }
    for tbl, keys in _config_migrate_keys.items():
        for k in keys:
            c.execute(
                f"INSERT OR IGNORE INTO {tbl} (key, value) "
                f"SELECT key, value FROM live_config WHERE key=?",
                (k,),
            )
    for tbl, defaults in _config_defaults.items():
        for k, v in defaults.items():
            c.execute(f"INSERT OR IGNORE INTO {tbl} (key, value) VALUES (?, ?)", (k, v))

    # ---- 第 5 步：清理 live_config 中的孤儿 key ----
    # 两类孤儿：
    #  (a) 已分流到新表的 key（vod_config/scheduler_config/epg_config），代码不再读
    #      live_config 的它们（crud 读 vod_config、scheduler_engine 读 scheduler_config、
    #      api/scheduler 读 scheduler_config、M3U 生成读 epg_config.epg_url）。
    #  (b) 旧同步子系统遗留的 sync_channels_*/sync_vod_*/sync_epg_* 开关——全仓库已无任何
    #      代码读取，属死键，一并清除。
    # 清理后 live_config 仅保留纯直播配置（udpxy/fcc/timeshift/logo/m3u 等）。
    _orphan_keys = []
    for _keys in _config_migrate_keys.values():
        _orphan_keys.extend(_keys)
    _legacy_sync_keys = (
        "sync_channels_enabled", "sync_channels_schedule_type", "sync_channels_schedule_value",
        "sync_vod_enabled", "sync_vod_schedule_type", "sync_vod_schedule_value",
        "sync_epg_enabled", "sync_epg_schedule_type", "sync_epg_schedule_value",
    )
    _orphan_keys.extend(_legacy_sync_keys)
    _orphan_keys.append("m3u_auth_required")  # 历史遗留开关，从未被读取，一并清除
    if _orphan_keys:
        _qmarks = ",".join("?" * len(_orphan_keys))
        c.execute(f"DELETE FROM live_config WHERE key IN ({_qmarks})", _orphan_keys)

    conn.commit()
    conn.close()
    logger.info("[DB] 数据库初始化完成")


if __name__ == "__main__":
    init_db()
    print(f"数据库路径: {DB_PATH}")

