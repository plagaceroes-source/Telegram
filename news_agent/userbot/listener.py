"""Telethon-юзербот: слушает назначенные ему источники и кладёт сырые посты в БД.

Один процесс = один аккаунт (ТЗ п.2.1): падение/ограничение одного аккаунта
не должно останавливать мониторинг через остальные.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path

from sqlalchemy import func, select
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest

from news_agent.bot.session import build_bot
from news_agent.config import settings
from news_agent.db.models import RawPost, Source, UserbotAccount
from news_agent.db.session import session_scope
from news_agent.services.language import detect_language
from news_agent.services.media_relay import upload_and_get_ref
from news_agent.stats.post_stats import collect_post_stats

logger = logging.getLogger(__name__)


class AccountWorker:
    """Обслуживает один Telethon-аккаунт: подписки на источники + сбор постов."""

    def __init__(self, account_id: int) -> None:
        self.account_id = account_id
        self.client: TelegramClient | None = None
        self.bot = build_bot(settings.bot_token) if settings.bot_token else None

    async def start(self) -> None:
        async with session_scope() as session:
            account = await session.get(UserbotAccount, self.account_id)
            if account is None:
                raise ValueError(f"Аккаунт юзербота {self.account_id} не найден в БД")
            session_name = account.session_name

        session_path = os.path.join(settings.sessions_path, session_name)
        Path(settings.sessions_path).mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)

        await self.client.start()
        logger.info("Аккаунт %s: сессия запущена", self.account_id)

        self.client.add_event_handler(self._on_album, events.Album())
        self.client.add_event_handler(self._on_message, events.NewMessage())

        await asyncio.gather(
            self._sync_sources_loop(),
            self._post_stats_loop(),
            self.client.run_until_disconnected(),
        )

    async def _is_post_stats_owner(self) -> bool:
        """Сбор статистики постов должен вести только один аккаунт — иначе при
        нескольких юзерботах одна и та же работа задваивалась бы. Детерминированно
        избираем аккаунт с наименьшим id среди активных/ограниченных."""
        async with session_scope() as session:
            min_id = await session.scalar(
                select(func.min(UserbotAccount.id)).where(UserbotAccount.status != "disabled")
            )
        return min_id == self.account_id

    async def _post_stats_loop(self) -> None:
        while True:
            try:
                if await self._is_post_stats_owner():
                    updated = await collect_post_stats(self.client, settings.post_stats_lookback_days)
                    if updated:
                        logger.info("Аккаунт %s: обновлена статистика %d постов", self.account_id, updated)
            except Exception:
                logger.exception("Аккаунт %s: ошибка сбора статистики постов", self.account_id)
            await asyncio.sleep(settings.post_stats_poll_interval)

    async def _sync_sources_loop(self) -> None:
        """Периодически подтягивает список источников из БД и постепенно вступает в новые."""
        while True:
            try:
                await self._sync_sources_once()
            except Exception:
                logger.exception("Аккаунт %s: ошибка синхронизации источников", self.account_id)
            await asyncio.sleep(settings.sources_poll_interval)

    async def _sync_sources_once(self) -> None:
        async with session_scope() as session:
            result = await session.execute(
                select(Source).where(
                    Source.userbot_account_id == self.account_id,
                    Source.active.is_(True),
                    Source.joined.is_(False),
                )
            )
            pending_sources = list(result.scalars())

        for source in pending_sources:
            try:
                await self.client(JoinChannelRequest(source.username))
            except Exception:
                logger.exception("Аккаунт %s: не удалось вступить в %s", self.account_id, source.username)
                continue

            async with session_scope() as session:
                db_source = await session.get(Source, source.id)
                if db_source:
                    db_source.joined = True
            logger.info("Аккаунт %s: вступил в источник %s", self.account_id, source.username)

            # Небольшая пауза между вступлениями, чтобы не создавать всплеск активности (2.1).
            pause = random.uniform(settings.join_pause_min, settings.join_pause_max)
            await asyncio.sleep(pause)

    async def _resolve_source(self, chat) -> Source | None:
        username = getattr(chat, "username", None)
        chat_id = getattr(chat, "id", None)
        async with session_scope() as session:
            stmt = select(Source).where(Source.userbot_account_id == self.account_id)
            if username:
                stmt = stmt.where(Source.username == username)
            elif chat_id is not None:
                stmt = stmt.where(Source.tg_chat_id == chat_id)
            else:
                return None
            result = await session.execute(stmt)
            return result.scalars().first()

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        if event.message.grouped_id is not None:
            return  # альбом обрабатывается отдельным хендлером _on_album
        chat = await event.get_chat()
        source = await self._resolve_source(chat)
        if source is None or not source.active:
            return
        await self._store_post(source, [event.message])

    async def _on_album(self, event: events.Album.Event) -> None:
        chat = await event.get_chat()
        source = await self._resolve_source(chat)
        if source is None or not source.active:
            return
        await self._store_post(source, event.messages)

    async def _store_post(self, source: Source, messages: list) -> None:
        text = next((m.message for m in messages if m.message), "")
        detected = detect_language(text)

        media_paths: list[str] = []
        post_dir = Path(settings.media_storage_path) / f"src{source.id}_{messages[0].id}"
        for msg in messages:
            if msg.media:
                post_dir.mkdir(parents=True, exist_ok=True)
                path = await self.client.download_media(msg, file=f"{post_dir}/")
                if not path:
                    continue
                if self.bot and settings.storage_chat_id:
                    try:
                        ref = await upload_and_get_ref(self.bot, settings.storage_chat_id, path)
                        media_paths.append(ref)
                    except Exception:
                        logger.exception(
                            "Источник %s: не удалось передать медиа %s через Bot API, пост будет без него",
                            source.username,
                            path,
                        )
                    finally:
                        Path(path).unlink(missing_ok=True)
                else:
                    # Нет бота/служебного чата — старое поведение (путь на локальном
                    # диске юзербота; не сработает, если bot/publish работают в другом сервисе).
                    media_paths.append(str(path))
        try:
            if post_dir.exists() and not any(post_dir.iterdir()):
                post_dir.rmdir()
        except OSError:
            pass

        async with session_scope() as session:
            existing = await session.execute(
                select(RawPost.id).where(
                    RawPost.source_id == source.id,
                    RawPost.tg_message_id == messages[0].id,
                )
            )
            if existing.scalars().first() is not None:
                logger.info(
                    "Источник %s: сообщение %s уже собрано ранее, пропускаю дубликат",
                    source.username,
                    messages[0].id,
                )
                return

            raw_post = RawPost(
                source_id=source.id,
                tg_message_id=messages[0].id,
                text=text,
                detected_lang=detected,
                media_paths=media_paths,
                status="pending",
            )
            session.add(raw_post)

        logger.info(
            "Источник %s: собран новый пост (язык=%s, медиафайлов=%d)",
            source.username,
            detected,
            len(media_paths),
        )


async def run_account(account_id: int) -> None:
    worker = AccountWorker(account_id)
    await worker.start()
