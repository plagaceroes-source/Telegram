"""Блогеры: сколько «живой» аудитории привёл каждый и нет ли признаков накрутки.

Считаем по событиям вступления/выхода (SubscriberEvent) и публичным признакам аккаунта
в момент вступления (JoinProfile). Это косвенные признаки: система ничего не
дисквалифицирует сама, а только ставит флаги для проверки человеком. Накрутку
живыми людьми за вознаграждение так не поймать — против неё правило «засчитываем
подписчика, который остался на N дней» (BLOGGER_RETENTION_DAYS).

compute_report — чистая функция (без БД), load_report — загрузка данных и расчёт.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select

from news_agent.config import settings
from news_agent.db.models import (
    Blogger,
    BloggerLink,
    InviteLink,
    JoinProfile,
    LinkClick,
    SubscriberEvent,
)

QUICK_LEAVE_HOURS = 24
QUICK_LEAVE_MIN_RATE = 0.25  # ниже этой доли быстрых уходов флаг не ставим
QUICK_LEAVE_MARGIN = 0.15  # и она должна заметно превышать норму (базовую линию)
RETENTION_GAP = 0.2  # удержание хуже базовой линии на столько — флаг
RETENTION_FLOOR = 0.5  # если базовой линии нет: удержание ниже этого — флаг
FRESH_QUANTILE = 0.75  # «свежий» аккаунт = id в верхней четверти базовой линии
LANG_KNOWN_SHARE = 0.05  # язык «обычный», если у базовой линии его доля не меньше

WEIGHTS = {"retention": 0.5, "quick": 0.15, "profile": 0.2, "spike": 0.15}


@dataclass(frozen=True)
class Rules:
    retention_days: int = 7
    min_sample: int = 30
    spike_window_min: int = 10
    spike_min_joins: int = 15
    spike_day_share: float = 0.4
    profile_deviation: float = 0.3

    @classmethod
    def from_settings(cls) -> "Rules":
        return cls(
            retention_days=settings.blogger_retention_days,
            min_sample=settings.blogger_min_sample,
            spike_window_min=settings.blogger_spike_window_min,
            spike_min_joins=settings.blogger_spike_min_joins,
            spike_day_share=settings.blogger_spike_day_share,
            profile_deviation=settings.blogger_profile_deviation,
        )


@dataclass
class Join:
    user_id: int
    at: dt.datetime
    link_id: int | None
    left_at: dt.datetime | None = None
    has_username: bool | None = None  # None — профиля нет (вступил до включения сбора)
    is_premium: bool | None = None
    language: str | None = None


@dataclass
class LinkInfo:
    link_id: int
    platform: str
    name: str
    url: str
    revoked: bool = False


@dataclass
class BloggerInput:
    blogger_id: int
    name: str
    links: list[LinkInfo]
    joins: list[Join]
    active: bool = True


@dataclass
class Spike:
    start: dt.datetime
    count: int
    day_total: int
    quick_leave_rate: float | None = None  # доля ушедших за сутки среди пришедших во всплеске

    @property
    def share(self) -> float:
        return self.count / self.day_total if self.day_total else 0.0


@dataclass
class Signal:
    code: str
    text: str


@dataclass
class LinkStats:
    info: LinkInfo
    joins: int = 0
    leaves: int = 0
    active: int = 0
    clicks_unique: int = 0
    conversion: float | None = None  # подписки / уникальные переходы


@dataclass
class Metrics:
    n: int = 0
    left: int = 0
    mature: int = 0
    counted: int = 0  # дожили до retention_days
    pending: int = 0  # ещё не прошли срок, но пока в канале
    retention: float | None = None
    quick_leave_rate: float | None = None
    profile_n: int = 0
    nouser_share: float | None = None
    premium_share: float | None = None
    fresh_share: float | None = None
    foreign_share: float | None = None
    top_language: tuple[str, float] | None = None


@dataclass
class BloggerStats:
    blogger_id: int
    name: str
    active: bool
    links: list[LinkStats]
    metrics: Metrics
    spike: Spike | None
    signals: list[Signal]
    verdict: str  # ok / check / suspicious / insufficient
    score: int | None
    rank: int = 0

    @property
    def joins(self) -> int:
        return self.metrics.n

    @property
    def leaves(self) -> int:
        return self.metrics.left

    @property
    def active_now(self) -> int:
        return self.metrics.n - self.metrics.left


@dataclass
class Baseline:
    kind: str  # "direct" — вступления без ссылки, "pooled" — все блогеры вместе, "none"
    metrics: Metrics = field(default_factory=Metrics)
    fresh_threshold: int | None = None
    common_languages: frozenset[str] = frozenset()


@dataclass
class BloggersReport:
    bloggers: list[BloggerStats]
    baseline: Baseline
    rules: Rules


# ------------------------------------------------------------------ расчёт

def _aware(moment: dt.datetime) -> dt.datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def _metrics(joins: list[Join], now: dt.datetime, rules: Rules, fresh_threshold: int | None,
             common_languages: frozenset[str] | None) -> Metrics:
    m = Metrics(n=len(joins))
    if not joins:
        return m
    retention = dt.timedelta(days=rules.retention_days)
    quick = dt.timedelta(hours=QUICK_LEAVE_HOURS)

    m.left = sum(1 for j in joins if j.left_at is not None)
    mature = [j for j in joins if now - j.at >= retention]
    m.mature = len(mature)
    retained = [j for j in mature if j.left_at is None or j.left_at - j.at >= retention]
    m.counted = len(retained)
    m.pending = sum(1 for j in joins if now - j.at < retention and j.left_at is None)
    if m.mature >= rules.min_sample:
        m.retention = m.counted / m.mature

    eligible = [j for j in joins if now - j.at >= quick]
    if len(eligible) >= rules.min_sample:
        m.quick_leave_rate = sum(1 for j in eligible if j.left_at is not None and j.left_at - j.at < quick) / len(eligible)

    profiled = [j for j in joins if j.has_username is not None]
    m.profile_n = len(profiled)
    if len(profiled) >= rules.min_sample:
        m.nouser_share = sum(1 for j in profiled if not j.has_username) / len(profiled)
        m.premium_share = sum(1 for j in profiled if j.is_premium) / len(profiled)
        if fresh_threshold is not None:
            m.fresh_share = sum(1 for j in profiled if j.user_id >= fresh_threshold) / len(profiled)
        langs = [j.language for j in profiled if j.language]
        if len(langs) >= rules.min_sample:
            top, top_n = Counter(langs).most_common(1)[0]
            m.top_language = (top, top_n / len(langs))
            if common_languages:
                m.foreign_share = sum(1 for lang in langs if lang not in common_languages) / len(langs)
    return m


def _build_baseline(direct: list[Join], pooled: list[Join], now: dt.datetime, rules: Rules) -> Baseline:
    if len(direct) >= rules.min_sample:
        kind, source = "direct", direct
    elif len(pooled) >= rules.min_sample:
        kind, source = "pooled", pooled
    else:
        return Baseline(kind="none")
    fresh_threshold = _percentile([j.user_id for j in source], FRESH_QUANTILE)
    langs = [j.language for j in source if j.language]
    common: frozenset[str] = frozenset()
    if len(langs) >= rules.min_sample:
        counts = Counter(langs)
        common = frozenset(lang for lang, c in counts.items() if c / len(langs) >= LANG_KNOWN_SHARE)
    return Baseline(
        kind=kind,
        metrics=_metrics(source, now, rules, fresh_threshold, common),
        fresh_threshold=fresh_threshold,
        common_languages=common,
    )


def find_spike(joins: list[Join], now: dt.datetime, rules: Rules) -> Spike | None:
    """Окно из spike_window_min минут с наибольшим числом вступлений; всплеск — если их
    не меньше spike_min_joins и это не меньше spike_day_share всех вступлений того дня."""
    if len(joins) < rules.spike_min_joins:
        return None
    ordered = sorted(joins, key=lambda j: j.at)
    window = dt.timedelta(minutes=rules.spike_window_min)
    best_count, best_i, j = 0, 0, 0
    for i in range(len(ordered)):
        if j < i:
            j = i
        while j + 1 < len(ordered) and ordered[j + 1].at - ordered[i].at <= window:
            j += 1
        if j - i + 1 > best_count:
            best_count, best_i = j - i + 1, i
    if best_count < rules.spike_min_joins:
        return None
    start = ordered[best_i].at
    day_total = sum(1 for x in ordered if x.at.date() == start.date())
    spike = Spike(start=start, count=best_count, day_total=day_total)
    if spike.share < rules.spike_day_share:
        return None
    cohort = [x for x in ordered if start <= x.at <= start + window]
    eligible = [x for x in cohort if now - x.at >= dt.timedelta(hours=QUICK_LEAVE_HOURS)]
    if len(eligible) >= max(5, rules.min_sample // 3):
        quick = dt.timedelta(hours=QUICK_LEAVE_HOURS)
        spike.quick_leave_rate = sum(1 for x in eligible if x.left_at is not None and x.left_at - x.at < quick) / len(eligible)
    return spike


def _signals(m: Metrics, spike: Spike | None, base: Baseline, rules: Rules) -> list[Signal]:
    out: list[Signal] = []
    pct = lambda v: f"{round(v * 100)}%"  # noqa: E731
    bm = base.metrics

    if spike is not None:
        text = (
            f"всплеск: {spike.count} вступлений за {rules.spike_window_min} мин "
            f"({pct(spike.share)} вступлений за день, {spike.start.strftime('%d.%m %H:%M')} UTC)"
        )
        if spike.quick_leave_rate is not None:
            text += f"; из них ушло за сутки {pct(spike.quick_leave_rate)}"
        out.append(Signal("spike", text))

    if m.retention is not None:
        if bm.retention is not None and m.retention <= bm.retention - RETENTION_GAP:
            out.append(Signal("retention", f"удержание {rules.retention_days} дн. {pct(m.retention)} при {pct(bm.retention)} в норме"))
        elif bm.retention is None and m.retention < RETENTION_FLOOR:
            out.append(Signal("retention", f"удержание {rules.retention_days} дн. всего {pct(m.retention)}"))

    if m.quick_leave_rate is not None:
        norm = bm.quick_leave_rate or 0.0
        if m.quick_leave_rate >= max(QUICK_LEAVE_MIN_RATE, norm + QUICK_LEAVE_MARGIN):
            out.append(Signal("quick", f"{pct(m.quick_leave_rate)} ушли в первые {QUICK_LEAVE_HOURS} ч (в норме {pct(norm)})"))

    dev = rules.profile_deviation
    if m.fresh_share is not None and bm.fresh_share is not None and m.fresh_share - bm.fresh_share >= dev:
        out.append(Signal("fresh", f"много «свежих» аккаунтов: {pct(m.fresh_share)} при {pct(bm.fresh_share)} в норме"))
    if m.nouser_share is not None and bm.nouser_share is not None and m.nouser_share - bm.nouser_share >= dev:
        out.append(Signal("nouser", f"без username {pct(m.nouser_share)} при {pct(bm.nouser_share)} в норме"))
    if m.foreign_share is not None and m.foreign_share >= dev:
        out.append(Signal("lang", f"{pct(m.foreign_share)} аккаунтов с языком, нетипичным для канала"))
    return out


def _score(m: Metrics, spike: Spike | None, base: Baseline) -> int | None:
    bm = base.metrics
    parts: dict[str, float] = {}
    if m.retention is not None:
        parts["retention"] = m.retention
    if m.quick_leave_rate is not None:
        parts["quick"] = 1 - min(1.0, m.quick_leave_rate / 0.5)
    deviations = []
    if m.fresh_share is not None and bm.fresh_share is not None:
        deviations.append(max(0.0, m.fresh_share - bm.fresh_share))
    if m.nouser_share is not None and bm.nouser_share is not None:
        deviations.append(max(0.0, m.nouser_share - bm.nouser_share))
    if m.foreign_share is not None:
        deviations.append(m.foreign_share)
    if deviations:
        parts["profile"] = 1 - sum(min(1.0, d / 0.5) for d in deviations) / len(deviations)
    if m.n:
        parts["spike"] = 1 - (min(1.0, spike.count / m.n) if spike else 0.0)
    if "retention" not in parts and len(parts) < 2:
        return None
    total_weight = sum(WEIGHTS[k] for k in parts)
    return round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()) / total_weight)


def compute_report(
    bloggers: list[BloggerInput],
    direct_joins: list[Join],
    clicks_unique: dict[int, int],
    now: dt.datetime,
    rules: Rules,
) -> BloggersReport:
    pooled = [j for b in bloggers for j in b.joins]
    base = _build_baseline(direct_joins, pooled, now, rules)

    results: list[BloggerStats] = []
    for b in bloggers:
        m = _metrics(b.joins, now, rules, base.fresh_threshold, base.common_languages)
        spike = find_spike(b.joins, now, rules)
        enough = m.n >= rules.min_sample
        signals = _signals(m, spike, base, rules)
        if signals:
            verdict = "check" if len(signals) == 1 else "suspicious"
        else:
            verdict = "ok" if enough else "insufficient"

        per_link = []
        by_link: dict[int, list[Join]] = defaultdict(list)
        for j in b.joins:
            if j.link_id is not None:
                by_link[j.link_id].append(j)
        for info in b.links:
            link_joins = by_link.get(info.link_id, [])
            ls = LinkStats(info=info, joins=len(link_joins), leaves=sum(1 for j in link_joins if j.left_at is not None))
            ls.active = ls.joins - ls.leaves
            ls.clicks_unique = clicks_unique.get(info.link_id, 0)
            ls.conversion = ls.joins / ls.clicks_unique if ls.clicks_unique else None
            per_link.append(ls)

        results.append(
            BloggerStats(
                blogger_id=b.blogger_id, name=b.name, active=b.active, links=per_link, metrics=m,
                spike=spike, signals=signals, verdict=verdict,
                score=_score(m, spike, base) if enough else None,
            )
        )

    results.sort(key=lambda r: (r.metrics.counted, r.active_now, r.metrics.n), reverse=True)
    for i, r in enumerate(results, 1):
        r.rank = i
    return BloggersReport(bloggers=results, baseline=base, rules=rules)


# ------------------------------------------------------------------ загрузка из БД

_CHUNK = 500


async def load_report(session, since: dt.datetime | None, until: dt.datetime | None, rules: Rules | None = None,
                      now: dt.datetime | None = None) -> BloggersReport:
    rules = rules or Rules.from_settings()
    now = now or dt.datetime.now(dt.timezone.utc)

    rows = (
        await session.execute(
            select(Blogger, BloggerLink, InviteLink)
            .select_from(Blogger)
            .join(BloggerLink, BloggerLink.blogger_id == Blogger.id, isouter=True)
            .join(InviteLink, InviteLink.id == BloggerLink.invite_link_id, isouter=True)
            .order_by(Blogger.id, BloggerLink.id)
        )
    ).all()
    blogger_links: dict[int, list[LinkInfo]] = {}
    blogger_obj: dict[int, Blogger] = {}
    link_to_blogger: dict[int, int] = {}
    channel_ids: set[int] = set()
    for blogger, clink, ilink in rows:
        blogger_obj[blogger.id] = blogger
        blogger_links.setdefault(blogger.id, [])
        if clink is not None and ilink is not None:
            blogger_links[blogger.id].append(
                LinkInfo(ilink.id, clink.platform, ilink.name, ilink.tg_invite_link, ilink.revoked)
            )
            link_to_blogger[ilink.id] = blogger.id
            channel_ids.add(ilink.target_channel_id)

    def period(query):
        if since is not None:
            query = query.where(SubscriberEvent.occurred_at >= since)
        if until is not None:
            query = query.where(SubscriberEvent.occurred_at <= until)
        return query

    join_rows: list = []
    direct_rows: list = []
    if link_to_blogger:
        base_q = (
            select(SubscriberEvent, JoinProfile)
            .join(JoinProfile, JoinProfile.subscriber_event_id == SubscriberEvent.id, isouter=True)
            .where(SubscriberEvent.event_type == "join")
        )
        join_rows = (
            await session.execute(period(base_q.where(SubscriberEvent.invite_link_id.in_(list(link_to_blogger)))))
        ).all()
        direct_rows = (
            await session.execute(
                period(base_q.where(SubscriberEvent.is_direct.is_(True), SubscriberEvent.target_channel_id.in_(list(channel_ids))))
            )
        ).all()

    def to_join(event: SubscriberEvent, profile: JoinProfile | None) -> Join:
        return Join(
            user_id=event.tg_user_id,
            at=_aware(event.occurred_at),
            link_id=event.invite_link_id,
            has_username=profile.has_username if profile else None,
            is_premium=profile.is_premium if profile else None,
            language=(profile.language_code or None) if profile else None,
        )

    joins_by_blogger: dict[int, list[Join]] = defaultdict(list)
    for event, profile in join_rows:
        joins_by_blogger[link_to_blogger[event.invite_link_id]].append(to_join(event, profile))
    direct = [to_join(event, profile) for event, profile in direct_rows]

    await _attach_leaves(session, [j for js in joins_by_blogger.values() for j in js] + direct, channel_ids)

    clicks: dict[int, int] = {}
    if link_to_blogger:
        click_q = select(LinkClick.invite_link_id, func.count(func.distinct(LinkClick.visitor_hash))).where(
            LinkClick.invite_link_id.in_(list(link_to_blogger))
        ).group_by(LinkClick.invite_link_id)
        if since is not None:
            click_q = click_q.where(LinkClick.clicked_at >= since)
        if until is not None:
            click_q = click_q.where(LinkClick.clicked_at <= until)
        clicks = {link_id: n for link_id, n in (await session.execute(click_q)).all()}

    inputs = [
        BloggerInput(
            blogger_id=bid, name=obj.name, active=obj.active,
            links=blogger_links[bid], joins=joins_by_blogger.get(bid, []),
        )
        for bid, obj in blogger_obj.items()
    ]
    return compute_report(inputs, direct, clicks, now, rules)


async def _attach_leaves(session, joins: list[Join], channel_ids: set[int]) -> None:
    """Для каждого вступления находим уход: первое событие этого пользователя после
    вступления, если это leave (если дальше он снова вступил — считаем, что не уходил)."""
    if not joins or not channel_ids:
        return
    user_ids = sorted({j.user_id for j in joins})
    events_by_user: dict[int, list[tuple[dt.datetime, str]]] = defaultdict(list)
    for i in range(0, len(user_ids), _CHUNK):
        chunk = user_ids[i : i + _CHUNK]
        result = await session.execute(
            select(SubscriberEvent.tg_user_id, SubscriberEvent.occurred_at, SubscriberEvent.event_type).where(
                SubscriberEvent.tg_user_id.in_(chunk), SubscriberEvent.target_channel_id.in_(list(channel_ids))
            )
        )
        for user_id, at, event_type in result.all():
            events_by_user[user_id].append((_aware(at), event_type))

    for events in events_by_user.values():
        events.sort()
    for j in joins:
        events = events_by_user.get(j.user_id, [])
        later = [e for e in events if e[0] > j.at]
        if later and later[0][1] == "leave":
            j.left_at = later[0][0]
