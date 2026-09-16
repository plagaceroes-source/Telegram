"""Шаг 1 удалённой авторизации юзербота через переменные окружения (без интерактивного ввода).

Используется, когда add_userbot_account.py нельзя запустить интерактивно (например,
авторизация проводится через временный запуск сервиса на хостинге). Отправляет код
подтверждения и печатает phone_code_hash, который нужен для шага 2.

Переменные окружения:
    AUTH_PHONE          — номер телефона, напр. +380733947844
    TELEGRAM_API_ID / TELEGRAM_API_HASH — как обычно
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient  # noqa: E402

from news_agent.config import settings  # noqa: E402


async def main() -> None:
    phone = os.environ["AUTH_PHONE"]
    session_name = f"account_{phone.strip('+')}"
    session_path = os.path.join(settings.sessions_path, session_name)
    Path(settings.sessions_path).mkdir(parents=True, exist_ok=True)

    client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    result = await client.send_code_request(phone)
    print(f"PHONE_CODE_HASH_START {result.phone_code_hash} PHONE_CODE_HASH_END")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
