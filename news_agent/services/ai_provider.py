"""Выбор LLM-провайдера для перевода/рерайта через AI_PROVIDER в .env (claude / gemini).

processing/worker.py импортирует translate_and_rewrite/rewrite_only отсюда, а не напрямую
из claude.py или gemini.py — так смена провайдера не требует правок в остальном коде.
"""
from __future__ import annotations

from news_agent.config import settings


async def translate_and_rewrite(text: str, source_lang: str, target_lang: str) -> str:
    if settings.ai_provider == "gemini":
        from news_agent.services import gemini as backend
    else:
        from news_agent.services import claude as backend
    return await backend.translate_and_rewrite(text, source_lang, target_lang)


async def rewrite_only(text: str, target_lang: str) -> str:
    if settings.ai_provider == "gemini":
        from news_agent.services import gemini as backend
    else:
        from news_agent.services import claude as backend
    return await backend.rewrite_only(text, target_lang)
