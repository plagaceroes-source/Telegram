"""Ежедневный отчёт по каналу (news_agent/reports/)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import chunk_message, fmt_num
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


def _render_daily_text(day: dt.date, d: dict) -> list[str]:
    delta = d["end_subs"] - d["start_subs"]
    lines = [
        f"📊 Ежедневный отчёт — {day:%d.%m.%Y}",
        "",
        f"Опубликовано постов: {fmt_num(d['posts_count'])}",
        f"Подписчиков: {fmt_num(d['end_subs'])} (было {fmt_num(d['start_subs'])}, "
        f"{'+' if delta >= 0 else '−'}{fmt_num(abs(delta))})",
        f"Подписалось: {fmt_num(d['joins'])}",
        f"Отписалось: {fmt_num(d['leaves'])}",
    ]

    lines.append("")
    lines.append("Источники подписок:")
    if d["joins_by_source"]:
        for label, count in sorted(d["joins_by_source"].items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"• {label}: {fmt_num(count)}")
    else:
        lines.append("— подписок не было")

    lines.append("")
    lines.append(f"Посты за день ({len(d['posts'])}):")
    if d["posts"]:
        for p in d["posts"]:
            local_time = p["published_at"].astimezone(REPORT_TZ)
            lines.append(
                f"• [{local_time:%H:%M}] @{p['channel_username']} — "
                f"👁 {fmt_num(p['views'])} · 🔁 {fmt_num(p['forwards'])} · "
                f"❤️ {fmt_num(p['reactions'])} · 💬 {fmt_num(p['comments'])} — {p['snippet']}"
            )
    else:
        lines.append("— постов не было")

    return chunk_message(lines)


async def build_daily_report(session, day: dt.date) -> list[str]:
    d = await _gather_daily_data(session, day)
    return _render_daily_text(day, d)


async def send_daily_report(bot: Bot, day: dt.date) -> None:
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        d = await _gather_daily_data(session, day)

    for text in _render_daily_text(day, d):
        await bot.send_message(settings.reports_chat_id, text)

    pdf_bytes = render_daily_pdf(day, d, REPORT_TZ)
    await bot.send_document(
        settings.reports_chat_id,
        BufferedInputFile(pdf_bytes, filename=f"report_{day:%Y-%m-%d}.pdf"),
        caption=f"PDF-версия отчёта за {day:%d.%m.%Y} с таблицами и диаграммами",
    )
    logger.info("Ежедневный отчёт за %s отправлен", day)
