"""Отдельная команда для авторизации нового аккаунта юзербота (ТЗ 2.1.1, 2.1.2).

Запускается вручную оператором:
    python scripts/add_userbot_account.py --phone +34600111222 --label "Аккаунт 1 — испанские СМИ"

Код входа приходит в служебный чат "Telegram" в самом мессенджере (если на
номере уже есть активная сессия) либо по SMS в качестве запасного канала.
Telethon сам покажет интерактивный промпт для ввода кода/пароля 2FA.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient  # noqa: E402

from news_agent.config import settings  # noqa: E402
from news_agent.db.models import UserbotAccount  # noqa: E402
from news_agent.db.session import init_db, session_scope  # noqa: E402


def mask_phone(phone: str) -> str:
    digits = phone.strip()
    if len(digits) <= 4:
        return "*" * len(digits)
    return digits[:3] + "*" * (len(digits) - 5) + digits[-2:]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Авторизация нового аккаунта-слушателя юзербота")
    parser.add_argument("--phone", required=True, help="Номер телефона владельца аккаунта, напр. +34600111222")
    parser.add_argument("--label", required=True, help="Метка для отображения в админке")
    args = parser.parse_args()

    await init_db()

    session_name = f"account_{args.phone.strip('+')}"
    session_path = os.path.join(settings.sessions_path, session_name)
    Path(settings.sessions_path).mkdir(parents=True, exist_ok=True)

    client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
    await client.start(phone=args.phone)
    me = await client.get_me()
    print(f"Успешно авторизован как {me.first_name} (id={me.id})")
    await client.disconnect()

    async with session_scope() as session:
        account = UserbotAccount(
            phone_masked=mask_phone(args.phone),
            label=args.label,
            session_name=session_name,
            status="active",
        )
        session.add(account)
        await session.flush()
        print(f"Аккаунт добавлен в БД с id={account.id}. Теперь его можно назначать источникам в админке.")


if __name__ == "__main__":
    asyncio.run(main())
