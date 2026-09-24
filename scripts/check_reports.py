"""Разовый диагностический скрипт: строит тексты ежедневного и месячного
отчётов (news_agent/reports/) против реальной БД и печатает их, без отправки
в Telegram (send_* сами не отправят, пока REPORTS_CHAT_ID не задан, но build_*
работают независимо и здесь вызываются напрямую)."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.session import init_db, session_scope  # noqa: E402
from news_agent.reports.daily import build_daily_report  # noqa: E402
from news_agent.reports.monthly import build_monthly_report  # noqa: E402
from news_agent.reports.period import REPORT_TZ  # noqa: E402


async def main() -> None:
    await init_db()
    today = dt.datetime.now(REPORT_TZ).date()
    async with session_scope() as session:
        daily_msgs = await build_daily_report(session, today)
        print(f"=== DAILY ({today}) — {len(daily_msgs)} сообщение(й) ===")
        for i, msg in enumerate(daily_msgs):
            print(f"--- chunk {i} ({len(msg)} chars) ---")
            print(msg)

        monthly_msgs = await build_monthly_report(session, today.year, today.month)
        print(f"\n=== MONTHLY ({today.year}-{today.month:02d}) — {len(monthly_msgs)} сообщение(й) ===")
        for i, msg in enumerate(monthly_msgs):
            print(f"--- chunk {i} ({len(msg)} chars) ---")
            print(msg)

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
