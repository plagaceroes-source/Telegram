"""Разовый скрипт: откатывает source_label у конкретных инвайт-ссылок по id
(исправление моей же предыдущей ошибочной массовой правки)."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.models import InviteLink  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

RESTORE = {
    3: "Барахолка резерв",
    6: "Барахолка резерв",
}


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        for link_id, label in RESTORE.items():
            link = await session.get(InviteLink, link_id)
            if link is None:
                print(f"NO_LINK id={link_id}")
                continue
            old = link.source_label
            link.source_label = label
            print(f"RESTORED id={link_id} name={link.name} {old!r} -> {label!r}")
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
