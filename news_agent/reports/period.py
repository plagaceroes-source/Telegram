"""Календарные границы периода отчёта в часовом поясе news_agent (Europe/Madrid
по умолчанию) — конвертируются в UTC-datetime для запросов к БД."""
from __future__ import annotations

import calendar
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


def incomplete_data_note(last_sync: dt.datetime | None, period_end: dt.datetime) -> str | None:
    """Пост попадает в отчёт только после того, как его скопирует в БД юзербот (раз в несколько
    минут). Если последний сбор был раньше конца периода, последние посты периода могли не
    попасть в отчёт — об этом нужно сказать, а не молча отдавать неполные цифры."""
    if last_sync is None:
        return "⚠ Данные по постам могут быть неполными: сбор постов канала ещё не выполнялся."
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=dt.timezone.utc)
    if last_sync >= period_end:
        return None
    return (
        "⚠ Данные по постам могут быть неполными: последний сбор постов канала был "
        f"{last_sync.astimezone(REPORT_TZ):%d.%m %H:%M}, раньше конца периода — "
        "последние посты могли не попасть в отчёт."
    )


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def is_last_day_of_month(day: dt.date) -> bool:
    return (day + dt.timedelta(days=1)).month != day.month


def week_monday(day: dt.date) -> dt.date:
    """Понедельник недели, в которую попадает день (неделя — с понедельника по воскресенье)."""
    return day - dt.timedelta(days=day.weekday())


def last_full_week_monday(today: dt.date) -> dt.date:
    """Понедельник последней полностью закончившейся недели."""
    return week_monday(today) - dt.timedelta(days=7)


def week_bounds_utc(monday: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """[понедельник 00:00, следующий понедельник 00:00) в часовом поясе отчёта, в UTC."""
    start_local = dt.datetime(monday.year, monday.month, monday.day, tzinfo=REPORT_TZ)
    end_day = monday + dt.timedelta(days=7)
    end_local = dt.datetime(end_day.year, end_day.month, end_day.day, tzinfo=REPORT_TZ)
    return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


def month_to_date_bounds(now_local: dt.datetime) -> dict:
    """Периоды для сравнения «с 1-го числа по сейчас» и «прошлый месяц к тому же моменту».

    Если сегодня 25-е, 14:30: текущий период — с 1-го числа 00:00 по сейчас, прошлый —
    с 1-го числа прошлого месяца по 25-е число 14:30 того же месяца. Если в прошлом месяце
    меньше дней (сегодня 31-е, а там 30), берём прошлый месяц целиком — capped=True."""
    year, month, day = now_local.year, now_local.month, now_local.day
    prev_year, prev_month = previous_month(year, month)
    prev_days = calendar.monthrange(prev_year, prev_month)[1]

    cur_since = dt.datetime(year, month, 1, tzinfo=REPORT_TZ)
    prev_since = dt.datetime(prev_year, prev_month, 1, tzinfo=REPORT_TZ)
    capped = day > prev_days
    if capped:
        prev_until = cur_since  # конец прошлого месяца
    else:
        prev_until = dt.datetime(
            prev_year, prev_month, day, now_local.hour, now_local.minute, now_local.second, tzinfo=REPORT_TZ
        )
    utc = dt.timezone.utc
    return {
        "year": year,
        "month": month,
        "day": day,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "prev_last_day": prev_days if capped else day,
        "capped": capped,
        "cur_since": cur_since.astimezone(utc),
        "cur_until": now_local.astimezone(utc),
        "prev_since": prev_since.astimezone(utc),
        "prev_until": prev_until.astimezone(utc),
    }
