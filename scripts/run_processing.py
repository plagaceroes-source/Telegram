"""Точка входа для processing worker (перевод/рерайт через Claude API)."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_agent.db.session import init_db  # noqa: E402
from news_agent.processing.worker import run_forever  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    await init_db()
    await run_forever()


if __name__ == "__main__":
    asyncio.run(main())
