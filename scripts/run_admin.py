"""Точка входа для веб-админки (ТЗ 2.5, деплой как Web Service на Render)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from news_agent.config import settings  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("admin.app:app", host="0.0.0.0", port=settings.admin_port, reload=False)
