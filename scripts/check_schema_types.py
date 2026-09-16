"""Разовый диагностический скрипт: сверяет типы timestamp-колонок в БД с ожидаемыми."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from news_agent.db.session import engine  # noqa: E402


async def main() -> None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE data_type LIKE 'timestamp%' AND table_schema = 'public' "
                "ORDER BY table_name, column_name"
            )
        )
        for row in result:
            print(f"{row.table_name}.{row.column_name} = {row.data_type}")
    sys.stdout.flush()
    time.sleep(20)


if __name__ == "__main__":
    asyncio.run(main())
