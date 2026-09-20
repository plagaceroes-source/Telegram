"""Веб-админка (ТЗ 2.5): FastAPI + Jinja2, простой CRUD источников/каналов + статистика."""
from __future__ import annotations

import datetime as dt
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


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "— не задано —"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "…" + "*" * 4


@app.get("/")
async def dashboard(request: Request):
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
        subscribers_total = 0
        for t in targets:
            latest = await session.scalar(
                select(ChannelStatsDaily.subscriber_count)
                .where(ChannelStatsDaily.target_channel_id == t.id)
                .order_by(ChannelStatsDaily.snapshot_at.desc())
                .limit(1)
            )
            if latest:
                subscribers_total += latest

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
async def stats_page(request: Request):
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

        invite_stats = []
        for link in invite_links:
            joins = await session.scalar(
                select(func.count()).where(SubscriberEvent.invite_link_id == link.id, SubscriberEvent.event_type == "join")
            )
            leaves = await session.scalar(
                select(func.count()).where(SubscriberEvent.invite_link_id == link.id, SubscriberEvent.event_type == "leave")
            )
            invite_stats.append(
                {
                    "id": link.id,
                    "name": link.name,
                    "tg_invite_link": link.tg_invite_link,
                    "channel_username": link.target_channel.username if link.target_channel else "?",
                    "source_label": link.source_label,
                    "joins": joins or 0,
                    "leaves": leaves or 0,
                    "revoked": link.revoked,
                }
            )

        direct_joins = await session.scalar(
            select(func.count()).where(SubscriberEvent.is_direct.is_(True), SubscriberEvent.event_type == "join")
        )
        direct_leaves = await session.scalar(
            select(func.count()).where(SubscriberEvent.is_direct.is_(True), SubscriberEvent.event_type == "leave")
        )

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "active": "stats",
            "channels": channel_rows,
            "target_channels": channels,
            "chart_series": chart_series,
            "invite_stats": invite_stats,
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
