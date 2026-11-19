"""Форматирование цен и дат для сайта, бота и обложек."""
from datetime import datetime
from zoneinfo import ZoneInfo

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def price(p) -> str:
    """1 -> '1', 32768 -> '32 768', 1048576 -> '1 048 576' (неразрывные пробелы), без 1e+06."""
    p = float(p)
    if abs(p - round(p)) < 1e-9:
        return f"{int(round(p)):,}".replace(",", " ")
    return f"{p:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def date_ru(ts, tz="Europe/Madrid") -> str:
    d = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} г. в {d:%H:%M}"
