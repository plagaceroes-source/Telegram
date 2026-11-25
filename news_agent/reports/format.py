"""Общее форматирование чисел для отчётов (news_agent/reports/)."""
from __future__ import annotations


def fmt_num(value: int) -> str:
    return "{:,}".format(value).replace(",", " ")


def fmt_pct(cur: int, prev: int) -> str:
    if not prev:
        return "н/д" if not cur else "новое"
    pct = (cur - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def fmt_signed(value: int) -> str:
    return ("+" if value >= 0 else "−") + fmt_num(abs(value))


def fmt_change(cur: int, prev: int, with_pct: bool = True) -> str:
    """Изменение «сейчас к раньше»: «+12 (+8.0%)»."""
    diff = cur - prev
    if not with_pct or not prev:
        return fmt_signed(diff)
    return f"{fmt_signed(diff)} ({fmt_pct(cur, prev)})"
