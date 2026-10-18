"""Официальная аналитика канала от Telegram (stats.getBroadcastStats).

В отличие от наших собственных счётчиков (chat_member-события приходят «по возможности»)
это данные самого Telegram: точные подписки/отписки, источники новых подписчиков, языки,
уведомления, просмотры по часам. Доступна каналам от 500 подписчиков и только администратору —
аккаунт юзербота должен быть админом канала (достаточно админа без прав).

Каждый график Telegram отдаёт как JSON в формате Telegram Graph (columns/names/types) —
либо сразу, либо «лениво» (StatsGraphAsync + токен). Здесь они нормализуются в
{"kind": ..., "x": [...], "series": [{"key","name","values"}]}.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

from sqlalchemy import select
from telethon import TelegramClient, errors
from telethon.tl.functions.stats import GetBroadcastStatsRequest, LoadAsyncGraphRequest
from telethon.tl.types import StatsGraph, StatsGraphAsync, StatsGraphError

from news_agent.db.models import ChannelAnalytics, ChannelStatsDaily, TargetChannel
from news_agent.db.session import session_scope
from news_agent.stats.post_forwards import _stats_call

logger = logging.getLogger(__name__)

GRAPH_FIELDS = (
    "growth_graph",
    "followers_graph",
    "mute_graph",
    "top_hours_graph",
    "interactions_graph",
    "iv_interactions_graph",
    "views_by_source_graph",
    "new_followers_by_source_graph",
    "languages_graph",
    "reactions_by_emotion_graph",
)
VALUE_FIELDS = ("followers", "views_per_post", "shares_per_post", "reactions_per_post")


def parse_graph_json(raw: str) -> dict | None:
    """Telegram Graph JSON → {"kind","x","series"}. Колонка "x" — ось (время в мс или подписи),
    остальные колонки "yN" — ряды; для круговых графиков (languages) "x" нет, каждая колонка —
    один сектор с одним значением."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    columns = data.get("columns") or []
    names = data.get("names") or {}
    types = data.get("types") or {}
    x: list = []
    series: list[dict] = []
    for col in columns:
        if not col:
            continue
        key, values = col[0], col[1:]
        if types.get(key) == "x" or key == "x":
            x = values
        else:
            series.append({"key": key, "name": names.get(key, key), "values": values})
    kind = "pie" if not x else ("bar" if "bar" in types.values() else "line")
    return {"kind": kind, "x": x, "series": series, "stacked": bool(data.get("stacked"))}


async def _resolve_graph(client: TelegramClient, graph, stats_state: dict) -> dict | None:
    if isinstance(graph, StatsGraphAsync):
        try:
            graph = await _stats_call(client, LoadAsyncGraphRequest(token=graph.token), stats_state)
        except errors.RPCError as e:
            logger.warning("Не удалось подгрузить график статистики: %s", e)
            return None
    if isinstance(graph, StatsGraphError):
        return None
    if isinstance(graph, StatsGraph):
        return parse_graph_json(graph.json.data)
    return None


def _abs_value(v) -> dict | None:
    if v is None:
        return None
    return {"current": v.current, "previous": v.previous}


async def fetch_broadcast_stats(client: TelegramClient, entity, stats_state: dict) -> dict:
    res = await _stats_call(client, GetBroadcastStatsRequest(channel=entity), stats_state)
    out: dict = {
        "period": {
            "min_date": res.period.min_date.isoformat(),
            "max_date": res.period.max_date.isoformat(),
        },
        "enabled_notifications": {
            "part": res.enabled_notifications.part,
            "total": res.enabled_notifications.total,
        },
        "graphs": {},
    }
    for name in VALUE_FIELDS:
        out[name] = _abs_value(getattr(res, name, None))
    for name in GRAPH_FIELDS:
        parsed = await _resolve_graph(client, getattr(res, name, None), stats_state)
        if parsed is not None:
            out["graphs"][name] = parsed
        await asyncio.sleep(0.3)
    return out


BACKFILL_HOUR_UTC = 21  # точка «конца дня» для восстановленных значений (до закрытия суток по Мадриду)


async def backfill_subscriber_history(session, target_id: int, data: dict, today: dt.date | None = None) -> int:
    """Достраивает ChannelStatsDaily по официальному графику подписчиков Telegram за дни, когда
    собственных снепшотов нет (например, сломался cron). Существующие снепшоты не трогает,
    сегодняшний день пропускает — его снимет обычный снепшот."""
    graph = (data.get("graphs") or {}).get("growth_graph")
    if not graph or not graph.get("series") or not graph.get("x"):
        return 0
    today = today or dt.datetime.now(dt.timezone.utc).date()
    values = graph["series"][0]["values"]
    added = 0
    for ms, value in zip(graph["x"], values):
        if not isinstance(value, (int, float)):
            continue
        day = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date()
        if day >= today:
            continue
        start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
        exists = await session.scalar(
            select(ChannelStatsDaily.id)
            .where(
                ChannelStatsDaily.target_channel_id == target_id,
                ChannelStatsDaily.snapshot_at >= start,
                ChannelStatsDaily.snapshot_at < start + dt.timedelta(days=1),
            )
            .limit(1)
        )
        if exists is None:
            session.add(
                ChannelStatsDaily(
                    target_channel_id=target_id,
                    subscriber_count=int(value),
                    snapshot_at=start + dt.timedelta(hours=BACKFILL_HOUR_UTC),
                )
            )
            added += 1
    return added


async def collect_tg_analytics(client: TelegramClient) -> int:
    """Обновляет ChannelAnalytics по каждому активному каналу. Возвращает число обновлённых."""
    async with session_scope() as session:
        targets = list(
            (await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars()
        )
    done = 0
    for target in targets:
        try:
            entity = await client.get_entity(f"@{target.username}")
            data = await fetch_broadcast_stats(client, entity, {})
        except errors.ChatAdminRequiredError:
            logger.warning(
                "Канал %s: CHAT_ADMIN_REQUIRED — для официальной аналитики Telegram аккаунт юзербота "
                "должен быть администратором канала.",
                target.username,
            )
            continue
        except errors.RPCError as e:
            logger.warning("Канал %s: официальная аналитика недоступна: %s", target.username, e)
            continue
        except Exception:
            logger.exception("Канал %s: сбой при сборе официальной аналитики", target.username)
            continue

        async with session_scope() as session:
            row = await session.scalar(
                select(ChannelAnalytics).where(ChannelAnalytics.target_channel_id == target.id)
            )
            if row is None:
                row = ChannelAnalytics(target_channel_id=target.id, data=data)
                session.add(row)
            row.data = data
            row.fetched_at = dt.datetime.now(dt.timezone.utc)
            restored = await backfill_subscriber_history(session, target.id, data)
        if restored:
            logger.warning(
                "Канал %s: в истории подписчиков не было снепшотов за %d дн. — восстановлено по данным Telegram",
                target.username,
                restored,
            )
        logger.info(
            "Канал %s: официальная аналитика Telegram обновлена, графики: %s",
            target.username,
            ", ".join(f"{k}({v['kind']},{len(v['series'])})" for k, v in data["graphs"].items()) or "нет",
        )
        done += 1
    return done
