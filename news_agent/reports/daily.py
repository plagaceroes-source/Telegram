"""Ежедневный отчёт по каналу (news_agent/reports/) — только PDF (таблицы + диаграммы)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import fmt_num
from news_agent.reports.pdf import render_daily_pdf
from news_agent.reports.period import REPORT_TZ, day_bounds_utc

logger = logging.getLogger(__name__)


async def _gather_daily_data(session, day: dt.date) -> dict:
    since, until = day_bounds_utc(day)
    target_ids = await data.active_target_ids(session)

    posts_count = await data.published_posts_count(session, target_ids, since, until)
    start_subs = await data.subscriber_count_at(session, target_ids, since)
    end_subs = await data.subscriber_count_at(session, target_ids, until)
    joins, leaves, joins_by_source = await data.subscriber_flow(session, target_ids, since, until)
    posts = await data.posts_with_stats(session, target_ids, since, until)

    return {
        "posts_count": posts_count,
        "start_subs": start_subs,
        "end_subs": end_subs,
        "joins": joins,
        "leaves": leaves,
        "joins_by_source": joins_by_source,
        "posts": posts,
    }


async def send_daily_report(bot: Bot, day: dt.date) -> None:
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        d = await _gather_daily_data(session, day)

    delta = d["end_subs"] - d["start_subs"]
    caption = (
        f"📊 Ежедневный отчёт — {day:%d.%m.%Y}\n"
        f"Постов: {fmt_num(d['posts_count'])} · Подписчики: {fmt_num(d['end_subs'])} "
        f"({'+' if delta >= 0 else '−'}{fmt_num(abs(delta))})"
    )

    pdf_bytes = render_daily_pdf(day, d, REPORT_TZ)
    await bot.send_document(
        settings.reports_chat_id,
        BufferedInputFile(pdf_bytes, filename=f"report_{day:%Y-%m-%d}.pdf"),
        caption=caption,
    )
    logger.info("Ежедневный отчёт за %s отправлен", day)
