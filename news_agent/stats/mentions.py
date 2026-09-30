"""Поиск упоминаний нашего канала (@username, ссылка t.me/..., инвайт-ссылки) в чужих
каналах и группах.

Два источника: глобальный поиск Telegram (messages.searchGlobal) и поиск по каналам-источникам,
на которые подписан юзербот. Поиск Telegram нечёткий, поэтому каждое найденное сообщение
дополнительно проверяется регулярным выражением — в базу попадают только реальные упоминания."""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select
from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat

from news_agent.bot.publish import SUBSCRIBE_LINK
from news_agent.config import settings
from news_agent.db.models import ChannelMention, InviteLink, Source, TargetChannel
from news_agent.db.session import session_scope
from news_agent.stats.post_forwards import chat_info

logger = logging.getLogger(__name__)

GLOBAL_LIMIT = 100
SOURCE_LIMIT = 30
PAUSE_BETWEEN_CALLS = 0.5
SNIPPET_LEN = 300
_LINK_HOSTS = r"(?:t\.me|telegram\.me|telegram\.dog)"


def invite_code(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1].lstrip("+")


def build_patterns(username: str, invite_codes: list[str]) -> list[tuple[str, re.Pattern]]:
    """[(человекочитаемая метка, regex)] для @username, t.me/username и инвайт-кодов."""
    u = re.escape(username)
    patterns = [
        (f"@{username}", re.compile(rf"(?<![A-Za-z0-9_])@{u}(?![A-Za-z0-9_])", re.I)),
        (f"t.me/{username}", re.compile(rf"{_LINK_HOSTS}/{u}(?![A-Za-z0-9_])", re.I)),
    ]
    for code in invite_codes:
        c = re.escape(code)
        patterns.append((f"t.me/+{code}", re.compile(rf"(?:{_LINK_HOSTS}/\+|joinchat/){c}(?![A-Za-z0-9_-])")))
    return patterns


def message_text(msg) -> str:
    """Текст сообщения + адреса из ссылок-сущностей (текст ссылки может отличаться от адреса)."""
    parts = [msg.message or ""]
    for ent in msg.entities or []:
        url = getattr(ent, "url", None)
        if url:
            parts.append(url)
    return "\n".join(parts)


def find_match(msg, patterns: list[tuple[str, re.Pattern]]) -> str | None:
    text = message_text(msg)
    for label, rx in patterns:
        if rx.search(text):
            return label
    return None


def _to_hit(msg, matched: str) -> dict | None:
    chat = msg.chat
    if not isinstance(chat, (Channel, Chat)):
        return None  # личные диалоги и боты нам не интересны
    title, username, chat_type = chat_info(chat)
    return {
        "chat_id": msg.chat_id,
        "title": title,
        "username": username,
        "chat_type": chat_type,
        "message_id": msg.id,
        "date": msg.date,
        "snippet": re.sub(r"\s+", " ", msg.message or "").strip()[:SNIPPET_LEN],
        "matched": matched,
        "views": getattr(msg, "views", 0) or 0,
    }


async def collect_mentions(client: TelegramClient) -> tuple[int, int]:
    """Возвращает (подтверждённых упоминаний за цикл, новых записей в базе)."""
    async with session_scope() as session:
        targets = list(
            (await session.execute(select(TargetChannel).where(TargetChannel.active.is_(True)))).scalars()
        )
        codes = [
            invite_code(link)
            for link in (await session.execute(select(InviteLink.tg_invite_link).where(InviteLink.revoked.is_(False)))).scalars()
        ]
        sources = list(
            (
                await session.execute(
                    select(Source).where(Source.active.is_(True), Source.joined.is_(True), Source.username != "")
                )
            ).scalars()
        )
    codes = sorted({*codes, invite_code(SUBSCRIBE_LINK)})

    own_chat_ids = {t.tg_chat_id for t in targets if t.tg_chat_id}
    own_chat_ids |= {c for c in (settings.approval_chat_id, settings.reports_chat_id) if c}
    bot_id = int(settings.bot_token.split(":")[0]) if settings.bot_token else None

    hits: dict[tuple[int, int], dict] = {}

    def consider(msg, patterns) -> None:
        if getattr(msg, "out", False) or msg.chat_id in own_chat_ids:
            return
        if bot_id is not None and getattr(msg, "sender_id", None) == bot_id:
            return
        matched = find_match(msg, patterns)
        if matched is None:
            return
        hit = _to_hit(msg, matched)
        if hit is not None:
            hits[(hit["chat_id"], hit["message_id"])] = hit

    try:
        for target in targets:
            patterns = build_patterns(target.username, codes)
            queries = [f"@{target.username}", f"t.me/{target.username}", *codes]
            for q in queries:
                before = len(hits)
                async for msg in client.iter_messages(None, search=q, limit=GLOBAL_LIMIT):
                    consider(msg, patterns)
                logger.info("Упоминания: глобальный поиск «%s» — новых подтверждённых %d", q, len(hits) - before)
                await asyncio.sleep(PAUSE_BETWEEN_CALLS)

            for src in sources:
                before = len(hits)
                try:
                    async for msg in client.iter_messages(src.username, search=f"@{target.username}", limit=SOURCE_LIMIT):
                        consider(msg, patterns)
                except errors.FloodWaitError:
                    raise
                except Exception as e:
                    logger.warning("Упоминания: не удалось просканировать источник %s: %s", src.username, e)
                if len(hits) > before:
                    logger.info("Упоминания: источник %s — подтверждённых %d", src.username, len(hits) - before)
                await asyncio.sleep(PAUSE_BETWEEN_CALLS)
    except errors.FloodWaitError as e:
        logger.warning("Упоминания: FloodWait %sс, цикл прерван (найденное сохраню)", e.seconds)

    new = 0
    if hits:
        async with session_scope() as session:
            existing_result = await session.execute(
                select(ChannelMention).where(
                    ChannelMention.chat_id.in_({k[0] for k in hits}),
                    ChannelMention.message_id.in_({k[1] for k in hits}),
                )
            )
            existing = {(r.chat_id, r.message_id): r for r in existing_result.scalars()}
            for key, h in hits.items():
                row = existing.get(key)
                if row is None:
                    row = ChannelMention(chat_id=h["chat_id"], message_id=h["message_id"], message_date=h["date"])
                    session.add(row)
                    new += 1
                row.chat_title = h["title"]
                row.chat_username = h["username"]
                row.chat_type = h["chat_type"]
                row.snippet = h["snippet"]
                row.matched = h["matched"]
                row.views = h["views"]
    return len(hits), new
