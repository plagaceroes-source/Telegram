"""Разовый диагностический скрипт: выводит список целевых каналов в БД."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).order_by(TargetChannel.id))
        rows = result.scalars().all()
        if not rows:
            print("NO_TARGET_CHANNELS")
        for t in rows:
            print(f"TARGET id={t.id} username={t.username} active={t.active} tg_chat_id={t.tg_chat_id}")
    sys.stdout.flush()
    time.sleep(25)


if __name__ == "__main__":
    asyncio.run(main())
