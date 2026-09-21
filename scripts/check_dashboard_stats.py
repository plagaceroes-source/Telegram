"""Разовый диагностический скрипт: прогоняет новую агрегацию статистики
подписчиков дашборда (admin/app.py) против реальной БД и печатает результат,
без поднятия HTTP-сервера."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from admin.app import PERIOD_DAYS, _subscriber_flow_series, _subscriber_growth_series  # noqa: E402
from news_agent.db.models import TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        targets = list((await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars())
        target_ids = [t.id for t in targets]
        print(f"targets={[(t.id, t.username) for t in targets]}")

        for period, days in PERIOD_DAYS.items():
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days) if days else None
            growth = await _subscriber_growth_series(session, target_ids, since)
            flow, joins, leaves = await _subscriber_flow_series(session, target_ids, since)
            print(f"--- period={period} since={since} ---")
            print(f"growth_series (len={len(growth)}): first={growth[0] if growth else None} last={growth[-1] if growth else None}")
            print(f"flow_series (len={len(flow)}) joins={joins} leaves={leaves}")

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
