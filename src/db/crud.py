"""
数据库 CRUD 操作模块 - 对 vod_items 表的增删改查。
"""
import time
import sqlite3
from typing import Optional

from src.db.models import get_db_connection
from src.utils.logger import logger


def bulk_upsert_items(items: list, type_name: str) -> int:
    """批量插入或更新 vod_items 数据。

    Args:
        items: filter.json 返回的 resultSet 列表
        type_name: 内容大类名称（电视剧/电影/综艺/动漫/少儿）

    Returns:
        成功 upsert 的条目数
    """
    if not items:
        return 0

    sync_time = int(time.time())
    count = 0

    conn = get_db_connection()
    c = conn.cursor()

    for item in items:
        try:
            content_code = item.get("contentCode", "")
            if not content_code:
                continue

            title = item.get("title", "")
            content_type = item.get("contentType", "vod")
            year = item.get("year", "") or ""
            country = item.get("country", "") or ""
            actors = item.get("actors", "") or ""
            director = item.get("director", "") or ""
            score = item.get("score", 0) or 0
            icon = item.get("icon", "") or ""
            poster = item.get("poster", "") or ""
            is_finished = 1 if item.get("isFinished") in (True, 1, "1") else 0
            episode_total = item.get("updateNum", 0) or 0
            content_base_type = item.get("contentBaseType", "") or ""
            content_base_tags = item.get("contentBaseTags", "") or ""

            c.execute("""
                INSERT INTO vod_items (
                    contentCode, title, type, contentType, year, country,
                    actors, director, score, icon, poster, isFinished,
                    episodeTotal, contentBaseType, contentBaseTags, syncedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contentCode) DO UPDATE SET
                    title = excluded.title,
                    type = excluded.type,
                    contentType = excluded.contentType,
                    year = excluded.year,
                    country = excluded.country,
                    actors = excluded.actors,
                    director = excluded.director,
                    score = excluded.score,
                    icon = excluded.icon,
                    poster = excluded.poster,
                    isFinished = excluded.isFinished,
                    episodeTotal = excluded.episodeTotal,
                    contentBaseType = excluded.contentBaseType,
                    contentBaseTags = excluded.contentBaseTags,
                    syncedAt = excluded.syncedAt
            """, (
                content_code, title, type_name, content_type, year, country,
                actors, director, score, icon, poster, is_finished,
                episode_total, content_base_type, content_base_tags, sync_time
            ))
            count += 1
        except Exception as e:
            logger.warning(f"Upsert item 失败: {e}, contentCode={item.get('contentCode', '?')}")

    conn.commit()
    conn.close()
    return count


def search_items(keyword: str, page: int = 1, page_size: int = 20) -> dict:
    """搜索 vod_items 数据。

    Args:
        keyword: 搜索关键词
        page: 页码（从 1 开始）
        page_size: 每页条数

    Returns:
        {"list": [...], "total": int, "page": int, "pagecount": int}
    """
    conn = get_db_connection()
    c = conn.cursor()

    like_kw = f"%{keyword}%"

    # 总数
    c.execute("""
        SELECT COUNT(*) FROM vod_items
        WHERE title LIKE ? OR actors LIKE ? OR director LIKE ?
    """, (like_kw, like_kw, like_kw))
    total = c.fetchone()[0]

    # 分页查询
    offset = (page - 1) * page_size
    c.execute("""
        SELECT contentCode, title, type, contentType, year, country,
               actors, director, score, icon, poster, isFinished, episodeTotal
        FROM vod_items
        WHERE title LIKE ? OR actors LIKE ? OR director LIKE ?
        ORDER BY score DESC
        LIMIT ? OFFSET ?
    """, (like_kw, like_kw, like_kw, page_size, offset))
    rows = c.fetchall()
    conn.close()

    result_list = []
    for row in rows:
        item_type = row["contentType"] or "vod"
        result_list.append({
            "vod_id": f"{item_type}_{row['contentCode']}",
            "vod_name": row["title"],
            "vod_pic": row["icon"] or row["poster"] or "",
            "vod_remarks": "电影" if item_type == "vod" else "电视剧"
        })

    pagecount = max(1, (total + page_size - 1) // page_size)
    return {
        "list": result_list,
        "total": total,
        "page": page,
        "pagecount": pagecount,
        "limit": page_size
    }


def filter_items(content_type: str, filters: dict = None, page: int = 1, page_size: int = 20) -> dict:
    """按条件和过滤参数查询 vod_items。

    Args:
        content_type: TVBox 分类（movies → 电影, series → 电视剧）
        filters: 过滤条件 {"country": "美国", "year": "2020-2029"}
        page: 页码
        page_size: 每页条数

    Returns:
        {"list": [...], "total": int, "page": int, "pagecount": int}
    """
    # content_type 映射到数据库 type 字段
    type_map = {
        "movies": "电影",
        "series": "电视剧",
        "variety": "综艺",
        "anime": "动漫",
        "kids": "少儿"
    }
    db_type = type_map.get(content_type, content_type)

    conn = get_db_connection()
    c = conn.cursor()

    sql = "SELECT COUNT(*) FROM vod_items WHERE type = ?"
    params = [db_type]

    filters = filters or {}
    if filters.get("country"):
        sql += " AND country = ?"
        params.append(filters["country"])

    if filters.get("year"):
        year_val = filters["year"]
        if "-" in year_val:
            parts = year_val.split("-")
            if len(parts) == 2:
                sql += " AND CAST(year AS INTEGER) BETWEEN ? AND ?"
                params.extend([int(parts[0]), int(parts[1])])
        else:
            sql += " AND year = ?"
            params.append(year_val)

    # Count
    c.execute(sql, params)
    total = c.fetchone()[0]

    # Query with sorting
    data_sql = sql.replace("SELECT COUNT(*)", """
        SELECT contentCode, title, type, contentType, year, country,
               actors, director, score, icon, poster, isFinished, episodeTotal
    """)
    data_sql += " ORDER BY score DESC LIMIT ? OFFSET ?"
    data_params = params + [page_size, (page - 1) * page_size]

    c.execute(data_sql, data_params)
    rows = c.fetchall()
    conn.close()

    result_list = []
    for row in rows:
        item_type = row["contentType"] or "vod"
        result_list.append({
            "vod_id": f"{item_type}_{row['contentCode']}",
            "vod_name": row["title"],
            "vod_pic": row["icon"] or row["poster"] or "",
            "vod_remarks": "电影" if item_type == "vod" else "电视剧"
        })

    pagecount = max(1, (total + page_size - 1) // page_size)
    return {
        "list": result_list,
        "total": total,
        "page": page,
        "pagecount": pagecount,
        "limit": page_size
    }


def get_item_by_code(content_code: str) -> Optional[dict]:
    """根据 contentCode 查询单条记录。

    Args:
        content_code: VIS 内容编码

    Returns:
        字典或 None
    """
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vod_items WHERE contentCode = ?", (content_code,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_stats() -> dict:
    """获取数据库统计信息。

    Returns:
        {"total": int, "types": {...}, "last_synced": int}
    """
    conn = get_db_connection()
    c = conn.cursor()

    # 总数
    c.execute("SELECT COUNT(*) FROM vod_items")
    total = c.fetchone()[0]

    # 各类型数量
    c.execute("SELECT type, COUNT(*) as cnt FROM vod_items GROUP BY type")
    type_counts = {row["type"]: row["cnt"] for row in c.fetchall()}

    # 最后同步时间
    c.execute("SELECT MAX(syncedAt) FROM vod_items")
    last = c.fetchone()[0] or 0

    conn.close()
    return {
        "total": total,
        "types": type_counts,
        "last_synced": last
    }


def get_unique_values(column: str, type_name: str = None) -> list:
    """获取某个字段的去重值列表（用于生成过滤器选项）。

    Args:
        column: 列名（如 country, year）
        type_name: 可选，限制类型

    Returns:
        去重后的值列表（降序排列）
    """
    conn = get_db_connection()
    c = conn.cursor()
    if type_name:
        c.execute(f"""
            SELECT DISTINCT {column} FROM vod_items
            WHERE type = ? AND {column} != ''
            ORDER BY {column} DESC
        """, (type_name,))
    else:
        c.execute(f"""
            SELECT DISTINCT {column} FROM vod_items
            WHERE {column} != ''
            ORDER BY {column} DESC
        """)
    values = [row[0] for row in c.fetchall()]
    conn.close()
    return values


def clean_old_data(sync_time: int):
    """删除同步时间戳不是指定值的旧数据（全量覆盖用）。

    Args:
        sync_time: 当前批次同步时间戳，不等于此值的数据将被删除
    """
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM vod_items WHERE syncedAt != ?", (sync_time,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info(f"[DB] 清理 {deleted} 条过期数据")


if __name__ == "__main__":
    from src.db.models import init_db
    init_db()

    # 测试插入
    test_items = [
        {
            "contentCode": "TEST001",
            "title": "测试电影1",
            "contentType": "vod",
            "year": "2024",
            "country": "美国",
            "actors": "测试演员A",
            "score": 8.5
        },
        {
            "contentCode": "TEST002",
            "title": "测试电视剧1",
            "contentType": "series",
            "year": "2023",
            "country": "内地",
            "actors": "测试演员B",
            "score": 9.0
        }
    ]
    count = bulk_upsert_items(test_items, "电影")
    print(f">>> 插入测试数据 {count} 条")

    # 测试 stats
    stats = get_stats()
    print(f">>> 数据库统计: {stats}")

    # 测试搜索
    result = search_items("测试")
    print(f">>> 搜索 '测试': {result['total']} 条")

    # 测试过滤
    result = filter_items("movies")
    print(f">>> 过滤 movies: {result['total']} 条")

    # 测试 get_unique_values
    countries = get_unique_values("country")
    print(f">>> 国家列表: {countries}")

    # 清理测试数据
    clean_old_data(0)
    print(">>> CRUD 模块测试完成")
