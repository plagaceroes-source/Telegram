"""Разовый диагностический скрипт: последние N черновиков с их состоянием."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import DraftPost, RawPost, Source  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

LIMIT = int(os.environ.get("LIST_LIMIT", "10"))


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(
            select(DraftPost).order_by(DraftPost.id.desc()).limit(LIMIT)
        )
        drafts = result.scalars().all()
        for d in drafts:
            raw = await session.get(RawPost, d.raw_post_id)
            source = await session.get(Source, raw.source_id) if raw else None
            print(
                f"DRAFT id={d.id} raw_post_id={d.raw_post_id} source={source.username if source else '?'} "
                f"status={d.status} media_paths={d.media_paths} approval_chat_id={d.approval_chat_id} "
                f"approval_message_id={d.approval_message_id} created_at={d.created_at}\n"
                f"  raw_text={raw.text[:150]!r}\n"
                f"  translated_text={d.translated_text[:150]!r}"
            )
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
