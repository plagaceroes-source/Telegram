"""Approval bot: карточки черновиков + кнопки Опубликовать/Редактировать/Отклонить (ТЗ 2.3)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from news_agent.bot import commands as admin_commands
from news_agent.bot import force_sub
from news_agent.bot.publish import publish_draft, reject_draft
from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import DraftPost, NewsSubmission, RawPost, Source, TargetChannel
from news_agent.db.session import session_scope
from news_agent.reports.scheduler import start_reports_scheduler
from news_agent.services.media_relay import CAPTION_LIMIT, resolve_media
from news_agent.stats.events import register_membership_handlers

logger = logging.getLogger(__name__)

router = Router()

# user_id -> draft_id для которого ожидается текст правки
_awaiting_edit: dict[int, int] = {}
# user_id -> draft_id, автору которого редактор пишет ответ (бот-предложка)
_awaiting_reply: dict[int, int] = {}

_suggest_bot: Bot | None = None


def _get_suggest_bot() -> Bot | None:
    """Бот-предложка нужен только чтобы отвечать авторам новостей — у автора есть
    диалог именно с ним, основной бот написать ему первым не может."""
    global _suggest_bot
    if not settings.suggest_bot_token:
        return None
    if _suggest_bot is None:
        _suggest_bot = build_bot(settings.suggest_bot_token)
    return _suggest_bot


def _is_approver(user_id: int) -> bool:
    return not settings.approver_chat_ids or user_id in settings.approver_chat_ids


def _approver_filter(message_or_query) -> bool:
    user = message_or_query.from_user
    return bool(user) and _is_approver(user.id)


async def _build_card_keyboard(draft_id: int, has_author: bool = False) -> InlineKeyboardMarkup:
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))
        targets = list(result.scalars())

    rows: list[list[InlineKeyboardButton]] = []
    if len(targets) <= 1:
        target_id = targets[0].id if targets else 0
        rows.append([InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{draft_id}:{target_id}")])
    else:
        for target in targets:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ В «{target.title or target.username}»",
                        callback_data=f"pub:{draft_id}:{target.id}",
                    )
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{draft_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej:{draft_id}"),
        ]
    )
    if has_author:
        rows.append([InlineKeyboardButton(text="💬 Ответить автору", callback_data=f"reply:{draft_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _card_caption(
    draft: DraftPost, raw_post: RawPost, source: Source | None, submission: NewsSubmission | None = None
) -> str:
    if submission is not None:
        author = submission.first_name or "без имени"
        if submission.username:
            author += f" (@{submission.username})"
        header = f"📨 Предложка от {author}\n\n"
    else:
        source_label = source.title or source.username if source else "неизвестный источник"
        header = f"📰 Источник: {source_label}\n\n"
    return header + draft.translated_text


async def send_draft_card(bot: Bot, draft_id: int) -> None:
    """Отправляет карточку черновика: одну в групповой чат (APPROVAL_CHAT_ID),
    либо каждому утверждающему лично (APPROVER_CHAT_IDS), если группа не задана."""
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            return
        raw_post = await session.get(RawPost, draft.raw_post_id)
        source = await session.get(Source, raw_post.source_id) if raw_post else None
        submission = (
            await session.execute(select(NewsSubmission).where(NewsSubmission.draft_post_id == draft_id))
        ).scalars().first()
        caption = await _card_caption(draft, raw_post, source, submission)
        media_paths = list(draft.media_paths)

    keyboard = await _build_card_keyboard(draft_id, has_author=submission is not None)
    recipients = [settings.approval_chat_id] if settings.approval_chat_id else (settings.approver_chat_ids or [])

    for chat_id in recipients:
        try:
            if not media_paths:
                message = await bot.send_message(chat_id=chat_id, text=caption, reply_markup=keyboard)
            else:
                media_type, source = resolve_media(media_paths[0])
                full_caption = caption if len(media_paths) == 1 else caption + f"\n\n(+{len(media_paths) - 1} медиафайлов)"
                if len(full_caption) > CAPTION_LIMIT:
                    # Не влезает в лимит подписи к медиа — шлём медиа без подписи,
                    # а текст с кнопками отдельным сообщением (иначе Telegram
                    # отклонит весь запрос, и карточка не дойдёт вовсе).
                    if media_type == "video":
                        await bot.send_video(chat_id=chat_id, video=source)
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=source)
                    message = await bot.send_message(chat_id=chat_id, text=full_caption, reply_markup=keyboard)
                elif media_type == "video":
                    message = await bot.send_video(
                        chat_id=chat_id, video=source, caption=full_caption, reply_markup=keyboard
                    )
                else:
                    message = await bot.send_photo(
                        chat_id=chat_id, photo=source, caption=full_caption, reply_markup=keyboard
                    )
        except Exception:
            logger.exception("Не удалось отправить карточку черновика %s в чат %s", draft_id, chat_id)
            continue

        async with session_scope() as session:
            db_draft = await session.get(DraftPost, draft_id)
            if db_draft:
                db_draft.approval_chat_id = chat_id
                db_draft.approval_message_id = message.message_id


async def poll_pending_drafts(bot: Bot, interval: int = 10) -> None:
    """Периодически ищет черновики, для которых карточка ещё не отправлена."""
    while True:
        try:
            async with session_scope() as session:
                result = await session.execute(
                    select(DraftPost.id).where(
                        DraftPost.status == "pending_approval",
                        DraftPost.approval_message_id.is_(None),
                    )
                )
                draft_ids = [row[0] for row in result.all()]
            for draft_id in draft_ids:
                await send_draft_card(bot, draft_id)
        except Exception:
            logger.exception("Ошибка при рассылке карточек на утверждение")
        await asyncio.sleep(interval)


async def _still_pending(callback: CallbackQuery, draft_id: int) -> bool:
    """Защита от гонки: несколько утверждающих в группе могут нажать кнопку одновременно."""
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        status = draft.status if draft else None
    if status != "pending_approval":
        await callback.answer("Этот пост уже обработан другим утверждающим.", show_alert=True)
        return False
    return True


@router.callback_query(F.data.startswith("pub:"))
async def on_publish(callback: CallbackQuery) -> None:
    if not _approver_filter(callback):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, draft_id_str, target_id_str = callback.data.split(":")
    draft_id, target_id = int(draft_id_str), int(target_id_str)
    if not target_id:
        await callback.answer("Нет ни одного целевого канала. Добавьте канал в сетку.", show_alert=True)
        return

    if not await _still_pending(callback, draft_id):
        return

    await callback.answer("Публикую…")
    try:
        await publish_draft(callback.bot, draft_id, target_id, decided_by=str(callback.from_user.id))
    except Exception:
        logger.exception("Ошибка публикации черновика %s", draft_id)
        await callback.message.reply("Не удалось опубликовать пост, смотрите логи.")
        return
    if callback.message.caption:
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Опубликовано", reply_markup=None)
    else:
        await callback.message.edit_text((callback.message.text or "") + "\n\n✅ Опубликовано", reply_markup=None)


@router.callback_query(F.data.startswith("rej:"))
async def on_reject(callback: CallbackQuery) -> None:
    if not _approver_filter(callback):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    draft_id = int(callback.data.split(":")[1])
    if not await _still_pending(callback, draft_id):
        return

    await reject_draft(draft_id, decided_by=str(callback.from_user.id))
    await callback.answer("Отклонено")
    if callback.message.caption:
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ Отклонено", reply_markup=None)
    else:
        await callback.message.edit_text((callback.message.text or "") + "\n\n❌ Отклонено", reply_markup=None)


@router.callback_query(F.data.startswith("edit:"))
async def on_edit_request(callback: CallbackQuery) -> None:
    if not _approver_filter(callback):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    draft_id = int(callback.data.split(":")[1])
    if not await _still_pending(callback, draft_id):
        return
    _awaiting_reply.pop(callback.from_user.id, None)
    _awaiting_edit[callback.from_user.id] = draft_id
    await callback.answer()
    # force_reply обязателен: в группе с включённым privacy mode бот не получает обычные
    # текстовые сообщения, только команды и ответы (reply) на свои сообщения.
    # selective=True здесь не подходит: сообщение, на которое отвечает бот, — это его же
    # карточка черновика, а не сообщение конкретного пользователя, поэтому критерии
    # selective (упоминание в тексте или автор реплаемого сообщения) не выполняются ни
    # для кого, кроме, возможно, случайных совпадений — реальный пользователь не видит
    # автоматическую подсказку для ответа. Без selective подсказка появляется у всех.
    await callback.message.reply(
        f"Пришлите следующим сообщением новый текст для черновика #{draft_id}.",
        reply_markup=ForceReply(),
    )


def _has_pending_edit(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in _awaiting_edit


@router.message(F.text, _has_pending_edit)
async def on_edit_text(message: Message) -> None:
    if not _approver_filter(message):
        return
    draft_id = _awaiting_edit.pop(message.from_user.id)
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            await message.reply("Черновик не найден.")
            return
        draft.translated_text = message.text
        draft.approval_message_id = None  # заставит poll_pending_drafts переслать обновлённую карточку
        draft.approval_chat_id = None
    await message.reply(f"Черновик #{draft_id} обновлён, новая карточка будет прислана.")


@router.callback_query(F.data.startswith("reply:"))
async def on_reply_request(callback: CallbackQuery) -> None:
    if not _approver_filter(callback):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if _get_suggest_bot() is None:
        await callback.answer("SUGGEST_BOT_TOKEN не задан в этом сервисе — ответить автору нельзя.", show_alert=True)
        return
    draft_id = int(callback.data.split(":")[1])
    _awaiting_edit.pop(callback.from_user.id, None)
    _awaiting_reply[callback.from_user.id] = draft_id
    await callback.answer()
    await callback.message.reply(
        f"Пришлите следующим сообщением ответ автору новости #{draft_id}.",
        reply_markup=ForceReply(),
    )


def _has_pending_reply(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in _awaiting_reply


@router.message(F.text, _has_pending_reply)
async def on_reply_text(message: Message) -> None:
    if not _approver_filter(message):
        return
    draft_id = _awaiting_reply.pop(message.from_user.id)
    async with session_scope() as session:
        submission = (
            await session.execute(select(NewsSubmission).where(NewsSubmission.draft_post_id == draft_id))
        ).scalars().first()
    suggest_bot = _get_suggest_bot()
    if submission is None or suggest_bot is None:
        await message.reply("Автор этой новости не найден.")
        return
    try:
        await suggest_bot.send_message(chat_id=submission.tg_user_id, text=f"✉️ Ответ редактора:\n\n{message.text}")
    except Exception:
        logger.exception("Не удалось отправить ответ автору новости %s", draft_id)
        await message.reply("Не удалось отправить — возможно, автор заблокировал бота.")
        return
    await message.reply("Ответ отправлен автору.")


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(admin_commands.router)
    dp.include_router(force_sub.router)
    register_membership_handlers(dp)
    return dp


async def run_forever() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан")
    bot = build_bot(settings.bot_token)
    dp = create_dispatcher()

    await force_sub.ensure_gated_group_invite_links(bot)

    reports_scheduler = start_reports_scheduler(bot)

    polling_task = asyncio.create_task(poll_pending_drafts(bot))
    try:
        await dp.start_polling(bot)
    finally:
        polling_task.cancel()
        if reports_scheduler is not None:
            reports_scheduler.shutdown(wait=False)
