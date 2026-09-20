"""Разовый скрипт: правит source_label конкретной инвайт-ссылки по её name."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import InviteLink  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

LINK_NAME = os.environ["FIX_LINK_NAME"]
NEW_LABEL = os.environ["FIX_LINK_LABEL"]


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(InviteLink).where(InviteLink.name == LINK_NAME))
        link = result.scalars().first()
        if link is None:
            print(f"NO_LINK name={LINK_NAME}")
            return
        old = link.source_label
        link.source_label = NEW_LABEL
        print(f"INVITE_LINK id={link.id} name={link.name} source_label {old!r} -> {NEW_LABEL!r}")
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
