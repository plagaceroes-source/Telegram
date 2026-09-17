"""Разовый диагностический скрипт: проверяет состояние источника по username."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from news_agent.db.models import RawPost, Source, UserbotAccount  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402

USERNAME = os.environ.get("CHECK_USERNAME", "noticiaru")


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(Source).where(Source.username == USERNAME))
        source = result.scalars().first()
        if source is None:
            print(f"NO_SOURCE username={USERNAME}")
        else:
            print(
                f"SOURCE id={source.id} username={source.username} active={source.active} "
                f"joined={source.joined} tg_chat_id={source.tg_chat_id} lang={source.lang} "
                f"mode={source.mode} userbot_account_id={source.userbot_account_id} "
                f"added_at={source.added_at}"
            )
            raw_result = await session.execute(
                select(RawPost).where(RawPost.source_id == source.id).order_by(RawPost.collected_at.desc())
            )
            raws = raw_result.scalars().all()
            print(f"RAW_POSTS_FOR_SOURCE count={len(raws)}")
            for rp in raws[:10]:
                print(f"  raw_post id={rp.id} status={rp.status} collected_at={rp.collected_at} tg_message_id={rp.tg_message_id}")

        accounts_result = await session.execute(select(UserbotAccount))
        for acc in accounts_result.scalars():
            print(f"ACCOUNT id={acc.id} label={acc.label} status={acc.status} session_name={acc.session_name}")
    sys.stdout.flush()
    time.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
