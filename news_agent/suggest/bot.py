"""Отдельный бот «предложка»: подписчики присылают новости в личку, редактор
разбирает их прямо в этом же боте — карточка с кнопками Опубликовать /
Редактировать / Ответить автору / Отклонить приходит в SUGGEST_CHAT_ID (или в личку
из APPROVER_CHAT_IDS). С апрув-ботом не связан: свой токен, свои карточки, а
публикует в канал сам (бот должен быть админом целевого канала).

Новость хранится как DraftPost со статусом "suggested" — апрув-бот берёт только
"pending_approval", так что чужих карточек не создаёт. Медиа не скачиваем:
file_id, полученный от подписчика, валиден для этого же бота при публикации.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    User,
)
from sqlalchemy import select

from news_agent.bot.publish import publish_draft, reject_draft
from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import DraftPost, NewsSubmission, RawPost, Source, TargetChannel
from news_agent.db.session import session_scope
from news_agent.services.media_relay import CAPTION_LIMIT, encode_ref, resolve_media

logger = logging.getLogger(__name__)

STATUS_OPEN = "suggested"
SOURCE_USERNAME = "suggest_bot"
ALBUM_WAIT = 1.5  # сек: сколько ждать остальные части альбома
TEXT_LIMIT = 4000  # лимит текстового сообщения Telegram 4096, с запасом на заголовок

intake = Router()  # приём новостей от подписчиков — только личка
intake.message.filter(F.chat.type == "private")
review = Router()  # разбор редактором: кнопки и текст правки/ответа — в любом чате

_albums: dict[str, list[Message]] = {}
# user_id -> (действие, draft_id, чат и id карточки, на которой нажали кнопку)
_awaiting: dict[int, tuple[str, int, int, int]] = {}
# Сильные ссылки на фоновые задачи: event loop держит их только слабо (иначе GC
# может убить задачу во время sleep).
_tasks: set[asyncio.Task] = set()


def _review_chats() -> list[int]:
    return [settings.suggest_chat_id] if settings.suggest_chat_id else list(settings.approver_chat_ids)


def _is_editor(user_id: int) -> bool:
    return not settings.approver_chat_ids or user_id in settings.approver_chat_ids


def _label(user: User) -> str:
    name = user.first_name or "без имени"
    return f"{name} (@{user.username})" if user.username else f"{name} (id {user.id})"


# ---------------------------------------------------------------- приём новостей

@intake.message(Command("start"))
async def on_start(message: Message) -> None:
    await message.answer(
        "Привет! Здесь можно предложить новость для канала.\n\n"
        "Просто пришлите текст, фото или видео (можно с подписью) — редактор посмотрит "
        "и, если подойдёт, опубликует. Если понадобятся уточнения, он ответит вам прямо здесь."
    )


@intake.message(F.reply_to_message.from_user.is_bot, F.text)
async def on_reply_to_editor(message: Message, bot: Bot) -> None:
    """Ответ автора на сообщение редактора — не новая новость, а реплика в диалоге."""
    draft_id = await _last_draft_id(message.from_user.id)
    keyboard = None
    if draft_id:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить автору", callback_data=f"sreply:{draft_id}")]]
        )
    delivered = False
    for chat_id in _review_chats():
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"💬 Ответ автора {_label(message.from_user)}:\n\n{message.text}"[:TEXT_LIMIT],
                reply_markup=keyboard,
            )
            delivered = True
        except Exception:
            logger.exception("Не удалось передать ответ автора в чат %s", chat_id)
    await message.answer("Передал редактору." if delivered else "Не удалось передать сообщение, попробуйте позже.")


@intake.message((F.text & ~F.text.startswith("/")) | F.photo | F.video)
async def on_submission(message: Message, bot: Bot) -> None:
    if not message.media_group_id:
        await _process_submission([message], bot)
        return

    group_id = message.media_group_id
    is_first = group_id not in _albums
    _albums.setdefault(group_id, []).append(message)
    if is_first:
        task = asyncio.create_task(_flush_album(group_id, bot))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


@intake.message()
async def on_unsupported(message: Message) -> None:
    await message.answer("Принимаю текст, фото и видео. Пришлите новость в таком виде.")


async def _flush_album(group_id: str, bot: Bot) -> None:
    await asyncio.sleep(ALBUM_WAIT)
    messages = sorted(_albums.pop(group_id, []), key=lambda m: m.message_id)
    if messages:
        await _process_submission(messages, bot)


def _media_ref(message: Message) -> str | None:
    if message.photo:
        return encode_ref("photo", message.photo[-1].file_id)
    if message.video:
        return encode_ref("video", message.video.file_id)
    return None


async def _process_submission(messages: list[Message], bot: Bot) -> None:
    first = messages[0]
    text = next((m.caption or m.text for m in messages if m.caption or m.text), "").strip()
    refs = [ref for m in messages if (ref := _media_ref(m))]
    if not text and not refs:
        return

    draft_id = await _save_submission(first.from_user, first.message_id, text, refs)
    await first.answer("Спасибо! Новость отправлена редактору. Если понадобятся уточнения — он ответит вам здесь.")
    logger.info("Предложка от %s: текст=%d симв., медиа=%d", first.from_user.id, len(text), len(refs))
    await send_card(bot, draft_id)


async def _save_submission(user: User, message_id: int, text: str, refs: list[str]) -> int:
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

        draft = DraftPost(raw_post_id=raw_post.id, translated_text=text, media_paths=refs, status=STATUS_OPEN)
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
        return draft.id


async def _last_draft_id(user_id: int) -> int | None:
    async with session_scope() as session:
        result = await session.execute(
            select(NewsSubmission.draft_post_id)
            .where(NewsSubmission.tg_user_id == user_id)
            .order_by(NewsSubmission.id.desc())
            .limit(1)
        )
        return result.scalars().first()


# ---------------------------------------------------------------- карточки

async def _build_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))
        targets = list(result.scalars())

    rows: list[list[InlineKeyboardButton]] = []
    if len(targets) <= 1:
        target_id = targets[0].id if targets else 0
        rows.append([InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"spub:{draft_id}:{target_id}")])
    else:
        for target in targets:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ В «{target.title or target.username}»",
                        callback_data=f"spub:{draft_id}:{target.id}",
                    )
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"sedit:{draft_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"srej:{draft_id}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="💬 Ответить автору", callback_data=f"sreply:{draft_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_card(bot: Bot, draft_id: int, include_album: bool = True) -> None:
    """include_album=False — при повторной карточке после правки текста: альбом из нескольких
    файлов уже висит в чате, второй раз его слать не нужно."""
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        submission = (
            await session.execute(select(NewsSubmission).where(NewsSubmission.draft_post_id == draft_id))
        ).scalars().first()
        if draft is None or submission is None:
            return
        author = submission.first_name or "без имени"
        if submission.username:
            author += f" (@{submission.username})"
        caption = f"📨 Предложка от {author}\n\n{draft.translated_text}"
        media_refs = list(draft.media_paths)

    keyboard = await _build_keyboard(draft_id)
    for chat_id in _review_chats():
        try:
            await _send_card_to(bot, chat_id, caption, keyboard, media_refs, include_album)
        except Exception:
            logger.exception("Не удалось отправить карточку предложки %s в чат %s", draft_id, chat_id)


def _input_media(ref: str) -> InputMediaPhoto | InputMediaVideo:
    media_type, source = resolve_media(ref)
    return InputMediaVideo(media=source) if media_type == "video" else InputMediaPhoto(media=source)


async def _send_card_to(
    bot: Bot,
    chat_id: int,
    caption: str,
    keyboard: InlineKeyboardMarkup,
    media_refs: list[str],
    include_album: bool = True,
) -> None:
    if not media_refs:
        await bot.send_message(chat_id=chat_id, text=caption[:TEXT_LIMIT], reply_markup=keyboard)
        return

    if len(media_refs) > 1:
        # У альбома не бывает кнопок — шлём все файлы альбомом, а текст с кнопками следом.
        if include_album:
            await bot.send_media_group(chat_id=chat_id, media=[_input_media(ref) for ref in media_refs])
        await bot.send_message(chat_id=chat_id, text=caption[:TEXT_LIMIT], reply_markup=keyboard)
        return

    media_type, source = resolve_media(media_refs[0])
    send = bot.send_video if media_type == "video" else bot.send_photo
    media_arg = {"video" if media_type == "video" else "photo": source}
    if len(caption) > CAPTION_LIMIT:
        # Подпись к медиа ограничена 1024 символами — шлём медиа без неё, текст с кнопками следом.
        await send(chat_id=chat_id, **media_arg)
        await bot.send_message(chat_id=chat_id, text=caption[:TEXT_LIMIT], reply_markup=keyboard)
    else:
        await send(chat_id=chat_id, caption=caption, reply_markup=keyboard, **media_arg)


# ---------------------------------------------------------------- разбор редактором

async def _still_open(callback: CallbackQuery, draft_id: int) -> bool:
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        status = draft.status if draft else None
    if status != STATUS_OPEN:
        await callback.answer("Эта новость уже обработана.", show_alert=True)
        return False
    return True


async def _mark_card(message: Message, suffix: str) -> None:
    if message.caption:
        await message.edit_caption(caption=message.caption + suffix, reply_markup=None)
    else:
        await message.edit_text((message.text or "") + suffix, reply_markup=None)


@review.callback_query(F.data.startswith("spub:"))
async def on_publish(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_editor(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, draft_id_str, target_id_str = callback.data.split(":")
    draft_id, target_id = int(draft_id_str), int(target_id_str)
    if not target_id:
        await callback.answer("Нет ни одного целевого канала. Добавьте канал в сетку.", show_alert=True)
        return
    if not await _still_open(callback, draft_id):
        return

    await callback.answer("Публикую…")
    try:
        await publish_draft(bot, draft_id, target_id, decided_by=str(callback.from_user.id))
    except Exception:
        logger.exception("Ошибка публикации предложки %s", draft_id)
        await callback.message.reply(
            "Не удалось опубликовать. Проверьте, что этот бот — админ канала с правом публикации."
        )
        return
    await _mark_card(callback.message, "\n\n✅ Опубликовано")


@review.callback_query(F.data.startswith("srej:"))
async def on_reject(callback: CallbackQuery) -> None:
    if not _is_editor(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    draft_id = int(callback.data.split(":")[1])
    if not await _still_open(callback, draft_id):
        return
    await reject_draft(draft_id, decided_by=str(callback.from_user.id))
    await callback.answer("Отклонено")
    await _mark_card(callback.message, "\n\n❌ Отклонено")


async def _ask(callback: CallbackQuery, action: str, prompt: str) -> None:
    if not _is_editor(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    draft_id = int(callback.data.split(":")[1])
    if action == "edit" and not await _still_open(callback, draft_id):
        return
    _awaiting[callback.from_user.id] = (action, draft_id, callback.message.chat.id, callback.message.message_id)
    await callback.answer()
    # force_reply: в группе с privacy mode бот получает только команды и ответы на свои сообщения.
    await callback.message.reply(prompt.format(draft_id=draft_id), reply_markup=ForceReply())


@review.callback_query(F.data.startswith("sedit:"))
async def on_edit_request(callback: CallbackQuery) -> None:
    await _ask(callback, "edit", "Пришлите следующим сообщением новый текст для новости #{draft_id}.")


@review.callback_query(F.data.startswith("sreply:"))
async def on_reply_request(callback: CallbackQuery) -> None:
    await _ask(callback, "reply", "Пришлите следующим сообщением ответ автору новости #{draft_id}.")


def _has_pending(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in _awaiting


@review.message(F.text, _has_pending)
async def on_pending_text(message: Message, bot: Bot) -> None:
    action, draft_id, card_chat_id, card_message_id = _awaiting.pop(message.from_user.id)
    if action == "edit":
        await _apply_edit(message, bot, draft_id, card_chat_id, card_message_id)
    else:
        await _send_to_author(message, bot, draft_id)


async def _apply_edit(message: Message, bot: Bot, draft_id: int, card_chat_id: int, card_message_id: int) -> None:
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None or draft.status != STATUS_OPEN:
            await message.reply("Эта новость уже обработана или не найдена.")
            return
        draft.translated_text = message.text
    try:  # старая карточка с прежним текстом больше не нужна
        await bot.delete_message(chat_id=card_chat_id, message_id=card_message_id)
    except Exception:
        logger.warning("Не удалось удалить старую карточку %s", card_message_id)
    await message.reply(f"Новость #{draft_id} обновлена, присылаю новую карточку.")
    await send_card(bot, draft_id, include_album=False)


async def _send_to_author(message: Message, bot: Bot, draft_id: int) -> None:
    async with session_scope() as session:
        submission = (
            await session.execute(select(NewsSubmission).where(NewsSubmission.draft_post_id == draft_id))
        ).scalars().first()
    if submission is None:
        await message.reply("Автор этой новости не найден.")
        return
    try:
        await bot.send_message(chat_id=submission.tg_user_id, text=f"✉️ Ответ редактора:\n\n{message.text}")
    except Exception:
        logger.exception("Не удалось отправить ответ автору новости %s", draft_id)
        await message.reply("Не удалось отправить — возможно, автор заблокировал бота.")
        return
    await message.reply("Ответ отправлен автору.")


async def run_forever() -> None:
    if not settings.suggest_bot_token:
        raise SystemExit("SUGGEST_BOT_TOKEN не задан")
    if not _review_chats():
        raise SystemExit("Не задан ни SUGGEST_CHAT_ID, ни APPROVER_CHAT_IDS — некуда присылать карточки")

    bot = build_bot(settings.suggest_bot_token)
    dp = Dispatcher()
    # review раньше intake: текст правки/ответа от редактора не должен стать новой новостью.
    dp.include_router(review)
    dp.include_router(intake)
    await dp.start_polling(bot)
