"""Точка входа для cron job снепшотов статистики (ТЗ раздел 8: Cron Job на Render).

Запускается по расписанию (раз в час/день) и завершается после одного снепшота —
свой шедулер не нужен, планирование делает Render Cron / системный cron.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.bot.session import build_bot  # noqa: E402
from news_agent.config import settings  # noqa: E402
from news_agent.db.session import init_db  # noqa: E402
from news_agent.stats.snapshot import take_snapshot  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    await init_db()
    bot = build_bot(settings.bot_token)
    try:
        taken = await take_snapshot(bot)
        logging.getLogger(__name__).info("Снепшот снят для %d каналов", taken)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
