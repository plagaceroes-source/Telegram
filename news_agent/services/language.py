"""Language detection for incoming posts (ТЗ 2.1: es/ru/uk/other)."""
from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

# Make detection deterministic.
DetectorFactory.seed = 0


def detect_language(text: str) -> str:
    """Returns an ISO 639-1 code (es/ru/uk/en/...), or "unknown" if too short/undetectable."""
    text = (text or "").strip()
    if len(text) < 3:
        return "unknown"
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"
