"""Веб-админка (ТЗ 2.5): FastAPI + Jinja2, простой CRUD источников/каналов + статистика."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from news_agent.config import settings
from news_agent.db.models import (
    ChannelStatsDaily,
    DraftPost,
    InviteLink,
    RawPost,
    Source,
    SubscriberEvent,
    TargetChannel,
    UserbotAccount,
)
from news_agent.db.session import init_db, session_scope

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="News Agent Admin")


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
async def root() -> RedirectResponse:
    return RedirectResponse("/sources")


# --- Источники ------------------------------------------------------------

@app.get("/sources")
async def sources_page(request: Request):
    async with session_scope() as session:
        result = await session.execute(
            select(Source).options(selectinload(Source.userbot_account)).order_by(Source.id)
        )
        sources = list(result.scalars())
        accounts_result = await session.execute(
            select(UserbotAccount).options(selectinload(UserbotAccount.sources)).order_by(UserbotAccount.id)
        )
        accounts = list(accounts_result.scalars())
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "active": "sources",
            "sources": sources,
            "accounts": accounts,
            "max_sources": settings.max_sources_per_account,
        },
    )


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
    return RedirectResponse("/sources", status_code=303)


@app.post("/sources/{source_id}/toggle")
async def toggle_source(source_id: int):
    async with session_scope() as session:
        source = await session.get(Source, source_id)
        if source:
            source.active = not source.active
    return RedirectResponse("/sources", status_code=303)


@app.post("/sources/{source_id}/delete")
async def delete_source(source_id: int):
    async with session_scope() as session:
        source = await session.get(Source, source_id)
        if source:
            await session.delete(source)
    return RedirectResponse("/sources", status_code=303)


# --- Целевые каналы ---------------------------------------------------------

@app.get("/targets")
async def targets_page(request: Request):
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).order_by(TargetChannel.id))
        targets = list(result.scalars())
    return templates.TemplateResponse(request, "targets.html", {"active": "targets", "targets": targets})


@app.post("/targets")
async def create_target(username: str = Form(...), network_tag: str = Form(""), lang: str = Form("ru")):
    async with session_scope() as session:
        session.add(TargetChannel(username=username.lstrip("@"), network_tag=network_tag, lang=lang))
    return RedirectResponse("/targets", status_code=303)


@app.post("/targets/{target_id}/toggle")
async def toggle_target(target_id: int):
    async with session_scope() as session:
        target = await session.get(TargetChannel, target_id)
        if target:
            target.active = not target.active
    return RedirectResponse("/targets", status_code=303)


# --- Аккаунты-слушатели -----------------------------------------------------

@app.get("/accounts")
async def accounts_page(request: Request):
    async with session_scope() as session:
        result = await session.execute(
            select(UserbotAccount).options(selectinload(UserbotAccount.sources)).order_by(UserbotAccount.id)
        )
        accounts = list(result.scalars())
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"active": "accounts", "accounts": accounts, "max_sources": settings.max_sources_per_account},
    )


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
                    "name": link.name,
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
            "chart_series": chart_series,
            "invite_stats": invite_stats,
            "direct_joins": direct_joins or 0,
            "direct_leaves": direct_leaves or 0,
        },
    )


# --- Настройки (read-only) ---------------------------------------------------

@app.get("/settings")
async def settings_page(request: Request):
    masked = {
        "telegram_api_id": str(settings.telegram_api_id) if settings.telegram_api_id else "— не задано —",
        "telegram_api_hash": _mask(settings.telegram_api_hash),
        "bot_token": _mask(settings.bot_token),
        "anthropic_api_key": _mask(settings.anthropic_api_key),
        "database_url": _mask(settings.database_url, keep=12),
    }
    return templates.TemplateResponse(
        request, "settings.html", {"active": "settings", "settings": settings, "masked": masked}
    )
