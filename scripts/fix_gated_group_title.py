"""Разовый скрипт: чистит title у GatedGroup от случайно вставленного текста
второй команды (например, при вводе /add_gated_group без переноса строки между
двумя вызовами) и синхронизирует уже созданные InviteLink.source_label."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import GatedGroup, InviteLink  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

TG_CHAT_ID = int(os.environ["FIX_GATED_CHAT_ID"])
NEW_TITLE = os.environ["FIX_GATED_TITLE"]


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(GatedGroup).where(GatedGroup.tg_chat_id == TG_CHAT_ID))
        gated = result.scalars().first()
        if gated is None:
            print(f"NO_GATED_GROUP tg_chat_id={TG_CHAT_ID}")
            return
        old_title = gated.title
        gated.title = NEW_TITLE
        print(f"GATED_GROUP id={gated.id} title {old_title!r} -> {NEW_TITLE!r}")

        links_result = await session.execute(
            select(InviteLink).where(
                InviteLink.target_channel_id == gated.target_channel_id,
                InviteLink.source_label == old_title,
            )
        )
        links = links_result.scalars().all()
        for link in links:
            link.source_label = NEW_TITLE
            print(f"INVITE_LINK id={link.id} name={link.name} source_label -> {NEW_TITLE!r}")

    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
