"""Отчёт «с начала месяца по сегодня» со сравнением с прошлым месяцем к тому же моменту
(news_agent/reports/): сегодня 25-е — результаты с 1-го по 25-е и динамика относительно
1–25 числа прошлого месяца. Формируется по запросу (/report_mtd), только PDF."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from news_agent.config import settings
from news_agent.db.session import session_scope
from news_agent.reports import data
from news_agent.reports.format import fmt_num, fmt_signed
from news_agent.reports.monthly import MONTH_NAMES_RU
from news_agent.reports.pdf import render_mtd_pdf
from news_agent.reports.period import REPORT_TZ, incomplete_data_note, month_to_date_bounds
from news_agent.stats.snapshot import take_snapshot

logger = logging.getLogger(__name__)

TOP_POSTS_LIMIT = 10


async def _gather_mtd_data(session, b: dict) -> dict:
    target_ids = await data.active_target_ids(session)
    cur = await data.period_metrics(session, target_ids, b["cur_since"], b["cur_until"])
    prev = await data.period_metrics(session, target_ids, b["prev_since"], b["prev_until"])
    cur_days = [dt.date(b["year"], b["month"], d) for d in range(1, b["day"] + 1)]
    prev_days = [dt.date(b["prev_year"], b["prev_month"], d) for d in range(1, b["prev_last_day"] + 1)]
    cur_series = await data.daily_series(session, target_ids, cur_days, b["cur_since"], b["cur_until"], REPORT_TZ)
    prev_series = await data.daily_series(session, target_ids, prev_days, b["prev_since"], b["prev_until"], REPORT_TZ)
    top_posts = (await data.posts_with_stats(session, target_ids, b["cur_since"], b["cur_until"]))[:TOP_POSTS_LIMIT]
    # Юзербот копирует посты раз в poll_interval секунд: отставание в пределах интервала — норма.
    tolerated = b["cur_until"] - dt.timedelta(seconds=settings.post_stats_poll_interval * 1.5)
    return {
        "cur": cur,
        "prev": prev,
        "cur_series": cur_series,
        "prev_series": prev_series,
        "top_posts": top_posts,
        "data_note": incomplete_data_note(await data.last_posts_sync(session, target_ids), tolerated),
    }


async def send_month_to_date_report(bot: Bot, now: dt.datetime | None = None) -> None:
    if not settings.reports_chat_id:
        return
    now_local = (now or dt.datetime.now(REPORT_TZ)).astimezone(REPORT_TZ)
    b = month_to_date_bounds(now_local)
    try:
        # Свежее число подписчиков «на сейчас», а не часовой давности.
        await take_snapshot(bot)
    except Exception:
        logger.exception("Не удалось снять снимок подписчиков перед отчётом с начала месяца")

    async with session_scope() as session:
        g = await _gather_mtd_data(session, b)

    cur, prev = g["cur"], g["prev"]
    cur_name, prev_name = MONTH_NAMES_RU[b["month"]], MONTH_NAMES_RU[b["prev_month"]]
    growth = fmt_signed(cur["growth"]) if cur["start_known"] else "н/д"
    prev_growth = fmt_signed(prev["growth"]) if prev["start_known"] else "н/д"
    caption = (
        f"📅 С начала месяца — 1–{b['day']} {cur_name} (на {now_local:%d.%m %H:%M}), "
        f"сравнение с 1–{b['prev_last_day']} {prev_name}\n"
        f"Постов: {fmt_num(cur['posts_count'])} (было {fmt_num(prev['posts_count'])}) · "
        f"Подписчики: {fmt_num(cur['end_subs'])}, за период {growth} (было {prev_growth})"
    )
    if g["data_note"]:
        caption += f"\n{g['data_note']}"

    pdf_bytes = render_mtd_pdf(
        b, cur, prev, g["cur_series"], g["prev_series"], g["top_posts"], REPORT_TZ, MONTH_NAMES_RU, now_local
    )
    await bot.send_document(
        settings.reports_chat_id,
        BufferedInputFile(pdf_bytes, filename=f"report_mtd_{now_local:%Y-%m-%d}.pdf"),
        caption=caption,
    )
    logger.info("Отчёт с начала месяца на %s отправлен", now_local)
