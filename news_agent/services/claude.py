"""Claude API wrapper for translation + light rewrite (ТЗ 2.2)."""
from __future__ import annotations

from anthropic import AsyncAnthropic

from news_agent.config import settings
from news_agent.services.prompts import build_rewrite_prompt, build_translate_prompt

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def translate_and_rewrite(text: str, source_lang: str, target_lang: str) -> str:
    """Translate `text` from source_lang into target_lang with a light rewrite."""
    if not text.strip():
        return text
    return await _complete(build_translate_prompt(text, source_lang, target_lang))


async def rewrite_only(text: str, target_lang: str) -> str:
    """Light rewrite without translation, for sources already in the target language."""
    if not text.strip():
        return text
    return await _complete(build_rewrite_prompt(text, target_lang))


async def _complete(prompt: str) -> str:
    client = _get_client()
    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "".join(parts).strip()
