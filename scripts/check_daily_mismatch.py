"""Разовый диагностический скрипт: смотрит, почему «прирост за день» в отчёте
не совпадает с (подписалось − отписалось) — сравнивает снепшоты ChannelStatsDaily
и события SubscriberEvent за конкретные календарные сутки (Europe/Madrid)."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import ChannelStatsDaily, SubscriberEvent, TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402
from news_agent.reports.period import REPORT_TZ, day_bounds_utc  # noqa: E402

DAY = dt.date(2026, 9, 26)


async def main() -> None:
    await init_db()
    since, until = day_bounds_utc(DAY)
    print(f"since={since} until={until}")

    async with session_scope() as session:
        targets = list((await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars())
        for t in targets:
            print(f"\n=== target {t.id} @{t.username} ===")

            snaps_result = await session.execute(
                select(ChannelStatsDaily)
                .where(
                    ChannelStatsDaily.target_channel_id == t.id,
                    ChannelStatsDaily.snapshot_at >= since - dt.timedelta(hours=3),
                    ChannelStatsDaily.snapshot_at <= until + dt.timedelta(hours=1),
                )
                .order_by(ChannelStatsDaily.snapshot_at)
            )
            snaps = list(snaps_result.scalars())
            print(f"snapshots around the day ({len(snaps)}):")
            for s in snaps:
                local = s.snapshot_at.astimezone(REPORT_TZ)
                marker = ""
                if s.snapshot_at < since:
                    marker = "  <- before day start"
                elif s.snapshot_at > until:
                    marker = "  <- after day end"
                print(f"  {s.snapshot_at.isoformat()} (local {local:%d.%m %H:%M}) count={s.subscriber_count}{marker}")

            events_result = await session.execute(
                select(SubscriberEvent)
                .where(
                    SubscriberEvent.target_channel_id == t.id,
                    SubscriberEvent.occurred_at >= since,
                    SubscriberEvent.occurred_at < until,
                )
                .order_by(SubscriberEvent.occurred_at)
            )
            events = list(events_result.scalars())
            print(f"events during the day ({len(events)}):")
            joins = sum(1 for e in events if e.event_type == "join")
            leaves = sum(1 for e in events if e.event_type == "leave")
            for e in events:
                local = e.occurred_at.astimezone(REPORT_TZ)
                print(
                    f"  {e.occurred_at.isoformat()} (local {local:%H:%M}) {e.event_type} "
                    f"user={e.tg_user_id} invite_link_id={e.invite_link_id} is_direct={e.is_direct}"
                )
            print(f"joins={joins} leaves={leaves} net={joins - leaves}")

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
