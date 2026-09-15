"""Обработка событий вступления/отписки в целевых каналах (ТЗ 3.4.3-3.4.4).

Bot API отдаёт эти данные через апдейт chat_member, если бот — админ канала.
Обработчик регистрируется в диспетчере approval-бота (news_agent/bot/approval.py),
т.к. это тот же бот, что стоит админом в целевых каналах (публикует посты).
"""
from __future__ import annotations

import logging

from aiogram import Dispatcher, Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy import select

from news_agent.db.models import InviteLink, SubscriberEvent, TargetChannel, utcnow
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)

router = Router()

_LEFT_STATUSES = {"left", "kicked"}
_MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}


async def _find_target_channel(tg_chat_id: int) -> TargetChannel | None:
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).where(TargetChannel.tg_chat_id == tg_chat_id))
        return result.scalars().first()


@router.chat_member()
async def on_chat_member_update(update: ChatMemberUpdated) -> None:
    target = await _find_target_channel(update.chat.id)
    if target is None:
        return  # обновление из чата, который не является нашим целевым каналом

    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status
    user_id = update.new_chat_member.user.id

    joined = old_status not in _MEMBER_STATUSES and new_status in _MEMBER_STATUSES
    left = old_status in _MEMBER_STATUSES and new_status in _LEFT_STATUSES

    if joined:
        await _record_join(target.id, user_id, update.invite_link)
    elif left:
        await _record_leave(target.id, user_id)


async def _record_join(target_channel_id: int, user_id: int, tg_invite_link) -> None:
    invite_link_id = None
    is_direct = True

    if tg_invite_link is not None:
        code = getattr(tg_invite_link, "name", None)
        async with session_scope() as session:
            result = await session.execute(
                select(InviteLink).where(
                    InviteLink.target_channel_id == target_channel_id,
                    InviteLink.name == code,
                )
            )
            link = result.scalars().first()
            if link:
                invite_link_id = link.id
                is_direct = False

    async with session_scope() as session:
        session.add(
            SubscriberEvent(
                target_channel_id=target_channel_id,
                tg_user_id=user_id,
                event_type="join",
                invite_link_id=invite_link_id,
                is_direct=is_direct,
                occurred_at=utcnow(),
            )
        )
    logger.info(
        "Канал %s: join user=%s invite_link_id=%s (direct=%s)",
        target_channel_id, user_id, invite_link_id, is_direct,
    )


async def _record_leave(target_channel_id: int, user_id: int) -> None:
    # Bot API не сообщает, по какой ссылке вступил ушедший — берём последний join (3.4.4).
    async with session_scope() as session:
        result = await session.execute(
            select(SubscriberEvent)
            .where(
                SubscriberEvent.target_channel_id == target_channel_id,
                SubscriberEvent.tg_user_id == user_id,
                SubscriberEvent.event_type == "join",
            )
            .order_by(SubscriberEvent.occurred_at.desc())
            .limit(1)
        )
        last_join = result.scalars().first()
        invite_link_id = last_join.invite_link_id if last_join else None
        is_direct = last_join.is_direct if last_join else True

        session.add(
            SubscriberEvent(
                target_channel_id=target_channel_id,
                tg_user_id=user_id,
                event_type="leave",
                invite_link_id=invite_link_id,
                is_direct=is_direct,
                occurred_at=utcnow(),
            )
        )
    logger.info("Канал %s: leave user=%s invite_link_id=%s", target_channel_id, user_id, invite_link_id)


def register_membership_handlers(dp: Dispatcher) -> None:
    dp.include_router(router)
