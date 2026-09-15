"""Точка входа для одного воркера юзербота (один процесс = один аккаунт, ТЗ 2.1).

Использование:
    USERBOT_ACCOUNT_ID=1 python scripts/run_userbot.py
или
    python scripts/run_userbot.py --account-id 1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.session import init_db  # noqa: E402
from news_agent.userbot.listener import run_account  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Запуск воркера юзербота для одного аккаунта")
    parser.add_argument("--account-id", type=int, default=None)
    args = parser.parse_args()

    account_id = args.account_id or int(os.environ.get("USERBOT_ACCOUNT_ID", "0"))
    if not account_id:
        raise SystemExit("Укажите --account-id или переменную окружения USERBOT_ACCOUNT_ID")

    await init_db()
    await run_account(account_id)


if __name__ == "__main__":
    asyncio.run(main())
