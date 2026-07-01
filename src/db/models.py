"""
数据库模型模块 - 表结构定义与初始化。
"""
import os
import sqlite3

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

    # 内容主表
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

    # 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_title ON vod_items(title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_type ON vod_items(type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_country ON vod_items(country)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_year ON vod_items(year)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vod_score ON vod_items(score)")

    conn.commit()
    conn.close()
    print(">>> [DB] 数据库初始化完成")


if __name__ == "__main__":
    init_db()
    print(f"数据库路径: {DB_PATH}")
