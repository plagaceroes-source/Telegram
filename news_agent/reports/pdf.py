"""Рендер PDF-версии отчётов (таблицы + диаграммы) через matplotlib.

Без браузера/headless-Chrome и системных зависимостей — matplotlib с backend'ом
Agg рисует прямо в PDF, несколько страниц через PdfPages."""
from __future__ import annotations

import datetime as dt
import io
import re

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from news_agent.reports.format import fmt_change, fmt_num, fmt_pct, fmt_signed  # noqa: E402

PAGE_SIZE = (8.27, 11.69)  # A4 в дюймах
POSTS_PER_PAGE = 28

# Шрифт matplotlib (DejaVu Sans) не знает эмодзи — в тексте постов они попадаются
# часто (это реальный контент канала), поэтому для PDF-таблиц их вырезаем, чтобы
# не рисовались "пустые" квадраты вместо символа.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "]+"
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()

COLOR_JOIN = "#34a853"
COLOR_LEAVE = "#ea4335"
COLOR_NEUTRAL = "#4285f4"
COLOR_HEADER_BG = "#f1f3f4"

_RECONCILIATION_NOTE = (
    "«Прирост» — это разница реального числа подписчиков в Telegram (точная величина).\n"
    "«Подписалось/отписалось» — по событиям, которые прислал Telegram: он не гарантирует\n"
    "уведомление о каждой подписке/отписке в канале, поэтому сумма событий может быть\n"
    "меньше реального прироста — это ограничение платформы, не ошибка подсчёта."
)


def _table_page(title: str, rows: list[tuple[str, str]], footnote: str | None = None) -> plt.Figure:
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.suptitle(title, fontsize=15, fontweight="bold", y=0.97)
    ax = fig.add_axes((0.06, 0.06, 0.88, 0.85))
    ax.axis("off")
    table = ax.table(cellText=rows, colWidths=[0.62, 0.38], cellLoc="left", loc="upper center")
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1, 2.0)
    for (r, _c), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if r == 0:
            cell.set_text_props(fontweight="bold")
            cell.set_facecolor(COLOR_HEADER_BG)
        elif r % 2 == 0:
            cell.set_facecolor("#fafafa")
    if footnote:
        fig.text(0.06, 0.04, footnote, fontsize=8, color="#666666", wrap=True, va="bottom")
    return fig


def _sources_chart_page(title: str, joins_by_source: dict[str, int]) -> plt.Figure | None:
    if not joins_by_source:
        return None
    items = sorted(joins_by_source.items(), key=lambda kv: kv[1])[-20:]
    labels = [_strip_emoji(label) or label for label, _ in items]
    values = [count for _, count in items]

    fig = plt.figure(figsize=PAGE_SIZE)
    fig.suptitle(title, fontsize=15, fontweight="bold", y=0.97)
    ax = fig.add_axes((0.32, 0.06, 0.6, 0.85))
    ax.barh(labels, values, color=COLOR_JOIN)
    ax.set_xlabel("Подписалось")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for i, v in enumerate(values):
        ax.text(v, i, f" {v}", va="center", fontsize=9)
    return fig


def _posts_table_pages(title: str, posts: list[dict], tz, show_date: bool) -> list[plt.Figure]:
    if not posts:
        return []
    header = ["Дата/время" if show_date else "Время", "Просмотры", "Репосты", "Реакции", "Коммент.", "Текст"]
    max_snippet = 46
    rows = []
    for p in posts:
        when = p["published_at"].astimezone(tz)
        when_str = when.strftime("%d.%m %H:%M") if show_date else when.strftime("%H:%M")
        snippet = _strip_emoji(p["snippet"])
        if len(snippet) > max_snippet:
            snippet = snippet[:max_snippet].rstrip() + "…"
        rows.append([when_str, str(p["views"]), str(p["forwards"]), str(p["reactions"]), str(p["comments"]), snippet])

    pages = []
    for i in range(0, len(rows), POSTS_PER_PAGE):
        chunk = rows[i : i + POSTS_PER_PAGE]
        page_num = i // POSTS_PER_PAGE + 1
        total_pages = (len(rows) + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
        page_title = title if total_pages == 1 else f"{title} ({page_num}/{total_pages})"

        fig = plt.figure(figsize=PAGE_SIZE)
        fig.suptitle(page_title, fontsize=15, fontweight="bold", y=0.97)
        ax = fig.add_axes((0.04, 0.04, 0.92, 0.88))
        ax.axis("off")
        table = ax.table(
            cellText=[header] + chunk,
            colWidths=[0.11, 0.10, 0.08, 0.08, 0.09, 0.54],
            cellLoc="left",
            loc="upper center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        table.scale(1, 1.5)
        for (r, _c), cell in table.get_celld().items():
            cell.set_edgecolor("#eeeeee")
            if r == 0:
                cell.set_text_props(fontweight="bold")
                cell.set_fontsize(7.5)
                cell.set_facecolor(COLOR_HEADER_BG)
        pages.append(fig)
    return pages


def _comparison_chart_page(title: str, cur: dict, prev: dict, cur_label: str, prev_label: str) -> plt.Figure:
    metrics = [
        ("Постов", cur["posts_count"], prev["posts_count"]),
        ("Прирост подп.", cur["growth"], prev["growth"]),
        ("Подписалось", cur["joins"], prev["joins"]),
        ("Отписалось", cur["leaves"], prev["leaves"]),
        ("Просмотры", cur["totals"]["views"], prev["totals"]["views"]),
        ("Репосты", cur["totals"]["forwards"], prev["totals"]["forwards"]),
        ("Реакции", cur["totals"]["reactions"], prev["totals"]["reactions"]),
        ("Комментарии", cur["totals"]["comments"], prev["totals"]["comments"]),
    ]

    fig, axes = plt.subplots(2, 4, figsize=PAGE_SIZE)
    fig.suptitle(title, fontsize=15, fontweight="bold", y=0.97)
    for ax, (name, cur_val, prev_val) in zip(axes.flat, metrics):
        bars = ax.bar([cur_label, prev_label], [cur_val, prev_val], color=[COLOR_NEUTRAL, "#bdbdbd"])
        ax.set_title(name, fontsize=10)
        ax.tick_params(labelsize=8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                fmt_num(int(height)),
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return fig


def _save_pdf(figures: list[plt.Figure]) -> bytes:
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        for fig in figures:
            pdf.savefig(fig)
            plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_daily_pdf(day: dt.date, d: dict, tz) -> bytes:
    delta = d["end_subs"] - d["start_subs"]
    net_events = d["joins"] - d["leaves"]
    unaccounted = delta - net_events
    summary_rows = [
        ("Показатель", "Значение"),
        ("Опубликовано постов", fmt_num(d["posts_count"])),
        ("Подписчиков (стало)", fmt_num(d["end_subs"])),
        ("Подписчиков (было)", fmt_num(d["start_subs"])),
        ("Прирост за день", ("+" if delta >= 0 else "−") + fmt_num(abs(delta))),
        ("Подписалось (учтено)", fmt_num(d["joins"])),
        ("Отписалось (учтено)", fmt_num(d["leaves"])),
    ]
    if unaccounted != 0:
        summary_rows.append(("Не учтено Telegram-событиями", ("+" if unaccounted >= 0 else "−") + fmt_num(abs(unaccounted))))

    figures = [
        _table_page(
            f"Отчёт за {day:%d.%m.%Y} — сводка",
            summary_rows,
            footnote=_RECONCILIATION_NOTE if unaccounted != 0 else None,
        )
    ]

    sources_page = _sources_chart_page(f"Источники подписок — {day:%d.%m.%Y}", d["joins_by_source"])
    if sources_page:
        figures.append(sources_page)

    figures.extend(_posts_table_pages(f"Посты за {day:%d.%m.%Y}", d["posts"], tz, show_date=False))

    return _save_pdf(figures)


def render_monthly_pdf(
    year: int, month: int, month_name: str, prev_month_name: str, prev_year: int, cur: dict, prev: dict, top_posts: list[dict], tz
) -> bytes:
    summary_rows = [
        ("Показатель", f"{month_name} {year}"),
        ("Опубликовано постов", f"{fmt_num(cur['posts_count'])} ({fmt_pct(cur['posts_count'], prev['posts_count'])})"),
        ("Подписчиков на конец месяца", fmt_num(cur["end_subs"])),
        ("Подписчиков на начало месяца", fmt_num(cur["start_subs"])),
        ("Прирост за месяц", ("+" if cur["growth"] >= 0 else "−") + fmt_num(abs(cur["growth"]))),
        ("Подписалось (учтено)", f"{fmt_num(cur['joins'])} ({fmt_pct(cur['joins'], prev['joins'])})"),
        ("Отписалось (учтено)", f"{fmt_num(cur['leaves'])} ({fmt_pct(cur['leaves'], prev['leaves'])})"),
        ("Просмотры (сумма)", f"{fmt_num(cur['totals']['views'])} ({fmt_pct(cur['totals']['views'], prev['totals']['views'])})"),
        ("Репосты (сумма)", f"{fmt_num(cur['totals']['forwards'])} ({fmt_pct(cur['totals']['forwards'], prev['totals']['forwards'])})"),
        ("Реакции (сумма)", f"{fmt_num(cur['totals']['reactions'])} ({fmt_pct(cur['totals']['reactions'], prev['totals']['reactions'])})"),
        ("Комментарии (сумма)", f"{fmt_num(cur['totals']['comments'])} ({fmt_pct(cur['totals']['comments'], prev['totals']['comments'])})"),
    ]
    unaccounted = cur["growth"] - (cur["joins"] - cur["leaves"])
    if unaccounted != 0:
        summary_rows.append(("Не учтено Telegram-событиями", ("+" if unaccounted >= 0 else "−") + fmt_num(abs(unaccounted))))

    figures = [
        _table_page(
            f"Месячный отчёт — {month_name} {year}",
            summary_rows,
            footnote=_RECONCILIATION_NOTE if unaccounted != 0 else None,
        )
    ]
    figures.append(
        _comparison_chart_page(
            f"{month_name} {year} vs {prev_month_name} {prev_year}",
            cur,
            prev,
            f"{month_name[:3]}.",
            f"{prev_month_name[:3]}.",
        )
    )

    sources_page = _sources_chart_page(f"Источники подписок — {month_name} {year}", cur["joins_by_source"])
    if sources_page:
        figures.append(sources_page)

    figures.extend(_posts_table_pages(f"Топ постов — {month_name} {year}", top_posts, tz, show_date=True))

    return _save_pdf(figures)


# --- недельный отчёт и отчёт «с начала месяца»: динамика по дням и сравнение с прошлым периодом ----

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
COLOR_PREV = "#9e9e9e"


def _grid_table_page(
    title: str,
    header: list[str],
    rows: list[list[str]],
    col_widths: list[float],
    footnote: str | None = None,
    font: float = 9,
    row_scale: float = 1.7,
) -> plt.Figure:
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.97)
    ax = fig.add_axes((0.04, 0.07 if footnote else 0.04, 0.92, 0.85))
    ax.axis("off")
    table = ax.table(cellText=[header] + rows, colWidths=col_widths, cellLoc="left", loc="upper center")
    table.auto_set_font_size(False)
    table.set_fontsize(font)
    table.scale(1, row_scale)
    for (r, _c), cell in table.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if r == 0:
            cell.set_text_props(fontweight="bold")
            cell.set_facecolor(COLOR_HEADER_BG)
        elif r % 2 == 0:
            cell.set_facecolor("#fafafa")
    if footnote:
        fig.text(0.05, 0.03, footnote, fontsize=8, color="#666666", va="bottom")
    return fig


def _growth_text(m: dict) -> str:
    return fmt_signed(m["growth"]) if m.get("start_known", True) else "н/д"


def _compare_rows(cur: dict, prev: dict) -> list[list[str]]:
    known = cur.get("start_known", True) and prev.get("start_known", True)
    t_cur, t_prev = cur["totals"], prev["totals"]
    return [
        ["Опубликовано постов", fmt_num(cur["posts_count"]), fmt_num(prev["posts_count"]), fmt_change(cur["posts_count"], prev["posts_count"])],
        ["Подписчиков на конец", fmt_num(cur["end_subs"]), fmt_num(prev["end_subs"]), "—"],
        [
            "Прирост подписчиков",
            _growth_text(cur),
            _growth_text(prev),
            fmt_change(cur["growth"], prev["growth"], with_pct=False) if known else "н/д",
        ],
        ["Подписалось (учтено)", fmt_num(cur["joins"]), fmt_num(prev["joins"]), fmt_change(cur["joins"], prev["joins"])],
        ["Отписалось (учтено)", fmt_num(cur["leaves"]), fmt_num(prev["leaves"]), fmt_change(cur["leaves"], prev["leaves"])],
        ["Просмотры постов", fmt_num(t_cur["views"]), fmt_num(t_prev["views"]), fmt_change(t_cur["views"], t_prev["views"])],
        ["Репосты", fmt_num(t_cur["forwards"]), fmt_num(t_prev["forwards"]), fmt_change(t_cur["forwards"], t_prev["forwards"])],
        ["Реакции", fmt_num(t_cur["reactions"]), fmt_num(t_prev["reactions"]), fmt_change(t_cur["reactions"], t_prev["reactions"])],
        ["Комментарии", fmt_num(t_cur["comments"]), fmt_num(t_prev["comments"]), fmt_change(t_cur["comments"], t_prev["comments"])],
    ]


def _compare_table_page(title: str, cur_label: str, prev_label: str, cur: dict, prev: dict, footnote: str | None) -> plt.Figure:
    return _grid_table_page(
        title,
        ["Показатель", cur_label, prev_label, "Изменение"],
        _compare_rows(cur, prev),
        [0.34, 0.2, 0.2, 0.26],
        footnote=footnote,
        font=10,
        row_scale=2.2,
    )


def _daily_rows(series: list[dict], label_fn) -> list[list[str]]:
    rows = []
    for item in series:
        growth = "н/д" if item["growth"] is None else fmt_signed(item["growth"])
        rows.append(
            [
                label_fn(item["day"]),
                fmt_num(item["end_subs"]) if item["end_subs"] else "н/д",
                growth,
                fmt_num(item["joins"]),
                fmt_num(item["leaves"]),
                fmt_num(item["posts"]),
                fmt_num(item["views"]),
            ]
        )
    return rows


def _daily_table_page(title: str, series: list[dict], label_fn, footnote: str | None = None) -> plt.Figure:
    return _grid_table_page(
        title,
        ["День", "Подписчиков", "Изменение", "Подписалось", "Отписалось", "Постов", "Просмотры"],
        _daily_rows(series, label_fn),
        [0.17, 0.15, 0.13, 0.14, 0.14, 0.11, 0.16],
        footnote=footnote,
        font=9 if len(series) <= 10 else 7.5,
        row_scale=1.9 if len(series) <= 10 else 1.35,
    )


def _overlay_chart_page(
    title: str, x_labels: list[str], panels: list[tuple[str, list, list]], cur_label: str, prev_label: str, rotate: bool = False
) -> plt.Figure:
    """Сетка 2×2 линейных графиков: текущий период (цветная линия) против прошлого (серый пунктир).
    Значение None (нет данных за этот день) на графике пропускается."""
    fig, axes = plt.subplots(2, 2, figsize=PAGE_SIZE)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.97)
    for ax, (name, cur_vals, prev_vals) in zip(axes.flat, panels):
        xs = list(range(len(x_labels)))
        ax.plot(xs[: len(prev_vals)], prev_vals, color=COLOR_PREV, linestyle="--", marker="o", markersize=3, label=prev_label)
        ax.plot(xs[: len(cur_vals)], cur_vals, color=COLOR_NEUTRAL, marker="o", markersize=3.5, linewidth=2, label=cur_label)
        ax.set_title(name, fontsize=10)
        step = max(1, len(x_labels) // 8)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels(x_labels[::step], fontsize=8, rotation=45 if rotate else 0)
        ax.tick_params(axis="y", labelsize=8)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(alpha=0.25)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.legend(fontsize=7, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return fig


def _series_values(series: list[dict], key: str, cumulative: bool) -> list:
    out, total = [], 0
    for item in series:
        value = item[key]
        if value is None:
            out.append(None)
            continue
        total += value
        out.append(total if cumulative else value)
    return out


def _overlay_panels(cur_series: list[dict], prev_series: list[dict], cumulative: bool) -> list[tuple[str, list, list]]:
    def panel(name, key):
        return (
            name if not cumulative else f"{name} (накопительно)",
            _series_values(cur_series, key, cumulative),
            _series_values(prev_series, key, cumulative),
        )

    return [
        panel("Прирост подписчиков", "growth"),
        panel("Подписалось", "joins"),
        panel("Постов", "posts"),
        panel("Просмотры постов", "views"),
    ]


def render_weekly_pdf(monday: dt.date, cur: dict, prev: dict, cur_series: list[dict], prev_series: list[dict], top_posts: list[dict], tz) -> bytes:
    sunday = monday + dt.timedelta(days=6)
    prev_monday = monday - dt.timedelta(days=7)
    prev_sunday = prev_monday + dt.timedelta(days=6)
    cur_label = f"{monday:%d.%m}–{sunday:%d.%m}"
    prev_label = f"{prev_monday:%d.%m}–{prev_sunday:%d.%m}"
    unaccounted = cur["growth"] - (cur["joins"] - cur["leaves"])
    note = _RECONCILIATION_NOTE if unaccounted != 0 and cur["start_known"] else None

    figures = [
        _compare_table_page(f"Недельный отчёт — {monday:%d.%m.%Y}–{sunday:%d.%m.%Y}", cur_label, prev_label, cur, prev, note),
        _daily_table_page(
            f"Динамика по дням — {cur_label}",
            cur_series,
            lambda d: f"{WEEKDAYS_RU[d.weekday()]} {d:%d.%m}",
            footnote="«Изменение» — разница числа подписчиков за день. «Просмотры» — по постам, вышедшим в этот день.",
        ),
        _overlay_chart_page(
            f"Динамика по дням: {cur_label} и {prev_label}",
            WEEKDAYS_RU,
            _overlay_panels(cur_series, prev_series, cumulative=False),
            cur_label,
            prev_label,
        ),
    ]
    sources_page = _sources_chart_page(f"Источники подписок — {cur_label}", cur["joins_by_source"])
    if sources_page:
        figures.append(sources_page)
    figures.extend(_posts_table_pages(f"Топ постов — {cur_label}", top_posts, tz, show_date=True))
    return _save_pdf(figures)


def render_mtd_pdf(b: dict, cur: dict, prev: dict, cur_series: list[dict], prev_series: list[dict], top_posts: list[dict], tz, month_names: list[str], now_local: dt.datetime) -> bytes:
    day = b["day"]
    cur_name = f"{month_names[b['month']]} {b['year']}"
    prev_name = f"{month_names[b['prev_month']]} {b['prev_year']}"
    cur_label = f"1–{day} {month_names[b['month']][:3]}."
    prev_label = f"1–{b['prev_last_day']} {month_names[b['prev_month']][:3]}."
    notes = [f"Сравнение «к тому же моменту»: {prev_label} против {cur_label} (по {now_local:%d.%m %H:%M})."]
    if b["capped"]:
        notes.append(f"В прошлом месяце {b['prev_last_day']} дней — взят весь месяц.")
    unaccounted = cur["growth"] - (cur["joins"] - cur["leaves"])
    if unaccounted != 0 and cur["start_known"]:
        notes.append("«Прирост» — разница реального числа подписчиков; подписалось/отписалось — только события Telegram.")

    figures = [
        _compare_table_page(f"{cur_name}: с 1-го числа по сегодня", cur_label, prev_label, cur, prev, "\n".join(notes)),
        _overlay_chart_page(
            f"Динамика с начала месяца: {cur_name} и {prev_name}",
            [str(d) for d in range(1, max(len(cur_series), len(prev_series)) + 1)],
            _overlay_panels(cur_series, prev_series, cumulative=True),
            cur_label,
            prev_label,
        ),
        _daily_table_page(f"По дням — {cur_name}", cur_series, lambda d: f"{d:%d.%m} {WEEKDAYS_RU[d.weekday()]}"),
    ]
    sources_page = _sources_chart_page(f"Источники подписок — {cur_name} (по сегодня)", cur["joins_by_source"])
    if sources_page:
        figures.append(sources_page)
    figures.extend(_posts_table_pages(f"Топ постов — {cur_name} (по сегодня)", top_posts, tz, show_date=True))
    return _save_pdf(figures)
