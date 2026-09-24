"""Агрегация данных для ежедневных/месячных отчётов (news_agent/reports/).

Границы периода — половинно-открытый интервал [since, until) в UTC; вызывающий
код (daily.py/monthly.py) сам считает эти границы по календарным суткам/месяцам
в часовом поясе отчёта (Europe/Madrid по умолчанию, см. config.reports_timezone)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from news_agent.db.models import (
    ChannelStatsDaily,
    DraftPost,
    InviteLink,
    PostStats,
    PublishedPost,
    SubscriberEvent,
    TargetChannel,
)


async def active_target_ids(session) -> list[int]:
    result = await session.execute(select(TargetChannel.id).where(TargetChannel.active.is_(True)))
    return [row[0] for row in result.all()]


async def subscriber_count_at(session, target_ids: list[int], at: dt.datetime) -> int:
    """Сумма последних известных снепшотов подписчиков на момент at (или раньше),
    по всем каналам из target_ids."""
    if not target_ids:
        return 0
    total = 0
    for target_id in target_ids:
        value = await session.scalar(
            select(ChannelStatsDaily.subscriber_count)
            .where(ChannelStatsDaily.target_channel_id == target_id, ChannelStatsDaily.snapshot_at <= at)
            .order_by(ChannelStatsDaily.snapshot_at.desc())
            .limit(1)
        )
        total += value or 0
    return total


async def published_posts_count(session, target_ids: list[int], since: dt.datetime, until: dt.datetime) -> int:
    if not target_ids:
        return 0
    return (
        await session.scalar(
            select(func.count()).where(
                PublishedPost.target_channel_id.in_(target_ids),
                PublishedPost.published_at >= since,
                PublishedPost.published_at < until,
            )
        )
    ) or 0


async def posts_with_stats(
    session, target_ids: list[int], since: dt.datetime, until: dt.datetime
) -> list[dict]:
    """Список опубликованных постов за период с их метриками, отсортированный по
    просмотрам по убыванию."""
    if not target_ids:
        return []
    post_q = (
        select(PublishedPost, PostStats, DraftPost, TargetChannel)
        .join(DraftPost, PublishedPost.draft_post_id == DraftPost.id)
        .join(TargetChannel, PublishedPost.target_channel_id == TargetChannel.id)
        .outerjoin(PostStats, PostStats.published_post_id == PublishedPost.id)
        .where(
            PublishedPost.target_channel_id.in_(target_ids),
            PublishedPost.published_at >= since,
            PublishedPost.published_at < until,
        )
        .order_by(func.coalesce(PostStats.views, 0).desc())
    )
    rows = (await session.execute(post_q)).all()

    posts = []
    for published, pstats, draft, target in rows:
        text = (draft.translated_text or "").strip()
        snippet = (text[:80] + "…") if len(text) > 80 else text
        posts.append(
            {
                "published_at": published.published_at,
                "channel_username": target.username,
                "link": f"https://t.me/{target.username}/{published.tg_message_id}",
                "snippet": snippet or "(без текста)",
                "views": pstats.views if pstats else 0,
                "forwards": pstats.forwards if pstats else 0,
                "reactions": pstats.reactions_count if pstats else 0,
                "comments": pstats.comments_count if pstats else 0,
            }
        )
    return posts


async def posts_totals(session, target_ids: list[int], since: dt.datetime, until: dt.datetime) -> dict[str, int]:
    """Суммарные просмотры/репосты/реакции/комментарии по постам за период."""
    if not target_ids:
        return {"views": 0, "forwards": 0, "reactions": 0, "comments": 0}
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(PostStats.views), 0),
                func.coalesce(func.sum(PostStats.forwards), 0),
                func.coalesce(func.sum(PostStats.reactions_count), 0),
                func.coalesce(func.sum(PostStats.comments_count), 0),
            )
            .select_from(PublishedPost)
            .join(PostStats, PostStats.published_post_id == PublishedPost.id)
            .where(
                PublishedPost.target_channel_id.in_(target_ids),
                PublishedPost.published_at >= since,
                PublishedPost.published_at < until,
            )
        )
    ).first()
    views, forwards, reactions, comments = row or (0, 0, 0, 0)
    return {"views": views, "forwards": forwards, "reactions": reactions, "comments": comments}


async def subscriber_flow(
    session, target_ids: list[int], since: dt.datetime, until: dt.datetime
) -> tuple[int, int, dict[str, int]]:
    """(всего подписок, всего отписок, подписки по источнику) за период."""
    if not target_ids:
        return 0, 0, {}

    events_q = (
        select(SubscriberEvent.event_type, SubscriberEvent.is_direct, InviteLink.source_label, InviteLink.name)
        .select_from(SubscriberEvent)
        .outerjoin(InviteLink, SubscriberEvent.invite_link_id == InviteLink.id)
        .where(
            SubscriberEvent.target_channel_id.in_(target_ids),
            SubscriberEvent.occurred_at >= since,
            SubscriberEvent.occurred_at < until,
        )
    )
    rows = (await session.execute(events_q)).all()

    total_joins = 0
    total_leaves = 0
    joins_by_source: dict[str, int] = {}
    for event_type, is_direct, source_label, link_name in rows:
        if event_type == "join":
            total_joins += 1
            if is_direct:
                label = "Прямые (без ссылки)"
            else:
                label = source_label or link_name or "Без метки"
            joins_by_source[label] = joins_by_source.get(label, 0) + 1
        elif event_type == "leave":
            total_leaves += 1

    return total_joins, total_leaves, joins_by_source
