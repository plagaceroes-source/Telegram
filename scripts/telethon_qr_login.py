"""Вход аккаунта юзербота по QR-коду — когда код входа не приходит (нет SIM).

Запускается на своём компьютере. В Терминале появится QR; на телефоне, где аккаунт уже
открыт: Telegram → Настройки → Устройства → Подключить устройство → навести камеру.
В конце печатается строка сессии — её кладут в переменную USERBOT_SESSION_STRING
сервиса userbot. Строка даёт полный доступ к аккаунту: никому не пересылать.

    python3 -m venv ~/tgsession && ~/tgsession/bin/pip install telethon qrcode
    ~/tgsession/bin/python3 telethon_qr_login.py
"""
import asyncio
import os

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


def show_qr(url: str) -> None:
    code = qrcode.QRCode(border=2)
    code.add_data(url)
    code.make()
    code.print_ascii(invert=True)  # invert=True — для тёмного фона Терминала


async def main() -> None:
    api_id = int(os.environ.get("TELEGRAM_API_ID") or input("api_id: "))
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("api_hash: ")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        qr = await client.qr_login()
        while True:
            print("\nОтсканируйте QR в приложении Telegram: Настройки → Устройства → Подключить устройство\n")
            show_qr(qr.url)
            try:
                await qr.wait(30)  # QR живёт ~30 с, потом выдаём новый
                break
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                await client.sign_in(password=input("Пароль двухфакторной защиты: "))
                break
        me = await client.get_me()
        print(f"\nВход выполнен: {me.first_name} (id {me.id}). Строка сессии — между линиями:\n")
        print("-" * 20)
        print(client.session.save())
        print("-" * 20)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
