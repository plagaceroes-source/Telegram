"""Парольная защита админки: подписанная cookie-сессия + ограничение попыток входа.

Cookie вместо HTTP Basic — потому что iOS-PWA («На главный экран») плохо хранит
Basic-авторизацию и постоянно переспрашивает пароль, а cookie живёт месяцами.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi import Request

from news_agent.config import settings

COOKIE_NAME = "admin_session"
SESSION_TTL = 90 * 24 * 3600

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW = 15 * 60

# Путь → публичный, вход не нужен. /go/* — счётчик переходов по рекламным ссылкам,
# его открывают посторонние люди.
PUBLIC_EXACT = {"/login"}
PUBLIC_PREFIXES = ("/static/", "/go/")

GLOBAL_MAX_FAILED = 50  # общий потолок на случай перебора с множества адресов

_failed: dict[str, list[float]] = {}


def _signing_key() -> bytes:
    # Пароль входит в ключ: смена ADMIN_PASSWORD сразу разлогинивает все устройства.
    raw = f"{settings.admin_secret_key}|{settings.admin_password}".encode()
    return hashlib.sha256(raw).digest()


def make_session_token(now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + SESSION_TTL)
    sig = hmac.new(_signing_key(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def verify_session_token(token: str | None, now: float | None = None) -> bool:
    if not token or not settings.admin_password:
        return False
    expires_s, _, sig = token.partition(".")
    if not expires_s.isdigit() or not sig:
        return False
    expected = hmac.new(_signing_key(), expires_s.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return int(expires_s) > (now if now is not None else time.time())


def check_password(candidate: str) -> bool:
    if not settings.admin_password:
        return False
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(),
        hashlib.sha256(settings.admin_password.encode()).digest(),
    )


def is_public_path(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def is_authenticated(request: Request) -> bool:
    return verify_session_token(request.cookies.get(COOKIE_NAME))


def client_ip(request: Request) -> str:
    # Первый элемент X-Forwarded-For клиент может подделать, поэтому берём то, что
    # дописал ближайший прокси хостинга: X-Real-IP или последний элемент цепочки.
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def is_locked_out(ip: str, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    recent = [t for t in _failed.get(ip, []) if now - t < LOCKOUT_WINDOW]
    if recent:
        _failed[ip] = recent
    else:
        _failed.pop(ip, None)
    total = sum(1 for ts in _failed.values() for t in ts if now - t < LOCKOUT_WINDOW)
    return len(recent) >= MAX_FAILED_ATTEMPTS or total >= GLOBAL_MAX_FAILED


def register_failure(ip: str, now: float | None = None) -> None:
    _failed.setdefault(ip, []).append(now if now is not None else time.time())


def clear_failures(ip: str) -> None:
    _failed.pop(ip, None)


def safe_next(target: str | None) -> str:
    """Возвращаем только локальный путь — иначе /login?next=//evil.com стал бы open-redirect."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def login_redirect_url(request: Request) -> str:
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return "/login" if target == "/" else f"/login?next={quote(target, safe='')}"
