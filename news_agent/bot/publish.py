"""Логика публикации одобренного черновика в целевой канал (ТЗ 2.4)."""
from __future__ import annotations

import html
import logging
import shutil
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InputMediaPhoto, InputMediaVideo, LinkPreviewOptions

from news_agent.db.models import DraftPost, PublishedPost, TargetChannel, utcnow
from news_agent.db.session import session_scope
from news_agent.services.media_relay import CAPTION_LIMIT, is_file_ref, resolve_media

logger = logging.getLogger(__name__)

SUBSCRIBE_LINK = "https://t.me/+pvdWFH9kAwk1OTQ0"


def _with_subscribe_link(text: str) -> str:
    """Добавляет кликабельную ссылку «ПОДПИСАТЬСЯ» в конец каждого публикуемого поста."""
    return f'{html.escape(text)}\n\n<a href="{SUBSCRIBE_LINK}">ПОДПИСАТЬСЯ</a>'


def _cleanup_media(media_paths: list[str]) -> None:
    """Удаляет временные медиафайлы после публикации/отклонения (раздел 5: медиа временное).

    Ссылки tgfile:... не занимают места на диске этого сервиса — их нечего удалять."""
    dirs_to_remove = set()
    for path in media_paths:
        if is_file_ref(path):
            continue
        p = Path(path)
        dirs_to_remove.add(p.parent)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить файл медиа %s", path)
    for d in dirs_to_remove:
        try:
            if d.exists() and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


async def publish_draft(bot: Bot, draft_id: int, target_channel_id: int, decided_by: str) -> None:
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            raise ValueError(f"draft_post {draft_id} не найден")
        target = await session.get(TargetChannel, target_channel_id)
        if target is None:
            raise ValueError(f"target_channel {target_channel_id} не найден")

        text = _with_subscribe_link(draft.translated_text)
        media_paths = list(draft.media_paths)
        chat_id = target.tg_chat_id or f"@{target.username}"

    tg_message_id: int
    if not media_paths:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        tg_message_id = message.message_id
    elif len(media_paths) == 1 and len(text) <= CAPTION_LIMIT:
        media_type, source = resolve_media(media_paths[0])
        if media_type == "video":
            message = await bot.send_video(chat_id=chat_id, video=source, caption=text, parse_mode=ParseMode.HTML)
        else:
            message = await bot.send_photo(chat_id=chat_id, photo=source, caption=text, parse_mode=ParseMode.HTML)
        tg_message_id = message.message_id
    elif len(media_paths) == 1:
        # Текст не влезает в лимит подписи к медиа (1024 симв.) — иначе Telegram
        # отклонит весь запрос, и пост не опубликуется вовсе. Шлём медиа без
        # подписи и текст отдельным сообщением следом.
        media_type, source = resolve_media(media_paths[0])
        if media_type == "video":
            await bot.send_video(chat_id=chat_id, video=source)
        else:
            await bot.send_photo(chat_id=chat_id, photo=source)
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        tg_message_id = message.message_id
    elif len(text) <= CAPTION_LIMIT:
        media_group = []
        for i, path in enumerate(media_paths):
            media_type, source = resolve_media(path)
            caption = text if i == 0 else None
            if media_type == "video":
                media_group.append(InputMediaVideo(media=source, caption=caption, parse_mode=ParseMode.HTML))
            else:
                media_group.append(InputMediaPhoto(media=source, caption=caption, parse_mode=ParseMode.HTML))
        messages = await bot.send_media_group(chat_id=chat_id, media=media_group)
        tg_message_id = messages[0].message_id
    else:
        media_group = []
        for path in media_paths:
            media_type, source = resolve_media(path)
            if media_type == "video":
                media_group.append(InputMediaVideo(media=source))
            else:
                media_group.append(InputMediaPhoto(media=source))
        messages = await bot.send_media_group(chat_id=chat_id, media=media_group)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        tg_message_id = messages[0].message_id

    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        draft.status = "published"
        draft.decided_by = decided_by
        draft.decided_at = utcnow()
        session.add(
            PublishedPost(
                draft_post_id=draft_id,
                target_channel_id=target_channel_id,
                tg_message_id=tg_message_id,
            )
        )

    _cleanup_media(media_paths)
    logger.info("Черновик %s опубликован в канал %s (message_id=%s)", draft_id, target.username, tg_message_id)


async def reject_draft(draft_id: int, decided_by: str) -> None:
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            return
        draft.status = "rejected"
        draft.decided_by = decided_by
        draft.decided_at = utcnow()
        media_paths = list(draft.media_paths)

    _cleanup_media(media_paths)
    logger.info("Черновик %s отклонён пользователем %s", draft_id, decided_by)
