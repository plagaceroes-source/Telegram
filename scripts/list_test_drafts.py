"""Разовый диагностический скрипт: список черновиков от тестового источника."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import DraftPost, RawPost, Source  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(Source).where(Source.username == "test_source_es"))
        source = result.scalars().first()
        if source is None:
            print("NO_TEST_SOURCE")
            return
        raw_result = await session.execute(select(RawPost).where(RawPost.source_id == source.id))
        raw_posts = raw_result.scalars().all()
        print(f"RAW_POSTS count={len(raw_posts)} ids={[r.id for r in raw_posts]}")
        for rp in raw_posts:
            draft_result = await session.execute(select(DraftPost).where(DraftPost.raw_post_id == rp.id))
            for d in draft_result.scalars():
                print(f"DRAFT id={d.id} raw_post_id={rp.id} status={d.status} approval_message_id={d.approval_message_id}")
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
