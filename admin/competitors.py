"""Вкладка «Конкуренты»: сравнение нашего канала с чужими по подписчикам, приросту,
активности и охвату."""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from sqlalchemy import select

from news_agent.db.models import ChannelStatsDaily, Competitor, CompetitorSnapshot, TargetChannel

MAX_COMPETITORS = 30
CHART_DAYS = 30
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def parse_username(raw: str) -> str | None:
    """@name, t.me/name, https://t.me/name → name (без закрытых +ссылок и посторонних URL)."""
    value = (raw or "").strip()
    value = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", value, flags=re.I)
    value = value.split("?")[0].split("/")[0].lstrip("@").strip()
    return value if _USERNAME_RE.match(value) else None


def growth(points: list[tuple[dt.date, int]], days: int, today: dt.date) -> tuple[int, int, int] | None:
    """Прирост за days дней: (разница, фактически охваченных дней, days). Базой служит последняя точка
    не позже today-days, а если истории ещё мало — самая ранняя (тогда охваченных дней меньше)."""
    if len(points) < 2:
        return None
    points = sorted(points)
    cutoff = today - dt.timedelta(days=days)
    base = next((p for p in reversed(points) if p[0] <= cutoff), points[0])
    last = points[-1]
    span = (last[0] - base[0]).days
    if span <= 0:
        return None
    return last[1] - base[1], span, days


def _pct(num: float, den: float) -> float | None:
    return round(num / den * 100, 1) if den else None


async def competitors_view(session, our: dict) -> dict:
    """our: {"subscribers", "avg_reach", "err", "posts_week"} — расчёт по нашему каналу."""
    today = dt.datetime.now(dt.timezone.utc).date()
    since_chart = today - dt.timedelta(days=CHART_DAYS)

    # Наш канал: суточные точки подписчиков (последнее значение за день, по всем активным каналам).
    active_ids = list(
        (await session.execute(select(TargetChannel.id).where(TargetChannel.active.is_(True)))).scalars()
    )
    our_points: dict[dt.date, dict[int, int]] = defaultdict(dict)
    if active_ids:
        rows = (
            await session.execute(
                select(ChannelStatsDaily)
                .where(ChannelStatsDaily.target_channel_id.in_(active_ids))
                .order_by(ChannelStatsDaily.snapshot_at)
            )
        ).scalars()
        for r in rows:
            snap_at = r.snapshot_at if r.snapshot_at.tzinfo else r.snapshot_at.replace(tzinfo=dt.timezone.utc)
            our_points[snap_at.date()][r.target_channel_id] = r.subscriber_count
    our_series = sorted((d, sum(v.values())) for d, v in our_points.items())

    competitors = list((await session.execute(select(Competitor).order_by(Competitor.id))).scalars())
    snaps_by_comp: dict[int, list[CompetitorSnapshot]] = defaultdict(list)
    for s in (
        await session.execute(select(CompetitorSnapshot).order_by(CompetitorSnapshot.snap_date))
    ).scalars():
        snaps_by_comp[s.competitor_id].append(s)

    def row_growth(series):
        return {
            "g7": growth(series, 7, today),
            "g30": growth(series, 30, today),
        }

    rows_out: list[dict] = [
        {
            "is_me": True,
            "username": None,
            "title": "Мой канал",
            "subscribers": our["subscribers"],
            "posts_week": our["posts_week"],
            "avg_reach": our["avg_reach"],
            "err": our["err"],
            "pending": False,
            "error": "",
            **row_growth(our_series),
        }
    ]
    chart_series = {"Мой канал": our_series}
    for c in competitors:
        snaps = snaps_by_comp.get(c.id, [])
        series = [(s.snap_date, s.subscribers) for s in snaps]
        last = snaps[-1] if snaps else None
        rows_out.append(
            {
                "id": c.id,
                "is_me": False,
                "username": c.username,
                "title": c.title or f"@{c.username}",
                "subscribers": last.subscribers if last else None,
                "posts_week": last.posts_7d if last else None,
                "avg_reach": last.avg_views_30d if last else None,
                "err": _pct(last.avg_views_30d, last.subscribers) if last else None,
                "pending": last is None and not c.last_error,
                "error": c.last_error,
                **row_growth(series),
            }
        )
        chart_series[c.title or f"@{c.username}"] = series

    rows_out.sort(key=lambda r: (r["subscribers"] is None, -(r["subscribers"] or 0)))

    # Динамика в % к началу окна — единственный способ честно сравнить каналы разного размера.
    labels = sorted({d for s in chart_series.values() for d, _ in s if d >= since_chart})
    datasets = []
    for name, series in chart_series.items():
        window = [(d, v) for d, v in series if d >= since_chart]
        if len(window) < 2 or not window[0][1]:
            continue
        base = window[0][1]
        by_date = {d: round((v / base - 1) * 100, 2) for d, v in window}
        datasets.append({"name": name, "values": [by_date.get(d) for d in labels], "me": name == "Мой канал"})

    return {
        "rows": rows_out,
        "count": len(competitors),
        "limit_reached": len(competitors) >= MAX_COMPETITORS,
        "chart": {"labels": [d.strftime("%d.%m") for d in labels], "datasets": datasets},
    }
