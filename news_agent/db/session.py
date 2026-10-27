"""Async SQLAlchemy engine/session setup."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from news_agent.config import settings
from news_agent.db.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


# create_all не меняет типы уже существующих колонок. Эти колонки когда-то создали как
# INTEGER, а id пользователей Telegram давно больше int32 — расширяем до BIGINT.
_WIDEN_TO_BIGINT = [("subscriber_events", "tg_user_id")]


async def _widen_columns(conn) -> None:
    if conn.dialect.name != "postgresql":
        return
    for table, column in _WIDEN_TO_BIGINT:
        data_type = await conn.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
        if data_type == "integer":
            await conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE BIGINT'))
            logger.info("Колонка %s.%s расширена до BIGINT", table, column)


# Новые колонки в уже существующих таблицах: create_all их не добавляет.
_ADD_COLUMNS = [("link_clicks", "source", "VARCHAR(8) NOT NULL DEFAULT 'link'")]


async def _add_missing_columns(conn) -> None:
    if conn.dialect.name != "postgresql":
        return  # в SQLite (тесты/локально) таблицы создаются с нуля сразу с нужными колонками
    for table, column, ddl in _ADD_COLUMNS:
        await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}'))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with engine.begin() as conn:
            await _add_missing_columns(conn)
    except Exception:
        logger.exception("Не удалось добавить недостающие колонки")
    try:
        async with engine.begin() as conn:
            await _widen_columns(conn)
    except Exception:
        # Не валим запуск сервиса: максимум — старые типы колонок остаются как были.
        logger.exception("Не удалось расширить типы колонок до BIGINT")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
