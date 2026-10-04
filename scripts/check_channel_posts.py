"""Разовый диагностический скрипт: число постов по дням из зеркала ChannelPost."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import ChannelPost  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402
from news_agent.reports.period import REPORT_TZ, day_bounds_utc  # noqa: E402


async def main() -> None:
    await init_db()
    today = dt.datetime.now(REPORT_TZ).date()
    async with session_scope() as session:
        total = len((await session.execute(select(ChannelPost.id))).scalars().all())
        print(f"ChannelPost всего строк: {total}")
        for back in range(0, 8):
            day = today - dt.timedelta(days=back)
            since, until = day_bounds_utc(day)
            rows = (
                await session.execute(
                    select(ChannelPost).where(ChannelPost.posted_at >= since, ChannelPost.posted_at < until)
                )
            ).scalars().all()
            system = sum(1 for r in rows if r.is_system)
            albums = sum(1 for r in rows if r.grouped_id)
            print(f"DAY {day}: постов={len(rows)} (через систему={system}, вручную={len(rows) - system}, альбомов={albums}) просмотры={sum(r.views for r in rows)}")
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
