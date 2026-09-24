"""Месячный отчёт по каналу со сравнением с предыдущим месяцем (news_agent/reports/)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import chunk_message, fmt_num, fmt_pct
from news_agent.reports.period import month_bounds_utc, previous_month

logger = logging.getLogger(__name__)

MONTH_NAMES_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

TOP_POSTS_LIMIT = 10


async def _month_metrics(session, target_ids: list[int], year: int, month: int) -> dict:
    since, until = month_bounds_utc(year, month)
    posts_count = await data.published_posts_count(session, target_ids, since, until)
    start_subs = await data.subscriber_count_at(session, target_ids, since)
    end_subs = await data.subscriber_count_at(session, target_ids, until)
    joins, leaves, joins_by_source = await data.subscriber_flow(session, target_ids, since, until)
    totals = await data.posts_totals(session, target_ids, since, until)
    return {
        "posts_count": posts_count,
        "start_subs": start_subs,
        "end_subs": end_subs,
        "growth": end_subs - start_subs,
        "joins": joins,
        "leaves": leaves,
        "joins_by_source": joins_by_source,
        "totals": totals,
    }


async def build_monthly_report(session, year: int, month: int) -> list[str]:
    target_ids = await data.active_target_ids(session)
    cur = await _month_metrics(session, target_ids, year, month)
    prev_year, prev_month = previous_month(year, month)
    prev = await _month_metrics(session, target_ids, prev_year, prev_month)

    since, until = month_bounds_utc(year, month)
    top_posts = await data.posts_with_stats(session, target_ids, since, until)

    month_name = MONTH_NAMES_RU[month]
    prev_month_name = MONTH_NAMES_RU[prev_month]

    lines = [
        f"🗓 Месячный отчёт — {month_name} {year}",
        f"Сравнение с {prev_month_name} {prev_year}",
        "",
        f"Опубликовано постов: {fmt_num(cur['posts_count'])} "
        f"({fmt_pct(cur['posts_count'], prev['posts_count'])})",
        "",
        f"Подписчиков на конец месяца: {fmt_num(cur['end_subs'])} "
        f"(было {fmt_num(cur['start_subs'])} на начало месяца)",
        f"Прирост за месяц: {'+' if cur['growth'] >= 0 else '−'}{fmt_num(abs(cur['growth']))} "
        f"(в {prev_month_name}: {'+' if prev['growth'] >= 0 else '−'}{fmt_num(abs(prev['growth']))})",
        f"Подписалось: {fmt_num(cur['joins'])} ({fmt_pct(cur['joins'], prev['joins'])})",
        f"Отписалось: {fmt_num(cur['leaves'])} ({fmt_pct(cur['leaves'], prev['leaves'])})",
    ]

    lines.append("")
    lines.append("Источники подписок за месяц:")
    if cur["joins_by_source"]:
        for label, count in sorted(cur["joins_by_source"].items(), key=lambda kv: kv[1], reverse=True):
            prev_count = prev["joins_by_source"].get(label, 0)
            lines.append(f"• {label}: {fmt_num(count)} ({fmt_pct(count, prev_count)})")
    else:
        lines.append("— подписок не было")

    lines.append("")
    lines.append("Метрики постов за месяц (суммарно):")
    lines.append(f"👁 Просмотры: {fmt_num(cur['totals']['views'])} ({fmt_pct(cur['totals']['views'], prev['totals']['views'])})")
    lines.append(f"🔁 Репосты: {fmt_num(cur['totals']['forwards'])} ({fmt_pct(cur['totals']['forwards'], prev['totals']['forwards'])})")
    lines.append(f"❤️ Реакции: {fmt_num(cur['totals']['reactions'])} ({fmt_pct(cur['totals']['reactions'], prev['totals']['reactions'])})")
    lines.append(f"💬 Комментарии: {fmt_num(cur['totals']['comments'])} ({fmt_pct(cur['totals']['comments'], prev['totals']['comments'])})")

    lines.append("")
    lines.append(f"Топ-{TOP_POSTS_LIMIT} постов месяца по просмотрам:")
    if top_posts:
        for p in top_posts[:TOP_POSTS_LIMIT]:
            lines.append(
                f"• {p['published_at']:%d.%m} @{p['channel_username']} — "
                f"👁 {fmt_num(p['views'])} · 🔁 {fmt_num(p['forwards'])} · "
                f"❤️ {fmt_num(p['reactions'])} · 💬 {fmt_num(p['comments'])} — {p['snippet']}"
            )
    else:
        lines.append("— постов не было")

    return chunk_message(lines)


async def send_monthly_report(bot: Bot, year: int, month: int) -> None:
    if not settings.reports_chat_id:
        return
    async with session_scope() as session:
        messages = await build_monthly_report(session, year, month)
    for text in messages:
        await bot.send_message(settings.reports_chat_id, text)
    logger.info("Месячный отчёт за %04d-%02d отправлен", year, month)
