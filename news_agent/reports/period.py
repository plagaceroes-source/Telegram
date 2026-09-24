"""Календарные границы периода отчёта в часовом поясе news_agent (Europe/Madrid
по умолчанию) — конвертируются в UTC-datetime для запросов к БД."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from news_agent.config import settings

REPORT_TZ = ZoneInfo(settings.reports_timezone)


def day_bounds_utc(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start_local = dt.datetime(day.year, day.month, day.day, tzinfo=REPORT_TZ)
    end_local = start_local + dt.timedelta(days=1)
    return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


def month_bounds_utc(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
    start_local = dt.datetime(year, month, 1, tzinfo=REPORT_TZ)
    if month == 12:
        end_local = dt.datetime(year + 1, 1, 1, tzinfo=REPORT_TZ)
    else:
        end_local = dt.datetime(year, month + 1, 1, tzinfo=REPORT_TZ)
    return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def is_last_day_of_month(day: dt.date) -> bool:
    return (day + dt.timedelta(days=1)).month != day.month
