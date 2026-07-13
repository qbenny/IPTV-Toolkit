"""
配置存储原语层（第 2 步引入）。

集中所有 key-value 配置的 DB 读写，避免 SQL 散落在 live.py / crud.py / scheduler.py 各处。
本层只负责「读写」，不含任何业务语义；领域语义与副作用（如定时启停）留给各领域模块。

表名走白名单校验，杜绝 SQL 注入（表名无法参数化，必须白名单）。
"""
from src.db.models import get_db_connection
from src.utils.logger import logger

# 允许访问的配置表（第 3 步拆表后扩展）
_ALLOWED_TABLES = {
    "live_config",
    "vod_config",
    "scheduler_config",
    "epg_config",
}


def _resolve_table(table: str) -> str:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"非法的配置表名: {table}")
    return table


def cfg_get(key: str, default=None, table: str = "live_config"):
    """读取单个配置项，缺省返回 default。"""
    try:
        t = _resolve_table(table)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(f"SELECT value FROM {t} WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception as e:
        logger.warning("[ConfigStore] 读取 %s.%s 失败: %s", table, key, e)
        return default


def cfg_set(key: str, value, table: str = "live_config"):
    """写入单个配置项（INSERT OR REPLACE）。"""
    try:
        t = _resolve_table(table)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(f"INSERT OR REPLACE INTO {t} (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[ConfigStore] 写入 %s.%s 失败: %s", table, key, e)


def cfg_bulk_set(configs: dict, table: str = "live_config"):
    """批量写入配置项。"""
    try:
        t = _resolve_table(table)
        conn = get_db_connection()
        c = conn.cursor()
        for k, v in configs.items():
            c.execute(f"INSERT OR REPLACE INTO {t} (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[ConfigStore] 批量写入 %s 失败: %s", table, e)


def cfg_get_all(table: str = "live_config") -> dict:
    """读取整张配置表为 {key: value}。"""
    try:
        t = _resolve_table(table)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(f"SELECT key, value FROM {t}")
        rows = c.fetchall()
        conn.close()
        return {row["key"]: row["value"] for row in rows}
    except Exception as e:
        logger.error("[ConfigStore] 读取 %s 失败: %s", table, e)
        return {}
