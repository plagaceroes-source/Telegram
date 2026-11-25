"""Агрегация данных для ежедневных/месячных отчётов (news_agent/reports/).

Границы периода — половинно-открытый интервал [since, until) в UTC; вызывающий
код (daily.py/monthly.py) сам считает эти границы по календарным суткам/месяцам
в часовом поясе отчёта (Europe/Madrid по умолчанию, см. config.reports_timezone)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from news_agent.db.models import (
    ChannelPost,
    ChannelStatsDaily,
    InviteLink,
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


async def last_posts_sync(session, target_ids: list[int]) -> dt.datetime | None:
    """Когда зеркало постов обновлялось в последний раз: каждая синхронизация проставляет
    updated_at всем постам окна, так что максимум — это время последнего успешного сбора."""
    if not target_ids:
        return None
    return await session.scalar(
        select(func.max(ChannelPost.updated_at)).where(ChannelPost.target_channel_id.in_(target_ids))
    )


async def published_posts_count(session, target_ids: list[int], since: dt.datetime, until: dt.datetime) -> int:
    """Число реальных постов канала за период (альбом — один пост, вне зависимости от того,
    опубликован пост через систему или вручную)."""
    if not target_ids:
        return 0
    return (
        await session.scalar(
            select(func.count()).where(
                ChannelPost.target_channel_id.in_(target_ids),
                ChannelPost.posted_at >= since,
                ChannelPost.posted_at < until,
            )
        )
    ) or 0


async def posts_with_stats(
    session, target_ids: list[int], since: dt.datetime, until: dt.datetime
) -> list[dict]:
    """Список постов канала за период с метриками, по просмотрам по убыванию."""
    if not target_ids:
        return []
    rows = (
        await session.execute(
            select(ChannelPost, TargetChannel)
            .join(TargetChannel, ChannelPost.target_channel_id == TargetChannel.id)
            .where(
                ChannelPost.target_channel_id.in_(target_ids),
                ChannelPost.posted_at >= since,
                ChannelPost.posted_at < until,
            )
            .order_by(ChannelPost.views.desc())
        )
    ).all()

    posts = []
    for cp, target in rows:
        text = (cp.snippet or "").strip()
        snippet = (text[:80] + "…") if len(text) > 80 else text
        posts.append(
            {
                "published_at": cp.posted_at,
                "channel_username": target.username,
                "link": f"https://t.me/{target.username}/{cp.tg_message_id}",
                "snippet": snippet or ("(медиа без текста)" if cp.has_media else "(без текста)"),
                "views": cp.views,
                "forwards": cp.forwards,
                "reactions": cp.reactions_count,
                "comments": cp.comments_count,
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
                func.coalesce(func.sum(ChannelPost.views), 0),
                func.coalesce(func.sum(ChannelPost.forwards), 0),
                func.coalesce(func.sum(ChannelPost.reactions_count), 0),
                func.coalesce(func.sum(ChannelPost.comments_count), 0),
            ).where(
                ChannelPost.target_channel_id.in_(target_ids),
                ChannelPost.posted_at >= since,
                ChannelPost.posted_at < until,
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


def _as_utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


async def period_metrics(session, target_ids: list[int], since: dt.datetime, until: dt.datetime) -> dict:
    """Все итоговые показатели периода [since, until) — для недельного, месячного и «с начала месяца» отчётов."""
    posts_count = await published_posts_count(session, target_ids, since, until)
    start_subs = await subscriber_count_at(session, target_ids, since)
    end_subs = await subscriber_count_at(session, target_ids, until)
    joins, leaves, joins_by_source = await subscriber_flow(session, target_ids, since, until)
    totals = await posts_totals(session, target_ids, since, until)
    return {
        "posts_count": posts_count,
        "start_subs": start_subs,
        "end_subs": end_subs,
        "growth": end_subs - start_subs,
        # Нет снимка на начало периода → «прирост» равен всему числу подписчиков, это не прирост.
        "start_known": start_subs > 0,
        "joins": joins,
        "leaves": leaves,
        "joins_by_source": joins_by_source,
        "totals": totals,
    }


async def daily_series(
    session, target_ids: list[int], days: list[dt.date], since: dt.datetime, until: dt.datetime, tz
) -> list[dict]:
    """Показатели по каждому календарному дню (в часовом поясе отчёта) из days, но только
    в пределах [since, until): последний день может быть неполным.

    На каждый день: posts, views (сумма просмотров постов, вышедших в этот день), joins, leaves,
    end_subs (число подписчиков на конец дня или на until) и growth (изменение за день,
    None если нет снимка на начало периода)."""
    posts_by_day: dict[dt.date, list[int]] = {d: [0, 0] for d in days}
    events_by_day: dict[dt.date, list[int]] = {d: [0, 0] for d in days}
    if target_ids:
        rows = (
            await session.execute(
                select(ChannelPost.posted_at, ChannelPost.views).where(
                    ChannelPost.target_channel_id.in_(target_ids),
                    ChannelPost.posted_at >= since,
                    ChannelPost.posted_at < until,
                )
            )
        ).all()
        for posted_at, views in rows:
            bucket = posts_by_day.get(_as_utc(posted_at).astimezone(tz).date())
            if bucket is not None:
                bucket[0] += 1
                bucket[1] += views or 0
        rows = (
            await session.execute(
                select(SubscriberEvent.event_type, SubscriberEvent.occurred_at).where(
                    SubscriberEvent.target_channel_id.in_(target_ids),
                    SubscriberEvent.occurred_at >= since,
                    SubscriberEvent.occurred_at < until,
                )
            )
        ).all()
        for event_type, occurred_at in rows:
            bucket = events_by_day.get(_as_utc(occurred_at).astimezone(tz).date())
            if bucket is not None and event_type in ("join", "leave"):
                bucket[0 if event_type == "join" else 1] += 1

    start_subs = await subscriber_count_at(session, target_ids, since)
    result = []
    previous = start_subs
    for day in days:
        day_end_local = dt.datetime(day.year, day.month, day.day, tzinfo=tz) + dt.timedelta(days=1)
        end = min(day_end_local.astimezone(dt.timezone.utc), until)
        end_subs = await subscriber_count_at(session, target_ids, end)
        result.append(
            {
                "day": day,
                "posts": posts_by_day[day][0],
                "views": posts_by_day[day][1],
                "joins": events_by_day[day][0],
                "leaves": events_by_day[day][1],
                "end_subs": end_subs,
                "growth": (end_subs - previous) if start_subs > 0 else None,
            }
        )
        previous = end_subs
    return result
