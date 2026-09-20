"""Разовый диагностический скрипт: полный дамп инвайт-ссылок для сверки."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import InviteLink  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(InviteLink).order_by(InviteLink.id))
        for link in result.scalars():
            print(
                f"id={link.id} name={link.name!r} source_label={link.source_label!r} "
                f"revoked={link.revoked} link={link.tg_invite_link}"
            )
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
