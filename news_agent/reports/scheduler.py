"""Планировщик ежедневных/месячных отчётов (news_agent/reports/).

Запускается внутри уже работающего процесса approval-бота (news_agent/bot/approval.py),
использует APScheduler с часовым поясом отчёта — учитывает переход на летнее/зимнее
время сам, в отличие от статичного cron-расписания в UTC."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from news_agent.config import settings
from news_agent.db.models import ReportLog
from news_agent.db.session import session_scope
from news_agent.reports.daily import send_daily_report
from news_agent.reports.monthly import send_monthly_report
from news_agent.reports.period import REPORT_TZ, previous_month

logger = logging.getLogger(__name__)

# Отчёт уходит сразу после полуночи за ПРОШЕДШИЕ сутки: юзербот копирует посты канала в БД
# раз в 15 минут, и в 23:55 пост, вышедший в последние минуты суток, в отчёт ещё не попадал.
REPORT_HOUR = 0
REPORT_MINUTE = 20


async def _already_sent(kind: str, period_key: str) -> bool:
    async with session_scope() as session:
        existing = await session.execute(
            select(ReportLog.id).where(ReportLog.kind == kind, ReportLog.period_key == period_key)
        )
        return existing.scalars().first() is not None


async def _mark_sent(kind: str, period_key: str) -> None:
    async with session_scope() as session:
        session.add(ReportLog(kind=kind, period_key=period_key))


async def _run_daily(bot: Bot) -> None:
    day = dt.datetime.now(REPORT_TZ).date() - dt.timedelta(days=1)
    period_key = day.isoformat()
    if await _already_sent("daily", period_key):
        return
    try:
        await send_daily_report(bot, day)
    except Exception:
        logger.exception("Не удалось отправить ежедневный отчёт за %s", period_key)
        return
    await _mark_sent("daily", period_key)


async def _run_monthly(bot: Bot) -> None:
    today = dt.datetime.now(REPORT_TZ).date()
    year, month = previous_month(today.year, today.month)
    period_key = f"{year:04d}-{month:02d}"
    if await _already_sent("monthly", period_key):
        return
    try:
        await send_monthly_report(bot, year, month)
    except Exception:
        logger.exception("Не удалось отправить месячный отчёт за %s", period_key)
        return
    await _mark_sent("monthly", period_key)


def start_reports_scheduler(bot: Bot) -> AsyncIOScheduler | None:
    if not settings.reports_chat_id:
        logger.info("REPORTS_CHAT_ID не задан — отчёты по каналу отключены")
        return None

    scheduler = AsyncIOScheduler(timezone=REPORT_TZ)
    scheduler.add_job(
        _run_daily,
        CronTrigger(hour=REPORT_HOUR, minute=REPORT_MINUTE, timezone=REPORT_TZ),
        args=[bot],
        id="daily_report",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _run_monthly,
        CronTrigger(day=1, hour=REPORT_HOUR, minute=REPORT_MINUTE, timezone=REPORT_TZ),
        args=[bot],
        id="monthly_report",
        misfire_grace_time=600,
    )
    scheduler.start()
    logger.info(
        "Планировщик отчётов запущен: ежедневно в %02d:%02d (%s) за прошедшие сутки, 1-го числа — за прошлый месяц",
        REPORT_HOUR,
        REPORT_MINUTE,
        settings.reports_timezone,
    )
    return scheduler
