"""Пересоздаёт все таблицы с нуля (DROP + CREATE) по текущим моделям.

ОПАСНО: удаляет все данные. Нужен явный флаг подтверждения, чтобы случайно не
снести прод-данные. Использовался один раз при первом деплое на Railway,
когда исходная схема была создана с багом в типах колонок дат (см. коммит
"Исправить критичный баг: datetime-колонки ломали любую запись в Postgres")
и таблицы были ещё пустые.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.models import Base  # noqa: E402
from news_agent.db.session import engine  # noqa: E402


async def main() -> None:
    if os.environ.get("CONFIRM_RESET") != "yes-drop-all-data":
        raise SystemExit("Установите CONFIRM_RESET=yes-drop-all-data, чтобы подтвердить удаление всех данных")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("DB_RESET_OK")


if __name__ == "__main__":
    asyncio.run(main())
