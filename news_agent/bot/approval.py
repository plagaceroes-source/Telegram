"""Approval bot: карточки черновиков + кнопки Опубликовать/Редактировать/Отклонить (ТЗ 2.3)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from news_agent.bot import commands as admin_commands
from news_agent.bot.publish import publish_draft, reject_draft
from news_agent.config import settings
from news_agent.db.models import DraftPost, RawPost, Source, TargetChannel
from news_agent.db.session import session_scope
from news_agent.stats.events import register_membership_handlers

logger = logging.getLogger(__name__)

router = Router()

# user_id -> draft_id для которого ожидается текст правки
_awaiting_edit: dict[int, int] = {}


def _is_approver(user_id: int) -> bool:
    return not settings.approver_chat_ids or user_id in settings.approver_chat_ids


def _approver_filter(message_or_query) -> bool:
    user = message_or_query.from_user
    return bool(user) and _is_approver(user.id)


async def _build_card_keyboard(draft_id: int) -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _card_caption(draft: DraftPost, raw_post: RawPost, source: Source | None) -> str:
    source_label = source.title or source.username if source else "неизвестный источник"
    header = f"📰 Источник: {source_label}\n\n"
    return header + draft.translated_text


async def send_draft_card(bot: Bot, draft_id: int) -> None:
    """Отправляет карточку черновика всем утверждающим (approver_chat_ids)."""
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            return
        raw_post = await session.get(RawPost, draft.raw_post_id)
        source = await session.get(Source, raw_post.source_id) if raw_post else None
        caption = await _card_caption(draft, raw_post, source)
        media_paths = list(draft.media_paths)

    keyboard = await _build_card_keyboard(draft_id)
    recipients = settings.approver_chat_ids or []

    for chat_id in recipients:
        try:
            if not media_paths:
                message = await bot.send_message(chat_id=chat_id, text=caption, reply_markup=keyboard)
            elif len(media_paths) == 1:
                message = await bot.send_photo(
                    chat_id=chat_id, photo=FSInputFile(media_paths[0]), caption=caption, reply_markup=keyboard
                )
            else:
                message = await bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(media_paths[0]),
                    caption=caption + f"\n\n(+{len(media_paths) - 1} медиафайлов)",
                    reply_markup=keyboard,
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
    _awaiting_edit[callback.from_user.id] = draft_id
    await callback.answer()
    await callback.message.reply(f"Пришлите следующим сообщением новый текст для черновика #{draft_id}.")


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


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(admin_commands.router)
    register_membership_handlers(dp)
    return dp


async def run_forever() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан")
    bot = Bot(token=settings.bot_token)
    dp = create_dispatcher()

    polling_task = asyncio.create_task(poll_pending_drafts(bot))
    try:
        await dp.start_polling(bot)
    finally:
        polling_task.cancel()
