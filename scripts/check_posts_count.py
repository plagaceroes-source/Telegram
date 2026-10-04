"""Разовый диагностический скрипт: сверка числа постов в отчёте с фактом по дням."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import DraftPost, PublishedPost, TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402
from news_agent.reports.period import REPORT_TZ, day_bounds_utc  # noqa: E402


async def main() -> None:
    await init_db()
    today = dt.datetime.now(REPORT_TZ).date()
    async with session_scope() as session:
        targets = list((await session.execute(select(TargetChannel))).scalars())
        print("targets:", [(t.id, t.username, t.active) for t in targets])
        for back in range(0, 5):
            day = today - dt.timedelta(days=back)
            since, until = day_bounds_utc(day)
            rows = (
                await session.execute(
                    select(PublishedPost)
                    .where(PublishedPost.published_at >= since, PublishedPost.published_at < until)
                    .order_by(PublishedPost.tg_message_id)
                )
            ).scalars().all()
            ids = [r.tg_message_id for r in rows]
            by_target = {}
            for r in rows:
                by_target[r.target_channel_id] = by_target.get(r.target_channel_id, 0) + 1
            gaps = [(a, b) for a, b in zip(ids, ids[1:]) if b - a > 1]
            print(f"\n=== {day} (UTC {since:%d.%m %H:%M}..{until:%d.%m %H:%M}) PublishedPost={len(rows)} by_target={by_target}")
            print("  tg_message_ids:", ids)
            print("  gaps (a->b):", gaps)
            if rows:
                print("  first/last local:", rows[0].published_at.astimezone(REPORT_TZ).strftime("%H:%M"), rows[-1].published_at.astimezone(REPORT_TZ).strftime("%H:%M"))

            drafts = (
                await session.execute(
                    select(DraftPost.status, DraftPost.decided_by).where(DraftPost.decided_at >= since, DraftPost.decided_at < until)
                )
            ).all()
            by_status = {}
            for st, by in drafts:
                by_status[st] = by_status.get(st, 0) + 1
            print("  drafts decided that day by status:", by_status)

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
