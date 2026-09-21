"""Разовый диагностический скрипт: прогоняет агрегацию статистики подписчиков
дашборда (admin/app.py), включая период «сегодня» и произвольный период,
против реальной БД и печатает результат."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from admin.app import (  # noqa: E402
    _granularity_for,
    _resolve_period,
    _subscriber_flow_series,
    _subscriber_growth_series,
)
from news_agent.db.models import TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

CASES = [
    ("today", None, None),
    ("7", None, None),
    ("all", None, None),
    ("custom", "2026-09-19", "2026-09-20"),
    ("custom", "invalid", "invalid"),  # проверка отката на дефолт при кривом вводе
]


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        targets = list((await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars())
        target_ids = [t.id for t in targets]
        print(f"targets={[(t.id, t.username) for t in targets]}")

        for period_in, date_from, date_to in CASES:
            period, since, until, resolved_from, resolved_to = _resolve_period(period_in, date_from, date_to)
            granularity = _granularity_for(since, until)
            growth = await _subscriber_growth_series(session, target_ids, since, until, granularity)
            flow, joins, leaves = await _subscriber_flow_series(session, target_ids, since, until, granularity)
            print(
                f"--- input={period_in!r} from={date_from} to={date_to} "
                f"=> period={period} since={since} until={until} granularity={granularity} "
                f"resolved_from={resolved_from} resolved_to={resolved_to} ---"
            )
            print(f"growth_series (len={len(growth)}): first={growth[0] if growth else None} last={growth[-1] if growth else None}")
            print(f"flow_series (len={len(flow)}) joins={joins} leaves={leaves}")

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
