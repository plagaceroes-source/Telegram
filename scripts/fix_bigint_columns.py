"""Разовая миграция: расширяет chat_id-колонки до BIGINT (Telegram id супергрупп/каналов
не помещаются в INTEGER — ловили asyncpg.DataError 'value out of int32 range')."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from news_agent.db.session import engine  # noqa: E402

ALTERS = [
    "ALTER TABLE sources ALTER COLUMN tg_chat_id TYPE BIGINT",
    "ALTER TABLE target_channels ALTER COLUMN tg_chat_id TYPE BIGINT",
    "ALTER TABLE draft_posts ALTER COLUMN approval_chat_id TYPE BIGINT",
]


async def main() -> None:
    async with engine.begin() as conn:
        for stmt in ALTERS:
            await conn.execute(text(stmt))
            print(f"OK: {stmt}")
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
