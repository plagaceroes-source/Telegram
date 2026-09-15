"""Claude API wrapper for translation + light rewrite (ТЗ 2.2)."""
from __future__ import annotations

from anthropic import AsyncAnthropic

from news_agent.config import settings

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


_TRANSLATE_REWRITE_PROMPT = """\
Ты — редактор новостного Telegram-канала. Тебе дан пост на языке "{source_lang}".
Целевой язык публикации — "{target_lang}".

Задача:
1. Переведи текст на {target_lang}, если он не на этом языке.
2. Сделай лёгкий рерайт: убери воду, приведи к живому новостному стилю коротких
   Telegram-постов, но НЕ теряй фактуру (цифры, имена, даты, цитаты должны
   остаться точными).
3. Не добавляй ничего от себя, не домысливай, не давай оценок и комментариев.
4. Не добавляй хэштеги, эмодзи и подписи источника, если их не было в оригинале.
5. Верни только готовый текст поста, без пояснений и без кавычек вокруг него.

Текст поста:
---
{text}
---
"""

_REWRITE_ONLY_PROMPT = """\
Ты — редактор новостного Telegram-канала. Тебе дан пост, уже написанный на нужном
языке публикации ("{target_lang}").

Сделай лёгкий рерайт: убери воду, приведи к живому новостному стилю коротких
Telegram-постов, но НЕ теряй фактуру (цифры, имена, даты, цитаты должны остаться
точными). Не добавляй ничего от себя. Верни только готовый текст поста, без
пояснений и без кавычек вокруг него.

Текст поста:
---
{text}
---
"""


async def translate_and_rewrite(text: str, source_lang: str, target_lang: str) -> str:
    """Translate `text` from source_lang into target_lang with a light rewrite."""
    if not text.strip():
        return text
    prompt = _TRANSLATE_REWRITE_PROMPT.format(source_lang=source_lang, target_lang=target_lang, text=text)
    return await _complete(prompt)


async def rewrite_only(text: str, target_lang: str) -> str:
    """Light rewrite without translation, for sources already in the target language."""
    if not text.strip():
        return text
    prompt = _REWRITE_ONLY_PROMPT.format(target_lang=target_lang, text=text)
    return await _complete(prompt)


async def _complete(prompt: str) -> str:
    client = _get_client()
    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "".join(parts).strip()
