"""Бот-предложка: подписчики присылают новости в личку, они попадают в обычную
очередь утверждения (DraftPost → карточка с кнопками Опубликовать/Редактировать/
Отклонить в approval-боте). Редактор может ответить автору прямо из карточки.

Второй бот нужен только для общения с подписчиками. Медиа он скачивает своим
токеном и перезаливает через основной бот в служебный чат (file_id привязан к
боту, а публикует основной), поэтому процессу нужны оба токена.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from sqlalchemy import select

from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import DraftPost, NewsSubmission, RawPost, Source
from news_agent.db.session import session_scope
from news_agent.services.media_relay import upload_and_get_ref

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

SOURCE_USERNAME = "suggest_bot"
ALBUM_WAIT = 1.5  # сек: сколько ждать остальные части альбома
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # лимит Bot API на скачивание файлов

_albums: dict[str, list[Message]] = {}
# Сильные ссылки на фоновые задачи: event loop держит их только слабо (иначе GC
# может убить задачу во время sleep).
_tasks: set[asyncio.Task] = set()


class MediaTooLarge(Exception):
    pass


@router.message(Command("start"))
async def on_start(message: Message) -> None:
    await message.answer(
        "Привет! Здесь можно предложить новость для канала.\n\n"
        "Просто пришлите текст, фото или видео (можно с подписью) — редактор посмотрит "
        "и, если подойдёт, опубликует. Если понадобятся уточнения, он ответит вам прямо здесь."
    )


@router.message(F.reply_to_message.from_user.is_bot, F.text)
async def on_reply_to_editor(message: Message, main_bot: Bot) -> None:
    """Ответ автора на сообщение редактора — не новая новость, а реплика в диалоге."""
    user = message.from_user
    draft_id = await _last_draft_id(user.id)
    keyboard = None
    if draft_id:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить автору", callback_data=f"reply:{draft_id}")]]
        )
    text = f"💬 Ответ автора {_label(user)}:\n\n{message.text}"
    if not await _notify_approvers(main_bot, text, keyboard):
        await message.answer("Не удалось передать сообщение редактору, попробуйте позже.")
        return
    await message.answer("Передал редактору.")


@router.message((F.text & ~F.text.startswith("/")) | F.photo | F.video)
async def on_submission(message: Message, bot: Bot, main_bot: Bot) -> None:
    if not message.media_group_id:
        await _process_submission([message], bot, main_bot)
        return

    group_id = message.media_group_id
    is_first = group_id not in _albums
    _albums.setdefault(group_id, []).append(message)
    if is_first:
        task = asyncio.create_task(_flush_album(group_id, bot, main_bot))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


@router.message()
async def on_unsupported(message: Message) -> None:
    await message.answer("Принимаю текст, фото и видео. Пришлите новость в таком виде.")


async def _flush_album(group_id: str, bot: Bot, main_bot: Bot) -> None:
    await asyncio.sleep(ALBUM_WAIT)
    messages = sorted(_albums.pop(group_id, []), key=lambda m: m.message_id)
    if messages:
        await _process_submission(messages, bot, main_bot)


def _label(user: User) -> str:
    name = user.first_name or "без имени"
    return f"{name} (@{user.username})" if user.username else f"{name} (id {user.id})"


async def _relay_media(message: Message, bot: Bot, main_bot: Bot) -> str | None:
    if message.photo:
        file, suffix = message.photo[-1], ".jpg"
    elif message.video:
        file, suffix = message.video, ".mp4"
    else:
        return None

    if file.file_size and file.file_size > MAX_DOWNLOAD_BYTES:
        raise MediaTooLarge
    if not settings.storage_chat_id:
        raise RuntimeError("Не задан STORAGE_CHAT_ID/APPROVAL_CHAT_ID — некуда перезаливать медиа")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{file.file_unique_id}{suffix}"
        await bot.download(file, destination=path)
        return await upload_and_get_ref(main_bot, settings.storage_chat_id, str(path))


async def _process_submission(messages: list[Message], bot: Bot, main_bot: Bot) -> None:
    first = messages[0]
    text = next((m.caption or m.text for m in messages if m.caption or m.text), "").strip()

    refs: list[str] = []
    try:
        for m in messages:
            ref = await _relay_media(m, bot, main_bot)
            if ref:
                refs.append(ref)
    except MediaTooLarge:
        await first.answer("Файл слишком большой — принимаю фото и видео до 20 МБ.")
        return
    except Exception:
        logger.exception("Не удалось принять предложку от %s", first.from_user.id)
        await first.answer("Не удалось принять сообщение, попробуйте ещё раз чуть позже.")
        return

    if not text and not refs:
        return

    await _save_submission(first.from_user, first.message_id, text, refs)
    await first.answer("Спасибо! Новость отправлена редактору. Если понадобятся уточнения — он ответит вам здесь.")
    logger.info("Предложка от %s: текст=%d симв., медиа=%d", first.from_user.id, len(text), len(refs))


async def _save_submission(user: User, message_id: int, text: str, refs: list[str]) -> None:
    async with session_scope() as session:
        result = await session.execute(select(Source).where(Source.username == SOURCE_USERNAME))
        source = result.scalars().first()
        if source is None:
            # Служебный источник: RawPost требует source_id. active=False — юзербот его не слушает.
            source = Source(username=SOURCE_USERNAME, title="Предложка", mode="as_is", active=False, joined=True)
            session.add(source)
            await session.flush()

        raw_post = RawPost(
            source_id=source.id, tg_message_id=message_id, text=text, media_paths=refs, status="done"
        )
        session.add(raw_post)
        await session.flush()

        draft = DraftPost(
            raw_post_id=raw_post.id, translated_text=text, media_paths=refs, status="pending_approval"
        )
        session.add(draft)
        await session.flush()

        session.add(
            NewsSubmission(
                draft_post_id=draft.id,
                tg_user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
            )
        )


async def _last_draft_id(user_id: int) -> int | None:
    async with session_scope() as session:
        result = await session.execute(
            select(NewsSubmission.draft_post_id)
            .where(NewsSubmission.tg_user_id == user_id)
            .order_by(NewsSubmission.id.desc())
            .limit(1)
        )
        return result.scalars().first()


async def _notify_approvers(main_bot: Bot, text: str, keyboard: InlineKeyboardMarkup | None) -> bool:
    recipients = [settings.approval_chat_id] if settings.approval_chat_id else settings.approver_chat_ids
    delivered = False
    for chat_id in recipients:
        try:
            await main_bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            delivered = True
        except Exception:
            logger.exception("Не удалось передать ответ автора в чат %s", chat_id)
    return delivered


async def run_forever() -> None:
    if not settings.suggest_bot_token:
        raise SystemExit("SUGGEST_BOT_TOKEN не задан")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан — он нужен для передачи медиа и ответов автора редактору")

    bot = build_bot(settings.suggest_bot_token)
    main_bot = build_bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    dp["main_bot"] = main_bot

    try:
        await dp.start_polling(bot)
    finally:
        await main_bot.session.close()
