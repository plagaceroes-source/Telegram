"""Processing core: перевод/рерайт сырых постов через Claude API (ТЗ 2.2)."""
from __future__ import annotations

import asyncio
import logging

from google.genai.errors import ClientError
from sqlalchemy import select

from news_agent.config import settings
from news_agent.db.models import DraftPost, RawPost, Source
from news_agent.db.session import session_scope
from news_agent.services.ai_provider import rewrite_only, translate_and_rewrite

logger = logging.getLogger(__name__)

# Пауза перед следующим циклом, если провайдер вернул 429 (исчерпана квота) —
# обычный интервал опроса (секунды) только зря сожжёт квоту повторными попытками.
RATE_LIMIT_COOLDOWN = 300


def _is_rate_limit(exc: Exception) -> bool:
    return isinstance(exc, ClientError) and exc.code == 429


async def process_pending_posts() -> tuple[int, bool]:
    """Обрабатывает все посты в статусе pending.

    Возвращает (число обработанных, был ли пойман 429 от AI-провайдера)."""
    async with session_scope() as session:
        result = await session.execute(
            select(RawPost).where(RawPost.status == "pending").order_by(RawPost.collected_at)
        )
        pending = list(result.scalars())
        for raw_post in pending:
            raw_post.status = "processing"
        raw_post_ids = [rp.id for rp in pending]

    processed = 0
    rate_limited = False
    for raw_post_id in raw_post_ids:
        try:
            await _process_one(raw_post_id)
            processed += 1
        except Exception as exc:
            if _is_rate_limit(exc):
                # Временная ситуация (исчерпана квота AI-провайдера) — возвращаем
                # пост в очередь, чтобы он обработался, когда квота освободится,
                # а не застревал в error навсегда без вмешательства.
                logger.warning("raw_post id=%s: провайдер вернул 429, возвращаю в очередь", raw_post_id)
                rate_limited = True
                async with session_scope() as session:
                    raw_post = await session.get(RawPost, raw_post_id)
                    if raw_post:
                        raw_post.status = "pending"
            else:
                logger.exception("Ошибка обработки raw_post id=%s", raw_post_id)
                async with session_scope() as session:
                    raw_post = await session.get(RawPost, raw_post_id)
                    if raw_post:
                        raw_post.status = "error"
    return processed, rate_limited


async def _process_one(raw_post_id: int) -> None:
    async with session_scope() as session:
        raw_post = await session.get(RawPost, raw_post_id)
        if raw_post is None:
            return
        source = await session.get(Source, raw_post.source_id)
        text = raw_post.text
        media_paths = list(raw_post.media_paths)
        source_lang = source.lang if source and source.lang != "auto" else raw_post.detected_lang
        mode = source.mode if source else "rewrite"

    target_lang = settings.target_language

    if not text.strip():
        translated_text = ""
    elif source_lang and source_lang != target_lang:
        translated_text = await translate_and_rewrite(text, source_lang, target_lang)
    elif mode == "as_is":
        translated_text = text
    else:
        translated_text = await rewrite_only(text, target_lang)

    async with session_scope() as session:
        raw_post = await session.get(RawPost, raw_post_id)
        if raw_post is None:
            return
        raw_post.status = "done"

        draft = DraftPost(
            raw_post_id=raw_post_id,
            translated_text=translated_text,
            media_paths=media_paths,
            status="pending_approval",
        )
        session.add(draft)

    logger.info("raw_post id=%s обработан, создан черновик", raw_post_id)


async def run_forever() -> None:
    logger.info("Processing worker запущен, интервал опроса %sс", settings.processing_poll_interval)
    while True:
        interval = settings.processing_poll_interval
        try:
            count, rate_limited = await process_pending_posts()
            if count:
                logger.info("Обработано постов: %d", count)
            if rate_limited:
                interval = RATE_LIMIT_COOLDOWN
        except Exception:
            logger.exception("Ошибка в цикле processing worker")
        await asyncio.sleep(interval)
