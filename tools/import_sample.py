"""
从 sample/ 目录导入测试数据到 SQLite 数据库。
用法：python tools/import_sample.py
"""
import json
import os
import sys

# 确保能找到 src 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.models import init_db, get_db_connection
from src.db.crud import get_stats

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_FILE = os.path.join(PROJECT_DIR, "sample", "series_top100.json")


def import_series():
    """导入 series_top100.json 到 vod_items 表。"""
    if not os.path.exists(SAMPLE_FILE):
        print(f"[ERROR] 样本文件不存在: {SAMPLE_FILE}")
        return

    with open(SAMPLE_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"[INFO] 读取到 {len(items)} 条数据")

    init_db()
    conn = get_db_connection()
    c = conn.cursor()

    # 清空旧测试数据（保留 type != '电视剧' 的数据）
    c.execute("DELETE FROM vod_items WHERE type = '电视剧' AND syncedAt = 0")
    deleted = c.rowcount
    if deleted:
        print(f"[INFO] 清理旧电视剧数据 {deleted} 条")
    conn.commit()

    import time
    sync_time = int(time.time())
    inserted = 0
    skipped = 0

    for item in items:
        content_code = item.get("contentCode", "")
        if not content_code:
            skipped += 1
            continue

        title = item.get("title", "")
        content_type = item.get("type", "series")  # series → contentType
        year = str(item.get("year", "")) or ""
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

        try:
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
                content_code, title, "电视剧", content_type, year, country,
                actors, director, score, icon, poster, is_finished,
                episode_total, content_base_type, content_base_tags, sync_time
            ))
            inserted += 1
        except Exception as e:
            print(f"[WARN] 插入失败 {content_code}: {e}")
            skipped += 1

    conn.commit()
    conn.close()

    print(f"[OK] 导入完成: 成功 {inserted} 条, 跳过 {skipped} 条")

    # 打印统计
    stats = get_stats()
    print(f"[STATS] 总数据: {stats['total']} 条")
    for t, cnt in stats.get("types", {}).items():
        print(f"  - {t}: {cnt} 条")


if __name__ == "__main__":
    import_series()
