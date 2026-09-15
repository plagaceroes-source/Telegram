"""Google Gemini API wrapper — альтернативный провайдер перевода/рерайта (есть бесплатный тариф,
удобно для проверки пайплайна без затрат; при желании переключиться обратно на Claude — просто
поменять AI_PROVIDER в .env, интерфейс модуля идентичен news_agent.services.claude).
"""
from __future__ import annotations

from google import genai

from news_agent.config import settings
from news_agent.services.prompts import build_rewrite_prompt, build_translate_prompt

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def translate_and_rewrite(text: str, source_lang: str, target_lang: str) -> str:
    if not text.strip():
        return text
    return await _complete(build_translate_prompt(text, source_lang, target_lang))


async def rewrite_only(text: str, target_lang: str) -> str:
    if not text.strip():
        return text
    return await _complete(build_rewrite_prompt(text, target_lang))


async def _complete(prompt: str) -> str:
    client = _get_client()
    response = await client.aio.models.generate_content(model=settings.gemini_model, contents=prompt)
    return (response.text or "").strip()
