"""Разовый тестовый скрипт: создаёт источник (если нет) и сырой пост для проверки
всего пайплайна (сбор → перевод → карточка → публикация) без реального юзербота.
Не предназначен для постоянного использования.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import RawPost, Source  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(Source).where(Source.username == "test_source_es"))
        source = result.scalars().first()
        if source is None:
            source = Source(username="test_source_es", title="Тестовый источник (ES)", lang="es", mode="rewrite")
            session.add(source)
            await session.flush()
            print(f"SOURCE_CREATED id={source.id}")
        else:
            print(f"SOURCE_EXISTS id={source.id}")

        raw = RawPost(
            source_id=source.id,
            text=(
                "El Ayuntamiento de Benidorm anunció hoy la puesta en marcha de un nuevo sistema "
                "de recogida selectiva de residuos en la zona del casco antiguo, que entrará en "
                "funcionamiento a partir de la próxima semana."
            ),
            detected_lang="es",
            media_paths=[],
            status="pending",
        )
        session.add(raw)
        await session.flush()
        print(f"RAW_POST_CREATED id={raw.id}")


if __name__ == "__main__":
    asyncio.run(main())
