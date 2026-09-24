"""Общее форматирование для текстов отчётов (news_agent/reports/)."""
from __future__ import annotations

TELEGRAM_MESSAGE_LIMIT = 3500  # с запасом от лимита Bot API в 4096 символов


def fmt_num(value: int) -> str:
    return "{:,}".format(value).replace(",", " ")


def fmt_delta(value: int) -> str:
    return f"+{fmt_num(value)}" if value >= 0 else f"−{fmt_num(abs(value))}"


def fmt_pct(cur: int, prev: int) -> str:
    if not prev:
        return "н/д" if not cur else "новое"
    pct = (cur - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def chunk_message(lines: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Режет список строк на сообщения не длиннее limit символов, не разрывая строки."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]
