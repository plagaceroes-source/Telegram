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

from news_agent.reports.format import fmt_num, fmt_pct  # noqa: E402

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


def _table_page(title: str, rows: list[tuple[str, str]]) -> plt.Figure:
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
    summary_rows = [
        ("Показатель", "Значение"),
        ("Опубликовано постов", fmt_num(d["posts_count"])),
        ("Подписчиков (стало)", fmt_num(d["end_subs"])),
        ("Подписчиков (было)", fmt_num(d["start_subs"])),
        ("Прирост за день", ("+" if delta >= 0 else "−") + fmt_num(abs(delta))),
        ("Подписалось", fmt_num(d["joins"])),
        ("Отписалось", fmt_num(d["leaves"])),
    ]

    figures = [_table_page(f"Отчёт за {day:%d.%m.%Y} — сводка", summary_rows)]

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
        ("Подписалось", f"{fmt_num(cur['joins'])} ({fmt_pct(cur['joins'], prev['joins'])})"),
        ("Отписалось", f"{fmt_num(cur['leaves'])} ({fmt_pct(cur['leaves'], prev['leaves'])})"),
        ("Просмотры (сумма)", f"{fmt_num(cur['totals']['views'])} ({fmt_pct(cur['totals']['views'], prev['totals']['views'])})"),
        ("Репосты (сумма)", f"{fmt_num(cur['totals']['forwards'])} ({fmt_pct(cur['totals']['forwards'], prev['totals']['forwards'])})"),
        ("Реакции (сумма)", f"{fmt_num(cur['totals']['reactions'])} ({fmt_pct(cur['totals']['reactions'], prev['totals']['reactions'])})"),
        ("Комментарии (сумма)", f"{fmt_num(cur['totals']['comments'])} ({fmt_pct(cur['totals']['comments'], prev['totals']['comments'])})"),
    ]

    figures = [_table_page(f"Месячный отчёт — {month_name} {year}", summary_rows)]
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
