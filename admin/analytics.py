"""Вкладка «Аналитика» дашборда: расчётные метрики по постам (охват, ERR, ER, лучшее время)
и подготовка официальной аналитики Telegram (ChannelAnalytics) к выводу."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select

from news_agent.db.models import ChannelAnalytics, ChannelPost, TargetChannel
from news_agent.reports.period import REPORT_TZ

AN_PERIODS = {"7": 7, "30": 30, "90": 90, "all": None}
MATURE_AFTER = dt.timedelta(hours=24)  # свежие посты ещё набирают просмотры — в «лучшее время» не берём
MIN_POSTS_FOR_BEST = 3
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

SOURCE_NAMES = {
    "Followers": "Подписчики",
    "Groups": "Группы",
    "Other": "Другое",
    "PM": "Личные сообщения",
    "URL": "Внешние ссылки",
    "Search": "Поиск",
    "Channels": "Каналы",
}
LANGUAGE_NAMES = {
    "Russian": "Русский", "Ukrainian": "Украинский", "English": "Английский", "Spanish": "Испанский",
    "German": "Немецкий", "French": "Французский", "Swedish": "Шведский", "Italian": "Итальянский",
    "Portuguese": "Португальский", "Polish": "Польский", "Arabic": "Арабский", "Turkish": "Турецкий",
    "Dutch": "Нидерландский", "Other": "Другие",
}


def _aware(d: dt.datetime) -> dt.datetime:
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _pct(num: float, den: float) -> float | None:
    return round(num / den * 100, 1) if den else None


def _hour_buckets(posts: list[ChannelPost], now: dt.datetime) -> tuple[list[dict], list[dict]]:
    by_hour: dict[int, list[int]] = defaultdict(list)
    by_wday: dict[int, list[int]] = defaultdict(list)
    for p in posts:
        posted = _aware(p.posted_at)
        if now - posted < MATURE_AFTER:
            continue
        local = posted.astimezone(REPORT_TZ)
        by_hour[local.hour].append(p.views)
        by_wday[local.weekday()].append(p.views)

    def pack(source: dict[int, list[int]], keys: range) -> list[dict]:
        return [
            {
                "key": k,
                "posts": len(source.get(k, [])),
                "avg_views": round(sum(source[k]) / len(source[k])) if source.get(k) else 0,
            }
            for k in keys
        ]

    hours, wdays = pack(by_hour, range(24)), pack(by_wday, range(7))
    for row in wdays:
        row["label"] = WEEKDAYS[row["key"]]
    for row in hours:
        row["label"] = f"{row['key']:02d}:00"
    return hours, wdays


def _best(rows: list[dict], top: int = 3) -> list[dict]:
    eligible = [r for r in rows if r["posts"] >= MIN_POSTS_FOR_BEST]
    return sorted(eligible, key=lambda r: r["avg_views"], reverse=True)[:top]


async def post_metrics(session, an_period: str, subscribers: int) -> dict:
    """Охват, ERR, ER и лучшее время публикации по зеркалу постов канала (ChannelPost)."""
    if an_period not in AN_PERIODS:
        an_period = "30"
    now = dt.datetime.now(dt.timezone.utc)
    days = AN_PERIODS[an_period]
    since = now - dt.timedelta(days=days) if days else None

    active_ids = list(
        (await session.execute(select(TargetChannel.id).where(TargetChannel.active.is_(True)))).scalars()
    )
    query = select(ChannelPost).where(ChannelPost.target_channel_id.in_(active_ids))
    if since:
        query = query.where(ChannelPost.posted_at >= since)
    posts = list((await session.execute(query)).scalars()) if active_ids else []

    views = sum(p.views for p in posts)
    interactions = sum(p.reactions_count + p.comments_count + p.forwards for p in posts)
    avg_reach = round(views / len(posts)) if posts else 0

    hours, wdays = _hour_buckets(posts, now)

    # Календарные счётчики публикаций (в часовом поясе отчётов), не зависят от выбранного периода.
    today_local = now.astimezone(REPORT_TZ).date()
    all_recent = list(
        (
            await session.execute(
                select(ChannelPost.posted_at).where(
                    ChannelPost.target_channel_id.in_(active_ids),
                    ChannelPost.posted_at >= now - dt.timedelta(days=45),
                )
            )
        ).scalars()
    ) if active_ids else []
    local_days = [_aware(d).astimezone(REPORT_TZ).date() for d in all_recent]
    published = {
        "today": sum(1 for d in local_days if d == today_local),
        "yesterday": sum(1 for d in local_days if d == today_local - dt.timedelta(days=1)),
        "week": sum(1 for d in local_days if d > today_local - dt.timedelta(days=7)),
        "month": sum(1 for d in local_days if d > today_local - dt.timedelta(days=30)),
    }
    per_day: dict[dt.date, int] = defaultdict(int)
    for d in local_days:
        per_day[d] += 1
    published_series = [
        {"date": (today_local - dt.timedelta(days=i)).strftime("%d.%m"),
         "count": per_day.get(today_local - dt.timedelta(days=i), 0)}
        for i in range(29, -1, -1)
    ]

    return {
        "period": an_period,
        "posts": len(posts),
        "views": views,
        "avg_reach": avg_reach,
        "err": _pct(avg_reach, subscribers),
        "er": _pct(interactions, views),
        "reactions": sum(p.reactions_count for p in posts),
        "comments": sum(p.comments_count for p in posts),
        "forwards": sum(p.forwards for p in posts),
        "hours": hours,
        "weekdays": wdays,
        "best_hours": _best(hours),
        "best_weekdays": _best(wdays, top=2),
        "published": published,
        "published_series": published_series,
    }


def _series_totals(graph: dict | None, names: dict[str, str]) -> list[dict]:
    if not graph:
        return []
    rows = [
        {"name": names.get(s["name"], s["name"]), "total": sum(v for v in s["values"] if isinstance(v, (int, float)))}
        for s in graph["series"]
    ]
    return sorted((r for r in rows if r["total"] > 0), key=lambda r: r["total"], reverse=True)


def _shift_hours_to_local(graph: dict) -> dict:
    """top_hours_graph: x — часы 0..23 по UTC. Переводим в часовой пояс отчётов (по текущему смещению)."""
    offset_h = int((dt.datetime.now(REPORT_TZ).utcoffset() or dt.timedelta()).total_seconds() // 3600)
    series = graph["series"][0]["values"] if graph["series"] else []
    if len(series) != 24:
        return {"labels": [], "values": []}
    shifted = [series[(h - offset_h) % 24] for h in range(24)]
    return {"labels": [f"{h:02d}:00" for h in range(24)], "values": shifted}


def _day_labels(graph: dict) -> list[str]:
    return [
        dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%d.%m")
        for ms in graph["x"]
    ]


async def telegram_analytics(session) -> dict | None:
    """Официальная аналитика Telegram по первому активному каналу, готовая к выводу."""
    row = await session.scalar(
        select(ChannelAnalytics)
        .join(TargetChannel, TargetChannel.id == ChannelAnalytics.target_channel_id)
        .where(TargetChannel.active.is_(True))
        .order_by(ChannelAnalytics.target_channel_id)
        .limit(1)
    )
    if row is None or not row.data:
        return None
    data = row.data
    graphs = data.get("graphs", {})
    followers = data.get("followers") or {}
    notif = data.get("enabled_notifications") or {}

    out: dict = {
        "fetched_at": row.fetched_at,
        "period_from": data["period"]["min_date"][:10],
        "period_to": data["period"]["max_date"][:10],
        "followers": followers.get("current"),
        "followers_prev": followers.get("previous"),
        "views_per_post": (data.get("views_per_post") or {}).get("current"),
        "shares_per_post": (data.get("shares_per_post") or {}).get("current"),
        "reactions_per_post": (data.get("reactions_per_post") or {}).get("current"),
        "notifications_pct": _pct(notif.get("part", 0), notif.get("total", 0)),
    }

    flow = graphs.get("followers_graph")
    if flow:
        by_name = {s["name"]: s["values"] for s in flow["series"]}
        joined, left = by_name.get("Joined", []), by_name.get("Left", [])
        out["flow"] = {"labels": _day_labels(flow), "joined": joined, "left": left}
        out["joined_total"], out["left_total"] = sum(joined), sum(left)

    sources = graphs.get("new_followers_by_source_graph")
    if sources:
        out["new_sources"] = {
            "labels": _day_labels(sources),
            "series": [
                {"name": SOURCE_NAMES.get(s["name"], s["name"]), "values": s["values"]} for s in sources["series"]
            ],
        }
        totals = _series_totals(sources, SOURCE_NAMES)
        attributed = sum(r["total"] for r in totals)
        out["new_source_totals"] = totals
        out["unattributed"] = max(out.get("joined_total", 0) - attributed, 0) if "joined_total" in out else None

    out["view_sources"] = _series_totals(graphs.get("views_by_source_graph"), SOURCE_NAMES)
    out["languages"] = _series_totals(graphs.get("languages_graph"), LANGUAGE_NAMES)

    hours = graphs.get("top_hours_graph")
    if hours:
        out["top_hours"] = _shift_hours_to_local(hours)

    inter = graphs.get("interactions_graph")
    if inter:
        out["interactions"] = {
            "labels": _day_labels(inter),
            "series": [{"name": {"Views": "Просмотры", "Shares": "Репосты"}.get(s["name"], s["name"]),
                        "values": s["values"]} for s in inter["series"]],
        }
    out["charts"] = {
        k: out[k]
        for k in ("flow", "new_sources", "view_sources", "languages", "top_hours", "interactions")
        if k in out
    }
    return out
