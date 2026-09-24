"""Разовый скрипт: отправляет тестовые дневной и месячный отчёты в REPORTS_CHAT_ID
прямо сейчас, не дожидаясь расписания (для проверки, что всё дошло и выглядит
как надо)."""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.bot.session import build_bot  # noqa: E402
from news_agent.config import settings  # noqa: E402
from news_agent.db.session import init_db  # noqa: E402
from news_agent.reports.daily import send_daily_report  # noqa: E402
from news_agent.reports.monthly import send_monthly_report  # noqa: E402
from news_agent.reports.period import REPORT_TZ  # noqa: E402


async def main() -> None:
    if not settings.reports_chat_id:
        print("REPORTS_CHAT_ID не задан")
        return
    await init_db()
    bot = build_bot(settings.bot_token)
    try:
        today = dt.datetime.now(REPORT_TZ).date()
        await send_daily_report(bot, today)
        print("daily sent")
        await send_monthly_report(bot, today.year, today.month)
        print("monthly sent")
    finally:
        await bot.session.close()

    sys.stdout.flush()
    time.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
