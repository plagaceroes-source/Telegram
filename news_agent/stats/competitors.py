"""Сбор данных по каналам-конкурентам: число подписчиков и метрики постов за 30 дней.

Берётся только то, что видно любому читателю публичного канала (через аккаунт юзербота):
подписчики (GetFullChannel) и просмотры последних постов."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

from news_agent.db.models import Competitor, CompetitorSnapshot
from news_agent.db.session import session_scope
from news_agent.stats.channel_posts import group_messages

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
ITER_LIMIT = 1500
PAUSE_BETWEEN_CHANNELS = 2.0


def summarize_posts(posts: list[dict], now: dt.datetime) -> dict:
    """posts — результат group_messages за окно WINDOW_DAYS."""
    week_ago = now - dt.timedelta(days=7)
    views = sum(p["views"] for p in posts)
    return {
        "posts_30d": len(posts),
        "posts_7d": sum(1 for p in posts if p["date"] >= week_ago),
        "avg_views_30d": round(views / len(posts)) if posts else 0,
    }


async def fetch_competitor(client: TelegramClient, username: str) -> dict:
    entity = await client.get_entity(username)
    if not isinstance(entity, Channel) or getattr(entity, "megagroup", False):
        raise ValueError("это не канал (группа или пользователь)")
    full = await client(GetFullChannelRequest(entity))
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=WINDOW_DAYS)
    messages = []
    async for msg in client.iter_messages(entity, limit=ITER_LIMIT):
        if msg.date < since:
            break
        messages.append(msg)
    result = summarize_posts(group_messages(messages), now)
    result["title"] = entity.title or ""
    result["subscribers"] = full.full_chat.participants_count or 0
    return result


async def collect_competitors(client: TelegramClient, min_interval: int) -> int:
    """Обновляет конкурентов, у которых данные старше min_interval секунд (или ещё не собирались)."""
    now = dt.datetime.now(dt.timezone.utc)
    async with session_scope() as session:
        rows = list((await session.execute(select(Competitor))).scalars())
    due = [
        c for c in rows
        if c.last_fetched_at is None
        or (now - (c.last_fetched_at if c.last_fetched_at.tzinfo else c.last_fetched_at.replace(tzinfo=dt.timezone.utc))).total_seconds() >= min_interval
        or c.last_error  # ошибочные пробуем снова при каждом проходе — вдруг канал появился/открылся
    ]
    done = 0
    for comp in due:
        data = None
        error = ""
        try:
            data = await fetch_competitor(client, comp.username)
        except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
            error = "канал не найден"
        except errors.ChannelPrivateError:
            error = "канал приватный"
        except ValueError as e:
            error = str(e)[:200]
        except errors.FloodWaitError as e:
            logger.warning("Конкуренты: FloodWait %sс, прерываю проход", e.seconds)
            break
        except Exception:
            logger.exception("Конкуренты: сбой при сборе @%s", comp.username)
            error = "не удалось получить данные"

        async with session_scope() as session:
            row = await session.get(Competitor, comp.id)
            if row is None:  # удалили, пока собирали
                continue
            row.last_error = error
            if data is not None:
                row.title = data["title"]
                row.last_fetched_at = dt.datetime.now(dt.timezone.utc)
                today = dt.datetime.now(dt.timezone.utc).date()
                snap = await session.scalar(
                    select(CompetitorSnapshot).where(
                        CompetitorSnapshot.competitor_id == comp.id, CompetitorSnapshot.snap_date == today
                    )
                )
                if snap is None:
                    snap = CompetitorSnapshot(competitor_id=comp.id, snap_date=today)
                    session.add(snap)
                snap.subscribers = data["subscribers"]
                snap.posts_7d = data["posts_7d"]
                snap.posts_30d = data["posts_30d"]
                snap.avg_views_30d = data["avg_views_30d"]
                snap.captured_at = dt.datetime.now(dt.timezone.utc)
                done += 1
        logger.info(
            "Конкуренты: @%s — %s",
            comp.username,
            f"подписчиков {data['subscribers']}, постов за 30 дн. {data['posts_30d']}" if data else error,
        )
        await asyncio.sleep(PAUSE_BETWEEN_CHANNELS)
    return done
