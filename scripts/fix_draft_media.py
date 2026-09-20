"""Разовый скрипт: перезаливает медиа зависшего черновика через Bot API (в file_id),
если исходный локальный файл ещё жив на диске текущего сервиса (запускать на userbot-1,
где Telethon скачивал файл)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.bot.session import build_bot  # noqa: E402
from news_agent.config import settings  # noqa: E402
from news_agent.db.models import DraftPost, RawPost  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402
from news_agent.services.media_relay import upload_and_get_ref  # noqa: E402

DRAFT_IDS = [int(x) for x in os.environ["FIX_DRAFT_ID"].split(",") if x.strip()]


async def _fix_one(bot, draft_id: int) -> None:
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            print(f"NO_DRAFT id={draft_id}")
            return
        old_paths = list(draft.media_paths)

    print(f"OLD_MEDIA_PATHS id={draft_id} {old_paths}")

    new_paths = []
    for p in old_paths:
        if not Path(p).exists():
            print(f"SKIP_MISSING id={draft_id} {p}")
            continue
        ref = await upload_and_get_ref(bot, settings.storage_chat_id, p)
        new_paths.append(ref)
        print(f"UPLOADED id={draft_id} {p} -> {ref}")

    if not new_paths:
        print(f"NOTHING_UPLOADED id={draft_id}")
        return

    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        draft.media_paths = new_paths
        raw = await session.get(RawPost, draft.raw_post_id)
        if raw:
            raw.media_paths = new_paths
    print(f"DRAFT_UPDATED id={draft_id} media_paths={new_paths}")


async def main() -> None:
    await init_db()
    if not settings.storage_chat_id:
        print("NO_STORAGE_CHAT_ID_CONFIGURED")
        return

    bot = build_bot(settings.bot_token)
    for draft_id in DRAFT_IDS:
        await _fix_one(bot, draft_id)
    await bot.session.close()
    sys.stdout.flush()
    time.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
