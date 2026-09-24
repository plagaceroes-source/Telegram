"""Разовый диагностический скрипт: проверяет _top_posts (admin/app.py) с новыми
периодами «сегодня» и произвольный диапазон против реальной БД."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from admin.app import _top_posts  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

CASES = [
    ("today", "views", None, None),
    ("7", "views", None, None),
    ("custom", "views", "2026-09-19", "2026-09-20"),
    ("custom", "reactions", "invalid", "invalid"),
]


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        for posts_period, posts_sort, date_from, date_to in CASES:
            top_posts, resolved_period, resolved_sort, resolved_from, resolved_to = await _top_posts(
                session, posts_period, posts_sort, date_from, date_to
            )
            print(
                f"--- input=({posts_period!r}, {posts_sort!r}, {date_from!r}, {date_to!r}) => "
                f"resolved=({resolved_period!r}, {resolved_sort!r}, {resolved_from!r}, {resolved_to!r}) "
                f"count={len(top_posts)} ---"
            )
            for p in top_posts[:3]:
                print(f"  {p['published_at']} views={p['views']} reactions={p['reactions']} {p['snippet'][:40]!r}")

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
