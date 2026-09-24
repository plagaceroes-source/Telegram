"""Ежедневный отчёт по каналу (news_agent/reports/)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import chunk_message, fmt_num
from news_agent.reports.period import REPORT_TZ, day_bounds_utc

logger = logging.getLogger(__name__)


async def build_daily_report(session, day: dt.date) -> list[str]:
    since, until = day_bounds_utc(day)
    target_ids = await data.active_target_ids(session)

    posts_count = await data.published_posts_count(session, target_ids, since, until)
    start_subs = await data.subscriber_count_at(session, target_ids, since)
    end_subs = await data.subscriber_count_at(session, target_ids, until)
    joins, leaves, joins_by_source = await data.subscriber_flow(session, target_ids, since, until)
    posts = await data.posts_with_stats(session, target_ids, since, until)

    delta = end_subs - start_subs
    lines = [
        f"📊 Ежедневный отчёт — {day:%d.%m.%Y}",
        "",
        f"Опубликовано постов: {fmt_num(posts_count)}",
        f"Подписчиков: {fmt_num(end_subs)} (было {fmt_num(start_subs)}, {'+' if delta >= 0 else '−'}{fmt_num(abs(delta))})",
        f"Подписалось: {fmt_num(joins)}",
        f"Отписалось: {fmt_num(leaves)}",
    ]

    lines.append("")
    lines.append("Источники подписок:")
    if joins_by_source:
        for label, count in sorted(joins_by_source.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"• {label}: {fmt_num(count)}")
    else:
        lines.append("— подписок не было")

    lines.append("")
    lines.append(f"Посты за день ({len(posts)}):")
    if posts:
        for p in posts:
            local_time = p["published_at"].astimezone(REPORT_TZ)
            lines.append(
                f"• [{local_time:%H:%M}] @{p['channel_username']} — "
                f"👁 {fmt_num(p['views'])} · 🔁 {fmt_num(p['forwards'])} · "
                f"❤️ {fmt_num(p['reactions'])} · 💬 {fmt_num(p['comments'])} — {p['snippet']}"
            )
    else:
        lines.append("— постов не было")

    return chunk_message(lines)


async def send_daily_report(bot: Bot, day: dt.date) -> None:
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        messages = await build_daily_report(session, day)
    for text in messages:
        await bot.send_message(settings.reports_chat_id, text)
    logger.info("Ежедневный отчёт за %s отправлен", day)
