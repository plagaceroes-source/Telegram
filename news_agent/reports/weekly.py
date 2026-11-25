"""Недельный отчёт (понедельник–воскресенье) с динамикой по дням и сравнением с предыдущей
неделей (news_agent/reports/) — только PDF (таблицы + диаграммы)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import fmt_num, fmt_signed
from news_agent.reports.pdf import render_weekly_pdf
from news_agent.reports.period import REPORT_TZ, incomplete_data_note, week_bounds_utc

logger = logging.getLogger(__name__)

TOP_POSTS_LIMIT = 10


async def _gather_weekly_data(session, monday: dt.date) -> dict:
    target_ids = await data.active_target_ids(session)
    since, until = week_bounds_utc(monday)
    prev_monday = monday - dt.timedelta(days=7)
    prev_since, prev_until = week_bounds_utc(prev_monday)

    cur = await data.period_metrics(session, target_ids, since, until)
    prev = await data.period_metrics(session, target_ids, prev_since, prev_until)
    cur_series = await data.daily_series(
        session, target_ids, [monday + dt.timedelta(days=i) for i in range(7)], since, until, REPORT_TZ
    )
    prev_series = await data.daily_series(
        session, target_ids, [prev_monday + dt.timedelta(days=i) for i in range(7)], prev_since, prev_until, REPORT_TZ
    )
    top_posts = (await data.posts_with_stats(session, target_ids, since, until))[:TOP_POSTS_LIMIT]
    return {
        "cur": cur,
        "prev": prev,
        "cur_series": cur_series,
        "prev_series": prev_series,
        "top_posts": top_posts,
        "data_note": incomplete_data_note(await data.last_posts_sync(session, target_ids), until),
    }


async def send_weekly_report(bot: Bot, monday: dt.date) -> None:
    """Отчёт за неделю, которая начинается в monday (понедельник), и сравнение с неделей до неё."""
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        g = await _gather_weekly_data(session, monday)

    cur, prev = g["cur"], g["prev"]
    sunday = monday + dt.timedelta(days=6)
    prev_monday = monday - dt.timedelta(days=7)
    growth = fmt_signed(cur["growth"]) if cur["start_known"] else "н/д"
    prev_growth = fmt_signed(prev["growth"]) if prev["start_known"] else "н/д"
    caption = (
        f"📈 Недельный отчёт — {monday:%d.%m}–{sunday:%d.%m.%Y} "
        f"(сравнение с {prev_monday:%d.%m}–{prev_monday + dt.timedelta(days=6):%d.%m})\n"
        f"Постов: {fmt_num(cur['posts_count'])} (было {fmt_num(prev['posts_count'])}) · "
        f"Подписчики: {fmt_num(cur['end_subs'])}, за неделю {growth} (было {prev_growth})"
    )
    if g["data_note"]:
        caption += f"\n{g['data_note']}"

    pdf_bytes = render_weekly_pdf(monday, cur, prev, g["cur_series"], g["prev_series"], g["top_posts"], REPORT_TZ)
    await bot.send_document(
        settings.reports_chat_id,
        BufferedInputFile(pdf_bytes, filename=f"report_week_{monday:%Y-%m-%d}.pdf"),
        caption=caption,
    )
    logger.info("Недельный отчёт за %s–%s отправлен", monday, sunday)
