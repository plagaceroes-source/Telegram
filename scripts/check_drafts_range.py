"""Разовый диагностический скрипт: показывает черновики по списку id (CHECK_DRAFT_IDS=9,10)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.models import DraftPost  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

DRAFT_IDS = [int(x) for x in os.environ.get("CHECK_DRAFT_IDS", "").split(",") if x.strip()]


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        for did in DRAFT_IDS:
            d = await session.get(DraftPost, did)
            if d is None:
                print(f"NO_DRAFT id={did}")
                continue
            print(
                f"DRAFT id={d.id} status={d.status} media_paths={d.media_paths} "
                f"approval_message_id={d.approval_message_id} created_at={d.created_at}"
            )
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
