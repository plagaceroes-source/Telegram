"""Веб-админка (ТЗ 2.5): FastAPI + Jinja2, простой CRUD источников/каналов + статистика."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import (
    ChannelStatsDaily,
    DraftPost,
    InviteLink,
    PublishedPost,
    RawPost,
    Source,
    SubscriberEvent,
    TargetChannel,
    UserbotAccount,
)
from news_agent.db.session import init_db, session_scope

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="News Agent Admin")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # iOS кэширует HTML standalone-приложения ("На главный экран") очень агрессивно
    # и может подолгу игнорировать изменения на сервере без явного запрета кэша.
    # Статику (/static/*: иконки, manifest) не трогаем — иначе каждый переход между
    # вкладками заново перекачивает то, что и так не меняется, добавляя задержку.
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "— не задано —"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "…" + "*" * 4


PERIOD_DAYS = {"7": 7, "30": 30, "90": 90, "all": None}


def _resolve_period(
    period: str, date_from: str | None, date_to: str | None
) -> tuple[str, dt.datetime | None, dt.datetime, str | None, str | None]:
    """Разбирает параметры периода в (period, since, until, date_from, date_to).
    since/until — границы в UTC, until всегда задан и включителен."""
    now = dt.datetime.now(dt.timezone.utc)
    if period == "custom" and date_from and date_to:
        try:
            since = dt.datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            until = dt.datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=dt.timezone.utc
            )
            if since <= until:
                return "custom", since, until, date_from, date_to
        except ValueError:
            pass
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "today", since, now, None, None
    if period in PERIOD_DAYS:
        days = PERIOD_DAYS[period]
        since = now - dt.timedelta(days=days) if days else None
        return period, since, now, None, None
    return "30", now - dt.timedelta(days=30), now, None, None


def _granularity_for(since: dt.datetime | None, until: dt.datetime) -> str:
    """Короткие периоды (сегодня, свой период до 2 дней) — почасовая детализация,
    иначе — по дням."""
    if since is None:
        return "day"
    return "hour" if (until - since) <= dt.timedelta(days=2) else "day"


def _bucket_step(granularity: str) -> dt.timedelta:
    return dt.timedelta(hours=1) if granularity == "hour" else dt.timedelta(days=1)


def _bucket_key(moment: dt.datetime, granularity: str):
    if granularity == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.date()


def _bucket_label(key, granularity: str) -> str:
    if granularity == "hour":
        return key.strftime("%Y-%m-%d %H:%M")
    return key.isoformat()


async def _subscriber_growth_series(
    session, target_ids: list[int], since: dt.datetime | None, until: dt.datetime, granularity: str = "day"
) -> list[dict]:
    """Суммарный ряд «подписчиков по сети» по бакетам (день/час): для каждого
    канала — последнее известное значение на бакет (с переносом вперёд на
    бакеты без снимка), сумма по всем каналам."""
    if not target_ids:
        return []

    baseline: dict[int, int] = {}
    if since is not None:
        baseline_result = await session.execute(
            select(ChannelStatsDaily.target_channel_id, func.max(ChannelStatsDaily.snapshot_at))
            .where(ChannelStatsDaily.target_channel_id.in_(target_ids), ChannelStatsDaily.snapshot_at < since)
            .group_by(ChannelStatsDaily.target_channel_id)
        )
        for ch_id, last_before in baseline_result.all():
            value = await session.scalar(
                select(ChannelStatsDaily.subscriber_count)
                .where(ChannelStatsDaily.target_channel_id == ch_id, ChannelStatsDaily.snapshot_at == last_before)
            )
            if value is not None:
                baseline[ch_id] = value

    snaps_q = select(ChannelStatsDaily).where(
        ChannelStatsDaily.target_channel_id.in_(target_ids), ChannelStatsDaily.snapshot_at <= until
    )
    if since is not None:
        snaps_q = snaps_q.where(ChannelStatsDaily.snapshot_at >= since)
    snaps_q = snaps_q.order_by(ChannelStatsDaily.snapshot_at)
    snaps = list((await session.execute(snaps_q)).scalars())
    if not snaps and not baseline:
        return []

    by_bucket: dict = defaultdict(dict)
    for s in snaps:
        by_bucket[_bucket_key(s.snapshot_at, granularity)][s.target_channel_id] = s.subscriber_count

    buckets = sorted(by_bucket.keys())
    if since is not None:
        start_bucket = _bucket_key(since, granularity)
        if start_bucket not in by_bucket:
            buckets = [start_bucket] + buckets
    running = dict(baseline)
    series = []
    for bucket in buckets:
        running.update(by_bucket.get(bucket, {}))
        if running:
            series.append({"date": _bucket_label(bucket, granularity), "total": sum(running.values())})
    return series


async def _subscriber_flow_series(
    session, target_ids: list[int], since: dt.datetime | None, until: dt.datetime, granularity: str = "day"
) -> tuple[list[dict], int, int]:
    """Join/leave по бакетам (день/час) за период + суммарные join/leave (для % соотношения)."""
    if not target_ids:
        return [], 0, 0

    events_q = select(SubscriberEvent.occurred_at, SubscriberEvent.event_type).where(
        SubscriberEvent.target_channel_id.in_(target_ids), SubscriberEvent.occurred_at <= until
    )
    if since is not None:
        events_q = events_q.where(SubscriberEvent.occurred_at >= since)
    events = (await session.execute(events_q)).all()

    counts: dict = defaultdict(lambda: {"join": 0, "leave": 0})
    total_joins = 0
    total_leaves = 0
    for occurred_at, event_type in events:
        bucket = _bucket_key(occurred_at, granularity)
        if event_type in ("join", "leave"):
            counts[bucket][event_type] += 1
            if event_type == "join":
                total_joins += 1
            else:
                total_leaves += 1

    if not counts:
        return [], 0, 0

    start_bucket = _bucket_key(since, granularity) if since is not None else min(counts.keys())
    end_bucket = _bucket_key(until, granularity)
    step = _bucket_step(granularity)
    series = []
    bucket = start_bucket
    while bucket <= end_bucket:
        row = counts.get(bucket, {"join": 0, "leave": 0})
        series.append({"date": _bucket_label(bucket, granularity), "joins": row["join"], "leaves": row["leave"]})
        bucket += step
    return series, total_joins, total_leaves


@app.get("/")
async def dashboard(request: Request, period: str = "30", date_from: str | None = None, date_to: str | None = None):
    period, since, until, date_from, date_to = _resolve_period(period, date_from, date_to)
    granularity = _granularity_for(since, until)

    async with session_scope() as session:
        accounts_result = await session.execute(
            select(UserbotAccount).options(selectinload(UserbotAccount.sources)).order_by(UserbotAccount.id)
        )
        accounts = list(accounts_result.scalars())
        accounts_online = sum(1 for a in accounts if a.status == "active")

        sources_total = await session.scalar(select(func.count()).select_from(Source))
        sources_active = await session.scalar(select(func.count()).where(Source.active.is_(True)))

        pending_count = await session.scalar(
            select(func.count()).where(DraftPost.status == "pending_approval")
        )

        today_start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        published_today = await session.scalar(
            select(func.count()).where(PublishedPost.published_at >= today_start)
        )

        targets_result = await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))
        targets = list(targets_result.scalars())
        target_ids = [t.id for t in targets]
        subscribers_total = 0
        per_channel_latest: dict[int, int] = {}
        for t in targets:
            latest = await session.scalar(
                select(ChannelStatsDaily.subscriber_count)
                .where(ChannelStatsDaily.target_channel_id == t.id)
                .order_by(ChannelStatsDaily.snapshot_at.desc())
                .limit(1)
            )
            if latest:
                subscribers_total += latest
                per_channel_latest[t.id] = latest

        growth_series = await _subscriber_growth_series(session, target_ids, since, until, granularity)
        flow_series, period_joins, period_leaves = await _subscriber_flow_series(
            session, target_ids, since, until, granularity
        )

        channel_breakdown = []
        for t in targets:
            ch_growth = await _subscriber_growth_series(session, [t.id], since, until, granularity)
            first = ch_growth[0]["total"] if ch_growth else None
            last = per_channel_latest.get(t.id)
            pct = ((last - first) / first * 100) if first and last is not None else None
            channel_breakdown.append(
                {
                    "username": t.username,
                    "latest_count": last,
                    "growth_pct": pct,
                }
            )

    growth_first = growth_series[0]["total"] if growth_series else None
    growth_last = growth_series[-1]["total"] if growth_series else None
    growth_pct = ((growth_last - growth_first) / growth_first * 100) if growth_first else None
    growth_abs = (growth_last - growth_first) if (growth_first is not None and growth_last is not None) else None

    period_total_events = period_joins + period_leaves
    join_share = (period_joins / period_total_events * 100) if period_total_events else None
    leave_share = (period_leaves / period_total_events * 100) if period_total_events else None

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "accounts": accounts,
            "accounts_online": accounts_online,
            "accounts_total": len(accounts),
            "sources_active": sources_active or 0,
            "sources_total": sources_total or 0,
            "pending_count": pending_count or 0,
            "published_today": published_today or 0,
            "subscribers_total": subscribers_total,
            "targets_count": len(targets),
            "max_sources": settings.max_sources_per_account,
            "period": period,
            "date_from": date_from,
            "date_to": date_to,
            "growth_series": growth_series,
            "flow_series": flow_series,
            "growth_pct": growth_pct,
            "growth_abs": growth_abs,
            "period_joins": period_joins,
            "period_leaves": period_leaves,
            "join_share": join_share,
            "leave_share": leave_share,
            "channel_breakdown": channel_breakdown,
        },
    )


# --- Сеть: источники + аккаунты + целевые каналы (одна вкладка футера) ------

@app.get("/network")
async def network_page(request: Request, tab: str = "sources"):
    async with session_scope() as session:
        sources_result = await session.execute(
            select(Source).options(selectinload(Source.userbot_account)).order_by(Source.id)
        )
        sources = list(sources_result.scalars())
        accounts_result = await session.execute(
            select(UserbotAccount).options(selectinload(UserbotAccount.sources)).order_by(UserbotAccount.id)
        )
        accounts = list(accounts_result.scalars())
        targets_result = await session.execute(select(TargetChannel).order_by(TargetChannel.id))
        targets = list(targets_result.scalars())

    return templates.TemplateResponse(
        request,
        "network.html",
        {
            "active": tab if tab in ("sources", "accounts", "targets") else "sources",
            "active_tab": tab if tab in ("sources", "accounts", "targets") else "sources",
            "sources": sources,
            "accounts": accounts,
            "targets": targets,
            "max_sources": settings.max_sources_per_account,
        },
    )


@app.get("/sources")
async def sources_page_redirect():
    return RedirectResponse("/network?tab=sources", status_code=308)


@app.get("/accounts")
async def accounts_page_redirect():
    return RedirectResponse("/network?tab=accounts", status_code=308)


@app.get("/targets")
async def targets_page_redirect():
    return RedirectResponse("/network?tab=targets", status_code=308)


@app.post("/sources")
async def create_source(
    username: str = Form(...),
    lang: str = Form("auto"),
    mode: str = Form("rewrite"),
    userbot_account_id: int = Form(...),
):
    async with session_scope() as session:
        session.add(
            Source(
                username=username.lstrip("@"),
                lang=lang,
                mode=mode,
                userbot_account_id=userbot_account_id,
                added_by="admin-panel",
            )
        )
    return RedirectResponse("/network?tab=sources", status_code=303)


@app.post("/sources/{source_id}/toggle")
async def toggle_source(source_id: int):
    async with session_scope() as session:
        source = await session.get(Source, source_id)
        if source:
            source.active = not source.active
    return RedirectResponse("/network?tab=sources", status_code=303)


@app.post("/sources/{source_id}/delete")
async def delete_source(source_id: int):
    async with session_scope() as session:
        source = await session.get(Source, source_id)
        if source:
            await session.delete(source)
    return RedirectResponse("/network?tab=sources", status_code=303)


@app.post("/targets")
async def create_target(username: str = Form(...), network_tag: str = Form(""), lang: str = Form("ru")):
    clean_username = username.lstrip("@")
    # Числовой chat_id нужен для сопоставления chat_member-апдейтов (статистика
    # подписчиков по инвайт-ссылкам, news_agent/stats/events.py ищет TargetChannel
    # именно по нему) — без него события подписки/отписки никогда не находят канал.
    tg_chat_id = None
    bot = build_bot(settings.bot_token)
    try:
        chat = await bot.get_chat(chat_id=f"@{clean_username}")
        tg_chat_id = chat.id
    except Exception:
        pass
    finally:
        await bot.session.close()

    async with session_scope() as session:
        session.add(
            TargetChannel(username=clean_username, network_tag=network_tag, lang=lang, tg_chat_id=tg_chat_id)
        )
    return RedirectResponse("/network?tab=targets", status_code=303)


@app.post("/targets/{target_id}/toggle")
async def toggle_target(target_id: int):
    async with session_scope() as session:
        target = await session.get(TargetChannel, target_id)
        if target:
            target.active = not target.active
    return RedirectResponse("/network?tab=targets", status_code=303)


# --- Очередь на утверждение (view-only история) ------------------------------

@app.get("/queue")
async def queue_page(request: Request):
    async with session_scope() as session:
        result = await session.execute(
            select(DraftPost)
            .options(selectinload(DraftPost.target_channel), selectinload(DraftPost.raw_post).selectinload(RawPost.source))
            .order_by(DraftPost.created_at.desc())
            .limit(200)
        )
        drafts = list(result.scalars())

    rows = []
    for d in drafts:
        source = d.raw_post.source if d.raw_post else None
        rows.append(
            {
                "id": d.id,
                "source_label": (source.title or source.username) if source else "—",
                "translated_text": d.translated_text,
                "status": d.status,
                "target_channel": d.target_channel,
                "decided_by": d.decided_by,
                "decided_at": d.decided_at,
                "created_at": d.created_at,
            }
        )
    return templates.TemplateResponse(request, "queue.html", {"active": "queue", "drafts": rows})


# --- Статистика --------------------------------------------------------------

@app.get("/stats")
async def stats_page(request: Request, sort: str = "recent"):
    async with session_scope() as session:
        channels_result = await session.execute(select(TargetChannel).order_by(TargetChannel.id))
        channels = list(channels_result.scalars())

        chart_series: dict[str, list[dict]] = {}
        channel_rows = []
        for ch in channels:
            snaps_result = await session.execute(
                select(ChannelStatsDaily)
                .where(ChannelStatsDaily.target_channel_id == ch.id)
                .order_by(ChannelStatsDaily.snapshot_at)
            )
            snaps = list(snaps_result.scalars())
            chart_series[str(ch.id)] = [
                {"date": s.snapshot_at.strftime("%Y-%m-%d %H:%M"), "count": s.subscriber_count} for s in snaps
            ]
            channel_rows.append(
                {
                    "id": ch.id,
                    "username": ch.username,
                    "network_tag": ch.network_tag,
                    "latest_count": snaps[-1].subscriber_count if snaps else None,
                }
            )

        invite_result = await session.execute(select(InviteLink).options(selectinload(InviteLink.target_channel)))
        invite_links = list(invite_result.scalars())

        # Один сгруппированный запрос вместо двух (join/leave) на каждую ссылку —
        # раньше это была классическая N+1 проблема (до 19 запросов при 7 ссылках),
        # заметно замедлявшая именно эту вкладку.
        counts_result = await session.execute(
            select(SubscriberEvent.invite_link_id, SubscriberEvent.event_type, func.count())
            .where(SubscriberEvent.invite_link_id.isnot(None))
            .group_by(SubscriberEvent.invite_link_id, SubscriberEvent.event_type)
        )
        counts_by_link: dict[int, dict[str, int]] = {}
        for link_id, event_type, cnt in counts_result.all():
            counts_by_link.setdefault(link_id, {})[event_type] = cnt

        invite_stats = []
        for link in invite_links:
            link_counts = counts_by_link.get(link.id, {})
            invite_stats.append(
                {
                    "id": link.id,
                    "name": link.name,
                    "tg_invite_link": link.tg_invite_link,
                    "channel_username": link.target_channel.username if link.target_channel else "?",
                    "source_label": link.source_label,
                    "joins": link_counts.get("join", 0),
                    "leaves": link_counts.get("leave", 0),
                    "revoked": link.revoked,
                }
            )

        direct_joins = await session.scalar(
            select(func.count()).where(SubscriberEvent.is_direct.is_(True), SubscriberEvent.event_type == "join")
        )
        direct_leaves = await session.scalar(
            select(func.count()).where(SubscriberEvent.is_direct.is_(True), SubscriberEvent.event_type == "leave")
        )

    if sort == "joins":
        invite_stats.sort(key=lambda r: r["joins"], reverse=True)
    elif sort == "leaves":
        invite_stats.sort(key=lambda r: r["leaves"], reverse=True)
    else:
        sort = "recent"

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "active": "stats",
            "channels": channel_rows,
            "target_channels": channels,
            "chart_series": chart_series,
            "invite_stats": invite_stats,
            "invite_sort": sort,
            "direct_joins": direct_joins or 0,
            "direct_leaves": direct_leaves or 0,
            "invite_error": request.query_params.get("invite_error"),
        },
    )


@app.post("/invite_links")
async def create_invite_link(
    target_channel_id: int = Form(...),
    name: str = Form(...),
    source_label: str = Form(""),
    campaign_tag: str = Form(""),
):
    name = name.strip()[:32]
    async with session_scope() as session:
        target = await session.get(TargetChannel, target_channel_id)
    if target is None:
        return RedirectResponse("/stats?invite_error=Канал+не+найден", status_code=303)

    chat_id = target.tg_chat_id or f"@{target.username}"
    bot = build_bot(settings.bot_token)
    try:
        tg_link = await bot.create_chat_invite_link(chat_id=chat_id, name=name)
    except Exception:
        return RedirectResponse(
            "/stats?invite_error=Не+удалось+создать+ссылку+—+бот+должен+быть+админом+канала", status_code=303
        )
    finally:
        await bot.session.close()

    async with session_scope() as session:
        session.add(
            InviteLink(
                target_channel_id=target_channel_id,
                name=name,
                tg_invite_link=tg_link.invite_link,
                source_label=source_label,
                campaign_tag=campaign_tag,
                created_by="admin-panel",
            )
        )
    return RedirectResponse("/stats", status_code=303)


@app.post("/invite_links/{link_id}/revoke")
async def revoke_invite_link(link_id: int):
    async with session_scope() as session:
        link = await session.get(InviteLink, link_id)
        if link is None:
            return RedirectResponse("/stats", status_code=303)
        target = await session.get(TargetChannel, link.target_channel_id)
        chat_id = target.tg_chat_id or f"@{target.username}"
        invite_link_value = link.tg_invite_link

    bot = build_bot(settings.bot_token)
    try:
        await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link_value)
    except Exception:
        pass
    finally:
        await bot.session.close()

    async with session_scope() as session:
        link = await session.get(InviteLink, link_id)
        if link:
            link.revoked = True
    return RedirectResponse("/stats", status_code=303)


# --- Настройки (read-only) ---------------------------------------------------

@app.get("/settings")
async def settings_page(request: Request):
    masked = {
        "telegram_api_id": str(settings.telegram_api_id) if settings.telegram_api_id else "— не задано —",
        "telegram_api_hash": _mask(settings.telegram_api_hash),
        "bot_token": _mask(settings.bot_token),
        "anthropic_api_key": _mask(settings.anthropic_api_key),
        "gemini_api_key": _mask(settings.gemini_api_key),
        "database_url": _mask(settings.database_url, keep=12),
    }
    return templates.TemplateResponse(
        request, "settings.html", {"active": "settings", "settings": settings, "masked": masked}
    )
