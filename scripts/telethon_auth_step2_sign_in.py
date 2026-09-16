"""Шаг 2 удалённой авторизации юзербота: подтверждение кодом (и паролем 2FA при необходимости).

Переменные окружения:
    AUTH_PHONE            — тот же номер, что и в шаге 1
    AUTH_CODE             — код, пришедший в Telegram/SMS
    AUTH_PHONE_CODE_HASH  — значение, напечатанное шагом 1
    AUTH_2FA_PASSWORD      — опционально, если на аккаунте включена двухфакторная защита
    UB_ACCOUNT_LABEL       — метка аккаунта для админки (по умолчанию "Аккаунт 1")

После успешного входа создаёт (или переиспользует) запись в userbot_accounts,
чтобы run_userbot.py сразу мог подхватить сессию.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient  # noqa: E402
from telethon.errors import SessionPasswordNeededError  # noqa: E402
from sqlalchemy import select  # noqa: E402

from news_agent.config import settings  # noqa: E402
from news_agent.db.models import UserbotAccount  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


def mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return "*" * len(phone)
    return phone[:3] + "*" * (len(phone) - 5) + phone[-2:]


async def main() -> None:
    phone = os.environ["AUTH_PHONE"]
    code = os.environ["AUTH_CODE"]
    phone_code_hash = os.environ["AUTH_PHONE_CODE_HASH"]
    password = os.environ.get("AUTH_2FA_PASSWORD")
    label = os.environ.get("UB_ACCOUNT_LABEL", "Аккаунт 1")

    session_name = f"account_{phone.strip('+')}"
    session_path = os.path.join(settings.sessions_path, session_name)

    client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not password:
            print("NEED_2FA_PASSWORD")
            await client.disconnect()
            return
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"SIGNED_IN_AS {me.id} {me.first_name}")
    await client.disconnect()

    await init_db()
    async with session_scope() as session:
        result = await session.execute(select(UserbotAccount).where(UserbotAccount.session_name == session_name))
        existing = result.scalars().first()
        if existing:
            print(f"ACCOUNT_ALREADY_EXISTS id={existing.id}")
        else:
            account = UserbotAccount(
                phone_masked=mask_phone(phone),
                label=label,
                session_name=session_name,
                status="active",
            )
            session.add(account)
            await session.flush()
            print(f"ACCOUNT_CREATED id={account.id}")


if __name__ == "__main__":
    asyncio.run(main())
