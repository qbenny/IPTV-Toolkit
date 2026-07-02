""" 
数据库模型模块 - 表结构定义与初始化。
"""
import os
import sqlite3

from src.utils.logger import logger

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "iptv.db")


def get_db_connection() -> sqlite3.Connection:
    """获取 SQLite 数据库连接。

    Returns:
        sqlite3.Connection 实例，row_factory 设置为 sqlite3.Row
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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

    # ---- VOD 内容表 ----
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
            syncedAt        INTEGER DEFAULT 0
        )
    """)

    # VOD 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_title ON vod_items(title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_type ON vod_items(type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_country ON vod_items(country)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_year ON vod_items(year)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_score ON vod_items(score)")

    # ---- 直播频道分类表 ----
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

    # ---- 直播频道主表 ----
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_channels (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source            TEXT NOT NULL DEFAULT 'server',
            channel_id        TEXT NOT NULL DEFAULT '',
            user_channel_id   TEXT DEFAULT '',
            name              TEXT DEFAULT '',
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
            synced_at         INTEGER DEFAULT 0,
            created_at        INTEGER DEFAULT 0
        )
    """)

    # 直播频道索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_source     ON live_channels(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_category   ON live_channels(category_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_enabled    ON live_channels(is_enabled)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_channel_id ON live_channels(channel_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_multicast  ON live_channels(multicast_url)")

    # ---- 直播配置表 ----
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_config (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()
    logger.info("[DB] 数据库初始化完成（含直播频道表）")


if __name__ == "__main__":
    init_db()
    print(f"数据库路径: {DB_PATH}")
