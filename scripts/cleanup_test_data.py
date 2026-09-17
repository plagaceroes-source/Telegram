"""Разовый скрипт очистки: удаляет карточки непринятых тестовых черновиков из чата
одобрения и полностью вычищает данные тестового источника (test_source_es) из БД."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from news_agent.bot.session import build_bot  # noqa: E402
from news_agent.db.models import DraftPost, PublishedPost, RawPost, Source  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(Source).where(Source.username == "test_source_es"))
        source = result.scalars().first()
        if source is None:
            print("NO_TEST_SOURCE")
            return

        raw_ids_result = await session.execute(select(RawPost.id).where(RawPost.source_id == source.id))
        raw_ids = [row[0] for row in raw_ids_result.all()]

        drafts_result = await session.execute(select(DraftPost).where(DraftPost.raw_post_id.in_(raw_ids)))
        drafts = drafts_result.scalars().all()
        draft_ids = [d.id for d in drafts]
        pending_cards = [(d.approval_chat_id, d.approval_message_id) for d in drafts if d.approval_message_id]

    bot = build_bot(os.environ["BOT_TOKEN"])
    for chat_id, message_id in pending_cards:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            print(f"DELETED_CARD chat={chat_id} message={message_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"COULD_NOT_DELETE chat={chat_id} message={message_id}: {exc}")
    await bot.session.close()

    async with session_scope() as session:
        await session.execute(delete(PublishedPost).where(PublishedPost.draft_post_id.in_(draft_ids)))
        await session.execute(delete(DraftPost).where(DraftPost.id.in_(draft_ids)))
        await session.execute(delete(RawPost).where(RawPost.id.in_(raw_ids)))
        await session.execute(delete(Source).where(Source.id == source.id))
    print(f"CLEANED_UP source_id={source.id} raw_posts={raw_ids} drafts={draft_ids}")
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
