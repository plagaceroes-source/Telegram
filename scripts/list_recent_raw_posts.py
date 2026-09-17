"""Разовый диагностический скрипт: последние N сырых постов с их статусом."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import RawPost, Source  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

LIMIT = int(os.environ.get("LIST_LIMIT", "10"))


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(
            select(RawPost).order_by(RawPost.id.desc()).limit(LIMIT)
        )
        raws = result.scalars().all()
        for rp in raws:
            source = await session.get(Source, rp.source_id)
            print(
                f"RAW id={rp.id} source={source.username if source else '?'} status={rp.status} "
                f"media_paths={rp.media_paths} collected_at={rp.collected_at} tg_message_id={rp.tg_message_id}"
            )
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
