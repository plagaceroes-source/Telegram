"""Сбор публичных репостов наших постов в другие каналы/группы.

Использует MTProto-метод stats.getMessagePublicForwards — Bot API такого не даёт.
Метод требует, чтобы аккаунт юзербота был администратором целевого канала
(достаточно админа без каких-либо прав) — иначе Telegram отвечает CHAT_ADMIN_REQUIRED."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from telethon import TelegramClient, errors, utils
from telethon.tl.functions.stats import GetMessagePublicForwardsRequest
from telethon.tl.types import Channel, PublicForwardMessage

from news_agent.db.models import PostForward, PostStats, PublishedPost, TargetChannel
from news_agent.db.session import session_scope

logger = logging.getLogger(__name__)

PAGE_LIMIT = 100
MAX_PAGES = 5
PAUSE_BETWEEN_CALLS = 0.5


def _chat_info(chat) -> tuple[str, str, str]:
    if chat is None:
        return "", "", "channel"
    title = getattr(chat, "title", "") or ""
    username = getattr(chat, "username", "") or ""
    is_group = isinstance(chat, Channel) and bool(getattr(chat, "megagroup", False))
    if not isinstance(chat, Channel):
        is_group = True  # обычная группа (Chat)
    return title, username, "group" if is_group else "channel"


async def _stats_call(client: TelegramClient, request, stats_state: dict):
    """Статистика канала живёт на отдельном дата-центре: Telegram отвечает StatsMigrateError(dc).
    Telethon эту ошибку сам не обрабатывает, поэтому переадресуем запрос на нужный DC через
    выделенный отправитель (как Telethon делает при скачивании файлов с чужого DC)."""
    dc = stats_state.get("dc")
    if dc is None:
        try:
            return await client(request)
        except errors.StatsMigrateError as e:
            dc = stats_state["dc"] = e.dc
    sender = await client._borrow_exported_sender(dc)
    try:
        return await client._call(sender, request)
    finally:
        await client._return_exported_sender(sender)


async def _fetch_post_forwards(
    client: TelegramClient, entity, msg_id: int, own_chat_id: int, stats_state: dict
) -> list[dict]:
    """Все публичные репосты одного поста (с пагинацией). Ошибки прав/лимитов пробрасываются."""
    found: list[dict] = []
    offset = ""
    for _ in range(MAX_PAGES):
        res = await _stats_call(
            client,
            GetMessagePublicForwardsRequest(channel=entity, msg_id=msg_id, offset=offset, limit=PAGE_LIMIT),
            stats_state,
        )
        chats_by_id = {c.id: c for c in res.chats}
        for fw in res.forwards:
            if not isinstance(fw, PublicForwardMessage):
                continue  # репосты в истории нам не нужны
            msg = fw.message
            peer = getattr(msg, "peer_id", None)
            if peer is None:
                continue
            chat_id = utils.get_peer_id(peer)
            if chat_id == own_chat_id:
                continue
            raw_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
            title, username, chat_type = _chat_info(chats_by_id.get(raw_id))
            found.append(
                {
                    "chat_id": chat_id,
                    "title": title,
                    "username": username,
                    "chat_type": chat_type,
                    "message_id": msg.id,
                    "date": msg.date,
                    "views": getattr(msg, "views", 0) or 0,
                }
            )
        if not res.next_offset:
            break
        offset = res.next_offset
        await asyncio.sleep(PAUSE_BETWEEN_CALLS)
    return found


async def collect_post_forwards(client: TelegramClient, lookback_days: int = 30) -> int:
    """Обновляет PostForward для постов за lookback_days дней. Запрашивает Telegram только
    по постам, у которых счётчик репостов > 0. Возвращает число найденных репостов."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)

    async with session_scope() as session:
        targets = list(
            (await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars()
        )

    total = 0
    for target in targets:
        async with session_scope() as session:
            posts = list(
                (
                    await session.execute(
                        select(PublishedPost)
                        .join(PostStats, PostStats.published_post_id == PublishedPost.id)
                        .where(
                            PublishedPost.target_channel_id == target.id,
                            PublishedPost.published_at >= since,
                            PostStats.forwards > 0,
                        )
                    )
                ).scalars()
            )
        if not posts:
            continue

        try:
            entity = await client.get_entity(f"@{target.username}")
        except Exception:
            logger.exception("Не удалось получить канал %s для сбора репостов", target.username)
            continue
        own_chat_id = utils.get_peer_id(entity)

        collected: dict[int, list[dict]] = {}
        stats_state: dict = {}
        for post in posts:
            try:
                collected[post.id] = await _fetch_post_forwards(
                    client, entity, post.tg_message_id, own_chat_id, stats_state
                )
            except errors.ChatAdminRequiredError:
                logger.warning(
                    "Канал %s: CHAT_ADMIN_REQUIRED — чтобы видеть репосты, аккаунт юзербота должен быть "
                    "администратором канала (права не нужны). Сбор репостов пропущен.",
                    target.username,
                )
                break
            except errors.FloodWaitError as e:
                logger.warning("Канал %s: FloodWait %sс при сборе репостов, прерываю цикл", target.username, e.seconds)
                break
            except errors.RPCError as e:
                logger.warning("Канал %s: ошибка сбора репостов поста %s: %s", target.username, post.tg_message_id, e)
            await asyncio.sleep(PAUSE_BETWEEN_CALLS)

        logger.info(
            "Канал %s: репосты проверены у %d из %d постов с ненулевым счётчиком, найдено публичных репостов: %d",
            target.username,
            len(collected),
            len(posts),
            sum(len(v) for v in collected.values()),
        )
        if not collected:
            continue

        async with session_scope() as session:
            existing_result = await session.execute(
                select(PostForward).where(PostForward.published_post_id.in_(list(collected.keys())))
            )
            existing = {
                (r.published_post_id, r.forward_chat_id, r.forward_message_id): r for r in existing_result.scalars()
            }
            for post_id, forwards in collected.items():
                for f in forwards:
                    key = (post_id, f["chat_id"], f["message_id"])
                    row = existing.get(key)
                    if row is None:
                        row = PostForward(
                            published_post_id=post_id,
                            forward_chat_id=f["chat_id"],
                            forward_message_id=f["message_id"],
                            forwarded_at=f["date"],
                        )
                        session.add(row)
                        existing[key] = row
                    row.forward_chat_title = f["title"]
                    row.forward_chat_username = f["username"]
                    row.forward_chat_type = f["chat_type"]
                    row.views = f["views"]
                    total += 1

    return total
