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
