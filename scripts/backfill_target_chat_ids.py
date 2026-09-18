"""Разовый скрипт: заполняет TargetChannel.tg_chat_id для каналов, где он пуст
(без него не работает сопоставление chat_member-апдейтов — статистика по
инвайт-ссылкам, news_agent/stats/events.py)."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.bot.session import build_bot  # noqa: E402
from news_agent.config import settings  # noqa: E402
from news_agent.db.models import TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).where(TargetChannel.tg_chat_id.is_(None)))
        targets = result.scalars().all()

    if not targets:
        print("NOTHING_TO_BACKFILL")
        return

    bot = build_bot(settings.bot_token)
    for target in targets:
        try:
            chat = await bot.get_chat(chat_id=f"@{target.username}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED id={target.id} username={target.username}: {exc}")
            continue
        async with session_scope() as session:
            db_target = await session.get(TargetChannel, target.id)
            db_target.tg_chat_id = chat.id
        print(f"BACKFILLED id={target.id} username={target.username} tg_chat_id={chat.id}")
    await bot.session.close()
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
