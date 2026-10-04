"""Зеркало реальных постов канала (таблица ChannelPost).

Отчёты и «Топ постов» должны считать то, что реально лежит в канале, а не только посты,
опубликованные через апрув-систему (PublishedPost): часть постов админы публикуют вручную."""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import delete, select
from telethon import TelegramClient

from news_agent.db.models import ChannelPost, PublishedPost, TargetChannel
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)

SNIPPET_LEN = 300
ITER_LIMIT = 5000


def _reactions_total(msg) -> int:
    results = getattr(getattr(msg, "reactions", None), "results", None) or []
    return sum(r.count for r in results)


def group_messages(messages: list) -> list[dict]:
    """Склеивает сообщения в посты: альбом (общий grouped_id) = один пост; служебные сообщения
    (закрепы, смена фото канала и т.п.) пропускаются. Возвращает посты, новые первыми."""
    posts: dict = {}
    for msg in messages:
        if getattr(msg, "action", None) is not None:
            continue
        key = ("g", msg.grouped_id) if msg.grouped_id else ("m", msg.id)
        p = posts.get(key)
        if p is None:
            p = posts[key] = {
                "grouped_id": msg.grouped_id,
                "first_id": msg.id,
                "date": msg.date,
                "text": "",
                "has_media": False,
                "views": 0,
                "forwards": 0,
                "reactions": 0,
                "comments": 0,
                "ids": [],
            }
        p["ids"].append(msg.id)
        p["first_id"] = min(p["first_id"], msg.id)
        p["date"] = min(p["date"], msg.date)
        if msg.message and not p["text"]:
            p["text"] = msg.message
        p["has_media"] = p["has_media"] or bool(msg.media)
        p["views"] = max(p["views"], getattr(msg, "views", 0) or 0)
        p["forwards"] = max(p["forwards"], getattr(msg, "forwards", 0) or 0)
        p["reactions"] += _reactions_total(msg)
        replies = getattr(msg, "replies", None)
        p["comments"] = max(p["comments"], (replies.replies if replies else 0) or 0)
    result = list(posts.values())
    result.sort(key=lambda p: p["first_id"], reverse=True)
    return result


async def sync_channel_posts(client: TelegramClient, lookback_days: int) -> int:
    """Обновляет ChannelPost по постам за lookback_days дней. Возвращает число постов в окне."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)

    async with session_scope() as session:
        targets = list(
            (await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars()
        )

    total = 0
    for target in targets:
        try:
            entity = await client.get_entity(f"@{target.username}")
        except Exception:
            logger.exception("Не удалось получить канал %s для синхронизации постов", target.username)
            continue

        messages = []
        async for msg in client.iter_messages(entity, limit=ITER_LIMIT):
            if msg.date < since:
                break
            messages.append(msg)
        posts = group_messages(messages)
        if not posts:
            continue

        async with session_scope() as session:
            system_ids = {
                r
                for r in (
                    await session.execute(
                        select(PublishedPost.tg_message_id).where(PublishedPost.target_channel_id == target.id)
                    )
                ).scalars()
            }
            existing_result = await session.execute(
                select(ChannelPost).where(
                    ChannelPost.target_channel_id == target.id,
                    ChannelPost.tg_message_id.in_([p["first_id"] for p in posts]),
                )
            )
            existing = {r.tg_message_id: r for r in existing_result.scalars()}
            now = dt.datetime.now(dt.timezone.utc)
            for p in posts:
                row = existing.get(p["first_id"])
                if row is None:
                    row = ChannelPost(target_channel_id=target.id, tg_message_id=p["first_id"], posted_at=p["date"])
                    session.add(row)
                row.grouped_id = p["grouped_id"]
                row.snippet = re.sub(r"\s+", " ", p["text"]).strip()[:SNIPPET_LEN]
                row.has_media = p["has_media"]
                row.is_system = any(i in system_ids for i in p["ids"])
                row.views = p["views"]
                row.forwards = p["forwards"]
                row.reactions_count = p["reactions"]
                row.comments_count = p["comments"]
                row.updated_at = now
            # Пост удалили из канала — убираем и из зеркала. Только если окно прочитано целиком,
            # иначе (лимит/сбой) можно было бы стереть живые посты.
            if len(messages) < ITER_LIMIT:
                await session.execute(
                    delete(ChannelPost).where(
                        ChannelPost.target_channel_id == target.id,
                        ChannelPost.posted_at >= since,
                        ChannelPost.tg_message_id.notin_([p["first_id"] for p in posts]),
                    ).execution_options(synchronize_session=False)
                )
        total += len(posts)
        logger.info(
            "Канал %s: синхронизировано постов за %d дн.: %d (альбомы считаются одним постом)",
            target.username,
            lookback_days,
            len(posts),
        )
    return total
