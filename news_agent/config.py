"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _list_int(name: str) -> list[int]:
    raw = os.getenv(name, "")
    return [int(x) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int = field(default_factory=lambda: _int("TELEGRAM_API_ID", 0))
    telegram_api_hash: str = field(default_factory=lambda: os.getenv("TELEGRAM_API_HASH", ""))

    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    approver_chat_ids: list[int] = field(default_factory=lambda: _list_int("APPROVER_CHAT_IDS"))

    # Какой LLM использовать для перевода/рерайта: "claude" или "gemini".
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "claude"))

    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5"))

    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))

    target_language: str = field(default_factory=lambda: os.getenv("TARGET_LANGUAGE", "ru"))

    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./storage/news_agent.db")
    )

    media_storage_path: str = field(default_factory=lambda: os.getenv("MEDIA_STORAGE_PATH", "./storage/incoming"))
    sessions_path: str = field(default_factory=lambda: os.getenv("SESSIONS_PATH", "./storage/sessions"))

    admin_secret_key: str = field(default_factory=lambda: os.getenv("ADMIN_SECRET_KEY", "change-me"))
    admin_port: int = field(default_factory=lambda: _int("ADMIN_PORT", 8000))

    sources_poll_interval: int = field(default_factory=lambda: _int("SOURCES_POLL_INTERVAL", 120))
    processing_poll_interval: int = field(default_factory=lambda: _int("PROCESSING_POLL_INTERVAL", 15))

    join_pause_min: int = field(default_factory=lambda: _int("JOIN_PAUSE_MIN", 30))
    join_pause_max: int = field(default_factory=lambda: _int("JOIN_PAUSE_MAX", 120))

    # Soft cap on sources per userbot account (Section 2.1: ориентир 20-40).
    max_sources_per_account: int = field(default_factory=lambda: _int("MAX_SOURCES_PER_ACCOUNT", 35))


settings = Settings()
