"""Передача медиа между юзерботом и ботом-апрувером через Telegram file_id.

userbot и bot — разные Railway-сервисы с разными дисками: локальный путь,
куда Telethon скачал файл, недоступен процессу бота. Поэтому юзербот сразу
после скачивания прогоняет файл через Bot API в служебный чат (STORAGE_CHAT_ID)
и сохраняет только Telegram file_id — он валиден для отправки из любого места.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
_PREFIX = "tgfile"

# Telegram ограничивает caption для фото/видео 1024 символами (у обычных текстовых
# сообщений лимит 4096) — длинный текст новости туда просто не влезает.
CAPTION_LIMIT = 1024


def encode_ref(media_type: str, file_id: str) -> str:
    return f"{_PREFIX}:{media_type}:{file_id}"


def is_file_ref(value: str) -> bool:
    return value.startswith(f"{_PREFIX}:")


def decode_ref(value: str) -> tuple[str, str]:
    _, media_type, file_id = value.split(":", 2)
    return media_type, file_id


def resolve_media(value: str) -> tuple[str, str | FSInputFile]:
    """Возвращает (media_type, источник для aiogram) для одной записи media_paths."""
    if is_file_ref(value):
        return decode_ref(value)
    # Устаревший формат (локальный путь) — сработает, только если bot и userbot
    # делят один диск/процесс.
    media_type = "video" if Path(value).suffix.lower() in _VIDEO_EXTS else "photo"
    return media_type, FSInputFile(value)


async def upload_and_get_ref(bot: Bot, chat_id: int, local_path: str) -> str:
    """Загружает файл в служебный чат и возвращает ссылку вида tgfile:<type>:<file_id>."""
    file = FSInputFile(local_path)
    if Path(local_path).suffix.lower() in _VIDEO_EXTS:
        message = await bot.send_video(chat_id=chat_id, video=file)
        file_id = message.video.file_id
        media_type = "video"
    else:
        message = await bot.send_photo(chat_id=chat_id, photo=file)
        file_id = message.photo[-1].file_id
        media_type = "photo"

    # По умолчанию STORAGE_CHAT_ID совпадает с APPROVAL_CHAT_ID — без удаления
    # эта служебная заливка (без текста и кнопок) засоряла бы чат одобрения
    # отдельным "голым" сообщением перед каждой настоящей карточкой.
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        logger.warning("Не удалось удалить служебное сообщение-релей %s в чате %s", message.message_id, chat_id)

    return encode_ref(media_type, file_id)
