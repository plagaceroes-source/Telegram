"""Сбор метрик опубликованных постов (просмотры/репосты/реакции/комментарии).

Telegram Bot API не отдаёт эти данные для произвольных сообщений — используем
уже авторизованную Telethon-сессию юзербота (MTProto), которая видит их у
публичных каналов без необходимости в них вступать."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from telethon import TelegramClient

from news_agent.db.models import PostStats, PublishedPost, TargetChannel
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def collect_post_stats(client: TelegramClient, lookback_days: int = 90) -> int:
    """Обновляет PostStats для постов, опубликованных за последние lookback_days
    дней. Возвращает число обновлённых записей."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)

    async with session_scope() as session:
        targets = list(
            (await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars()
        )

    updated = 0
    for target in targets:
        async with session_scope() as session:
            posts = list(
                (
                    await session.execute(
                        select(PublishedPost).where(
                            PublishedPost.target_channel_id == target.id,
                            PublishedPost.published_at >= since,
                        )
                    )
                ).scalars()
            )
        if not posts:
            continue

        try:
            entity = await client.get_entity(f"@{target.username}")
        except Exception:
            logger.exception("Не удалось получить канал %s для сбора статистики постов", target.username)
            continue

        by_msg_id = {p.tg_message_id: p for p in posts}
        fetched: dict[int, dict] = {}
        ids = list(by_msg_id.keys())
        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i : i + BATCH_SIZE]
            try:
                messages = await client.get_messages(entity, ids=batch)
            except Exception:
                logger.exception(
                    "Канал %s: не удалось получить сообщения для статистики (batch %s)", target.username, batch
                )
                continue
            for msg in messages:
                if msg is None:
                    continue
                reactions_breakdown: dict[str, int] = {}
                reactions_count = 0
                if msg.reactions and msg.reactions.results:
                    for r in msg.reactions.results:
                        emoji = getattr(r.reaction, "emoticon", None) or "?"
                        reactions_breakdown[emoji] = r.count
                        reactions_count += r.count
                fetched[msg.id] = {
                    "views": msg.views or 0,
                    "forwards": msg.forwards or 0,
                    "reactions_count": reactions_count,
                    "reactions_breakdown": reactions_breakdown,
                    "comments_count": msg.replies.replies if msg.replies else 0,
                }

        if not fetched:
            continue

        published_ids = [by_msg_id[mid].id for mid in fetched if mid in by_msg_id]
        async with session_scope() as session:
            existing_result = await session.execute(
                select(PostStats).where(PostStats.published_post_id.in_(published_ids))
            )
            existing_by_pid = {row.published_post_id: row for row in existing_result.scalars()}
            now = dt.datetime.now(dt.timezone.utc)
            for msg_id, stats in fetched.items():
                published = by_msg_id.get(msg_id)
                if published is None:
                    continue
                row = existing_by_pid.get(published.id)
                if row is None:
                    row = PostStats(published_post_id=published.id)
                    session.add(row)
                row.views = stats["views"]
                row.forwards = stats["forwards"]
                row.reactions_count = stats["reactions_count"]
                row.reactions_breakdown = stats["reactions_breakdown"]
                row.comments_count = stats["comments_count"]
                row.updated_at = now
        updated += len(fetched)

    return updated
