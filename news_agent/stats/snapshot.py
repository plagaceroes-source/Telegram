"""Снепшоты числа подписчиков по расписанию (ТЗ 3.2)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from news_agent.config import settings
from news_agent.db.models import ChannelStatsDaily, TargetChannel
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)


async def take_snapshot(bot: Bot) -> int:
    """Снимает снепшот getChatMemberCount для всех активных целевых каналов."""
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))
        targets = list(result.scalars())

    taken = 0
    for target in targets:
        chat_id = target.tg_chat_id or f"@{target.username}"
        try:
            count = await bot.get_chat_member_count(chat_id)
        except Exception:
            logger.exception("Не удалось получить число подписчиков для канала %s", target.username)
            continue

        async with session_scope() as session:
            session.add(ChannelStatsDaily(target_channel_id=target.id, subscriber_count=count))
        taken += 1

    return taken


async def run_forever(bot: Bot, interval_seconds: int = 3600) -> None:
    logger.info("Stats snapshot запущен, интервал %sс", interval_seconds)
    while True:
        try:
            taken = await take_snapshot(bot)
            logger.info("Снепшот подписчиков снят для %d каналов", taken)
        except Exception:
            logger.exception("Ошибка при снятии снепшота статистики")
        await asyncio.sleep(interval_seconds)
