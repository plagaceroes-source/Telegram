"""Разовый диагностический скрипт: инвайт-ссылки и события подписки/отписки."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import InviteLink, SubscriberEvent, TargetChannel  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(InviteLink))
        links = result.scalars().all()
        print(f"INVITE_LINKS count={len(links)}")
        for link in links:
            print(
                f"  id={link.id} name={link.name!r} target_channel_id={link.target_channel_id} "
                f"revoked={link.revoked} tg_invite_link={link.tg_invite_link}"
            )

        result = await session.execute(select(SubscriberEvent).order_by(SubscriberEvent.occurred_at.desc()).limit(20))
        events = result.scalars().all()
        print(f"SUBSCRIBER_EVENTS (last 20) count={len(events)}")
        for ev in events:
            print(
                f"  id={ev.id} target_channel_id={ev.target_channel_id} user={ev.tg_user_id} "
                f"type={ev.event_type} invite_link_id={ev.invite_link_id} is_direct={ev.is_direct} "
                f"occurred_at={ev.occurred_at}"
            )

        result = await session.execute(select(TargetChannel))
        targets = result.scalars().all()
        print(f"TARGET_CHANNELS count={len(targets)}")
        for t in targets:
            print(f"  id={t.id} username={t.username} tg_chat_id={t.tg_chat_id} active={t.active}")
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
