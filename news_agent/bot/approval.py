"""Approval bot: карточки с переведёнными постами. Кнопок нет — текст идёт блоком кода и
копируется одним нажатием; обработчики pub/rej/edit остались для старых карточек в чате (ТЗ 2.3)."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, ForceReply, LinkPreviewOptions, Message
from sqlalchemy import select

from news_agent.bot import commands as admin_commands
from news_agent.bot import force_sub
from news_agent.bot.publish import publish_draft, reject_draft
from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import DraftPost, RawPost, Source
from news_agent.db.session import session_scope
from news_agent.reports.scheduler import start_reports_scheduler
from news_agent.services.media_relay import CAPTION_LIMIT, resolve_media
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


MAX_CARD_ATTEMPTS = 3  # после стольких неудачных циклов рассылку карточки прекращаем
TEXT_LIMIT = 4000  # лимит текстового сообщения Telegram — 4096, берём с запасом
STATUS_CARD_FAILED = "card_failed"
STATUS_DELIVERED = "delivered"  # карточка отправлена; кнопок решения больше нет

_card_failures: dict[int, int] = {}
# (draft_id, chat_id): медиа без подписи уже отправлено, а текст ещё нет. При повторной
# попытке шлём только текст — иначе каждая неудача плодила бы ещё одно «голое» видео.
_media_sent: set[tuple[int, int]] = set()

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _card_html(header: str, body: str, note: str = "") -> str:
    """Источник отдельной строкой, сам текст — блоком кода: на телефоне и в десктопе блок
    копируется одним нажатием. Обрезаем до экранирования, чтобы не разорвать HTML-сущность."""
    budget = TEXT_LIMIT - len(header) - len(note) - 4
    if len(body) > budget:
        body = body[: max(0, budget - 1)] + "…"
    text = html.escape(header, quote=False)
    if body:
        text += f"\n<pre>{html.escape(body, quote=False)}</pre>"
    if note:
        text += f"\n{html.escape(note, quote=False)}"
    return text


async def send_draft_card(bot: Bot, draft_id: int) -> None:
    """Отправляет карточку черновика: одну в групповой чат (APPROVAL_CHAT_ID),
    либо каждому утверждающему лично (APPROVER_CHAT_IDS), если группа не задана."""
    recipients = [settings.approval_chat_id] if settings.approval_chat_id else (settings.approver_chat_ids or [])
    if not recipients:
        logger.warning("Некому слать карточку черновика %s: не заданы APPROVAL_CHAT_ID/APPROVER_CHAT_IDS", draft_id)
        return

    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is None:
            return
        raw_post = await session.get(RawPost, draft.raw_post_id)
        source = await session.get(Source, raw_post.source_id) if raw_post else None
        source_label = (source.title or source.username) if source else "неизвестный источник"
        body = draft.translated_text.strip()
        media_paths = list(draft.media_paths)

    header = f"📰 Источник: {source_label}"
    note = f"(+{len(media_paths) - 1} медиафайлов)" if len(media_paths) > 1 else ""
    card = _card_html(header, body, note)
    fits_caption = len(header) + len(body) + len(note) + 3 <= CAPTION_LIMIT

    delivered = False
    last_error = ""
    for chat_id in recipients:
        try:
            if not media_paths:
                message = await bot.send_message(
                    chat_id=chat_id, text=card, parse_mode=ParseMode.HTML, link_preview_options=_NO_PREVIEW
                )
            else:
                media_type, media = resolve_media(media_paths[0])
                if not fits_caption:
                    # Не влезает в лимит подписи к медиа (1024) — шлём медиа без подписи,
                    # а текст отдельным сообщением (иначе Telegram отклонит весь запрос,
                    # и карточка не дойдёт вовсе).
                    if (draft_id, chat_id) not in _media_sent:
                        if media_type == "video":
                            await bot.send_video(chat_id=chat_id, video=media)
                        else:
                            await bot.send_photo(chat_id=chat_id, photo=media)
                        _media_sent.add((draft_id, chat_id))
                    message = await bot.send_message(
                        chat_id=chat_id, text=card, parse_mode=ParseMode.HTML, link_preview_options=_NO_PREVIEW
                    )
                elif media_type == "video":
                    message = await bot.send_video(chat_id=chat_id, video=media, caption=card, parse_mode=ParseMode.HTML)
                else:
                    message = await bot.send_photo(chat_id=chat_id, photo=media, caption=card, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.exception("Не удалось отправить карточку черновика %s в чат %s", draft_id, chat_id)
            last_error = f"{type(e).__name__}: {e}"[:300]
            continue

        _media_sent.discard((draft_id, chat_id))
        delivered = True
        async with session_scope() as session:
            db_draft = await session.get(DraftPost, draft_id)
            if db_draft:
                db_draft.approval_chat_id = chat_id
                db_draft.approval_message_id = message.message_id
                db_draft.status = STATUS_DELIVERED

    if delivered:
        _card_failures.pop(draft_id, None)
        return

    # Рассылка идёт каждые несколько секунд, пока карточка не отправлена, — без предела
    # сбой превращается в бесконечный спам в чат апрува.
    _card_failures[draft_id] = _card_failures.get(draft_id, 0) + 1
    if _card_failures[draft_id] >= MAX_CARD_ATTEMPTS:
        await _give_up_on_card(bot, draft_id, recipients, last_error)


async def _give_up_on_card(bot: Bot, draft_id: int, recipients: list[int], error: str) -> None:
    _card_failures.pop(draft_id, None)
    async with session_scope() as session:
        draft = await session.get(DraftPost, draft_id)
        if draft is not None and draft.status == "pending_approval":
            draft.status = STATUS_CARD_FAILED
    logger.error("Карточка черновика %s не отправлена после %d попыток, рассылка остановлена: %s",
                 draft_id, MAX_CARD_ATTEMPTS, error)
    for chat_id in recipients:
        _media_sent.discard((draft_id, chat_id))
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠ Не удалось отправить карточку черновика #{draft_id} за {MAX_CARD_ATTEMPTS} попытки — "
                    f"рассылку по нему остановил, чтобы не засорять чат.\nПричина: {error}"
                ),
            )
        except Exception:
            logger.exception("Не удалось сообщить о сбое карточки %s в чат %s", draft_id, chat_id)


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
