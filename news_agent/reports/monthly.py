"""Месячный отчёт по каналу со сравнением с предыдущим месяцем (news_agent/reports/) —
только PDF (таблицы + диаграммы)."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import fmt_num
from news_agent.reports.pdf import render_monthly_pdf
from news_agent.reports.period import REPORT_TZ, incomplete_data_note, month_bounds_utc, previous_month

logger = logging.getLogger(__name__)

MONTH_NAMES_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

TOP_POSTS_LIMIT = 10


async def _month_metrics(session, target_ids: list[int], year: int, month: int) -> dict:
    since, until = month_bounds_utc(year, month)
    return await data.period_metrics(session, target_ids, since, until)


async def _gather_monthly_data(session, year: int, month: int) -> dict:
    target_ids = await data.active_target_ids(session)
    cur = await _month_metrics(session, target_ids, year, month)
    prev_year, prev_month = previous_month(year, month)
    prev = await _month_metrics(session, target_ids, prev_year, prev_month)

    since, until = month_bounds_utc(year, month)
    top_posts = await data.posts_with_stats(session, target_ids, since, until)

    return {
        "cur": cur,
        "prev": prev,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "top_posts": top_posts[:TOP_POSTS_LIMIT],
        "data_note": incomplete_data_note(await data.last_posts_sync(session, target_ids), until),
    }


async def send_monthly_report(bot: Bot, year: int, month: int) -> None:
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        g = await _gather_monthly_data(session, year, month)

    cur = g["cur"]
    month_name = MONTH_NAMES_RU[month]
    prev_month_name = MONTH_NAMES_RU[g["prev_month"]]
    caption = (
        f"🗓 Месячный отчёт — {month_name} {year} (сравнение с {prev_month_name} {g['prev_year']})\n"
        f"Постов: {fmt_num(cur['posts_count'])} · Подписчики: {fmt_num(cur['end_subs'])} "
        f"({'+' if cur['growth'] >= 0 else '−'}{fmt_num(abs(cur['growth']))})"
    )
    if g["data_note"]:
        caption += f"\n{g['data_note']}"

    pdf_bytes = render_monthly_pdf(
        year, month, month_name, prev_month_name, g["prev_year"], cur, g["prev"], g["top_posts"], REPORT_TZ
    )
    await bot.send_document(
        settings.reports_chat_id,
        BufferedInputFile(pdf_bytes, filename=f"report_{year:04d}-{month:02d}.pdf"),
        caption=caption,
    )
    logger.info("Месячный отчёт за %04d-%02d отправлен", year, month)
