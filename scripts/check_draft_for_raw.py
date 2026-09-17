"""Разовый диагностический скрипт: показывает черновик(и) для указанного raw_post_id."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import DraftPost, RawPost  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

RAW_POST_ID = int(os.environ.get("CHECK_RAW_POST_ID", "8"))


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        raw = await session.get(RawPost, RAW_POST_ID)
        if raw is None:
            print(f"NO_RAW_POST id={RAW_POST_ID}")
            return
        print(f"RAW_POST id={raw.id} status={raw.status} text={raw.text[:200]!r}")

        result = await session.execute(select(DraftPost).where(DraftPost.raw_post_id == RAW_POST_ID))
        drafts = result.scalars().all()
        if not drafts:
            print("NO_DRAFT_FOR_RAW_POST")
        for d in drafts:
            print(
                f"DRAFT id={d.id} status={d.status} approval_chat_id={d.approval_chat_id} "
                f"approval_message_id={d.approval_message_id} target_channel_id={d.target_channel_id} "
                f"created_at={d.created_at} translated_text={d.translated_text[:200]!r}"
            )
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
