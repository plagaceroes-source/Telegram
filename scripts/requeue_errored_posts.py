"""Разовый скрипт: возвращает raw_posts в статусе error обратно в pending
(например, после сбоя AI-провайдера — квота исчерпана и т.п.)."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import RawPost  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(RawPost).where(RawPost.status == "error"))
        errored = result.scalars().all()
        for rp in errored:
            print(f"REQUEUE id={rp.id}")
            rp.status = "pending"
    print(f"TOTAL_REQUEUED={len(errored)}")
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
