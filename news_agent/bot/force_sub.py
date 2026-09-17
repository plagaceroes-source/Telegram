"""Force-sub: в группах из gated_groups удаляет сообщения участников, не
подписанных на привязанный target_channel, и просит подписаться.

Использует тот же Bot API бот, что публикует посты (approval-бот), т.к. он
уже должен быть админом в целевых каналах сети — приглашающие ссылки на
группу переиспользуют InviteLink/SubscriberEvent (news_agent/stats/events.py)
для атрибуции: chat_member апдейт по такой ссылке распознаётся по её `name`.
"""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from news_agent.config import settings
from news_agent.db.models import GatedGroup, InviteLink, TargetChannel
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)

router = Router()

_SUBSCRIBED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


def _link_name(gated_group_id: int) -> str:
    return f"grp{gated_group_id}"[:32]  # Telegram ограничивает имя ссылки 32 символами


async def _get_gated_group(chat_id: int) -> GatedGroup | None:
    async with session_scope() as session:
        result = await session.execute(
            select(GatedGroup).where(GatedGroup.tg_chat_id == chat_id, GatedGroup.active.is_(True))
        )
        return result.scalars().first()


async def _get_or_create_invite_link(bot: Bot, gated: GatedGroup, target: TargetChannel) -> str | None:
    name = _link_name(gated.id)
    async with session_scope() as session:
        result = await session.execute(
            select(InviteLink).where(
                InviteLink.target_channel_id == target.id,
                InviteLink.name == name,
                InviteLink.revoked.is_(False),
            )
        )
        link = result.scalars().first()
        if link:
            return link.tg_invite_link

    chat_id = target.tg_chat_id or f"@{target.username}"
    try:
        tg_link = await bot.create_chat_invite_link(chat_id=chat_id, name=name)
    except Exception:
        logger.exception("Не удалось создать инвайт-ссылку для gated_group %s", gated.id)
        return None

    async with session_scope() as session:
        session.add(
            InviteLink(
                target_channel_id=target.id,
                name=name,
                tg_invite_link=tg_link.invite_link,
                source_label=gated.title,
                campaign_tag="force_sub",
                created_by="force_sub",
            )
        )
    return tg_link.invite_link


async def ensure_gated_group_invite_links(bot: Bot) -> None:
    """Создаёт недостающие invite-ссылки для всех активных gated-групп при старте."""
    async with session_scope() as session:
        result = await session.execute(select(GatedGroup).where(GatedGroup.active.is_(True)))
        gated_groups = list(result.scalars())

    for gated in gated_groups:
        async with session_scope() as session:
            target = await session.get(TargetChannel, gated.target_channel_id)
        if target is None:
            logger.warning("gated_group %s ссылается на несуществующий target_channel", gated.id)
            continue
        await _get_or_create_invite_link(bot, gated, target)


async def _cleanup_warning(warning: Message, ttl: int) -> None:
    await asyncio.sleep(ttl)
    try:
        await warning.delete()
    except Exception:
        pass


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def check_subscription_on_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return  # не мешаем командам боту (включая админские)

    gated = await _get_gated_group(message.chat.id)
    if gated is None:
        return

    async with session_scope() as session:
        target = await session.get(TargetChannel, gated.target_channel_id)
    if target is None:
        return

    channel_ref = target.tg_chat_id or f"@{target.username}"
    try:
        member = await bot.get_chat_member(chat_id=channel_ref, user_id=message.from_user.id)
    except Exception as e:
        # Например, бот потерял админку в канале — не блокируем чат из-за ошибки.
        logger.error("Ошибка проверки подписки для %s в канале %s: %s", message.from_user.id, target.id, e)
        return

    if member.status in _SUBSCRIBED_STATUSES:
        return

    try:
        await message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение %s: %s", message.message_id, e)

    invite_link = await _get_or_create_invite_link(bot, gated, target)
    channel_title = target.title or f"@{target.username}"
    safe_name = html.escape(message.from_user.first_name or "")
    safe_title = html.escape(channel_title)

    if invite_link:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"Подписаться на «{channel_title}»", url=invite_link)]]
        )
        channel_link = f'<a href="{html.escape(invite_link)}">{safe_title}</a>'
        text = (
            f"{safe_name}, мы публикуем ваши объявления совершенно бесплатно, "
            f"чтобы и дальше пользоваться этой возможностью и писать в этой группе, подпишись, "
            f"пожалуйста, на канал «{channel_link}»\n"
            f"Подпишитесь и попробуйте снова 👇"
        )
    else:
        keyboard = None
        text = (
            f"{safe_name}, мы публикуем ваши объявления совершенно бесплатно, "
            f"чтобы и дальше пользоваться этой возможностью и писать в этой группе, подпишись, "
            f"пожалуйста, на канал «{safe_title}»."
        )

    try:
        warning = await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Не удалось отправить предупреждение в чат %s", message.chat.id)
        return

    asyncio.create_task(_cleanup_warning(warning, settings.force_sub_warning_ttl))
