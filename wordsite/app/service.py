"""Бизнес-логика: слова, заявки, слот, цена x2. Синхронно, SQLite, одна блокировка."""
import io
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import zipfile
from math import ceil

from . import config, images

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS words(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL, url TEXT, business TEXT, logo TEXT,
  price REAL NOT NULL, started_at REAL NOT NULL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT UNIQUE NOT NULL,
  word TEXT NOT NULL, url TEXT, business TEXT, logo TEXT, contact TEXT NOT NULL,
  ip TEXT, price REAL, status TEXT NOT NULL,
  method TEXT, created_at REAL NOT NULL,
  approved_at REAL, expires_at REAL, claimed_at REAL, paid_at REAL
);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
"""
# status: pending -> approved -> claimed -> live (потом replaced)
#         pending -> rejected | approved -> expired | cancelled


class OrderError(Exception):
    pass


def init(path: str | None = None):
    global _conn
    _conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    for t in ("words", "orders"):  # миграция старых баз
        if "logo" not in [r["name"] for r in _q(f"PRAGMA table_info({t})")]:
            _conn.execute(f"ALTER TABLE {t} ADD COLUMN logo TEXT")
    _conn.commit()


def _q(sql, args=()):
    return _conn.execute(sql, args).fetchall()


def _one(sql, args=()):
    r = _conn.execute(sql, args).fetchone()
    return dict(r) if r else None


# ---------- валидация ----------
_WORD_RE = re.compile(r"^[\w\-!?.,:;&@#%+*()'«»\"]+$", re.UNICODE)


def clean_word(raw: str) -> str:
    w = re.sub(r"\s+", "", (raw or ""))  # фраза -> всё слитно
    if not w:
        raise OrderError("Напиши слово")
    if len(w) > config.MAX_WORD_LEN:
        raise OrderError(f"Слишком длинно: максимум {config.MAX_WORD_LEN} символов")
    if not _WORD_RE.match(w) or re.search(r"https?:|www\.|<|>", w, re.I):
        raise OrderError("В слове допустимы буквы, цифры и простая пунктуация (без ссылок и тегов)")
    return w


def clean_url(raw: str) -> str | None:
    u = (raw or "").strip()
    if not u:
        return None
    if re.match(r"^(t\.me|instagram\.com|www\.)", u, re.I) or "." in u and "://" not in u:
        u = "https://" + u
    if not re.match(r"^https?://[^\s<>\"']{3,300}$", u, re.I):
        raise OrderError("Ссылка должна начинаться с http:// или https://")
    return u


def _clip(s, n):
    return (s or "").strip()[:n]


# ---------- состояние ----------
def current_word():
    return _one("SELECT * FROM words WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")


def next_price() -> float:
    cur = current_word()
    return cur["price"] * 2 if cur else float(config.START_PRICE)


def slot_lock(now=None):
    """Заказ, который сейчас держит слот (ждём оплату / проверяем оплату)."""
    now = now or time.time()
    return _one(
        "SELECT * FROM orders WHERE status='claimed' "
        "OR (status='approved' AND expires_at>?) ORDER BY id LIMIT 1", (now,))


def state():
    cur = current_word()
    lock = slot_lock()
    total = _one("SELECT COUNT(*) c, COALESCE(SUM(price),0) s FROM words")
    return {
        "word": cur,
        "next_price": next_price(),
        "locked": bool(lock),
        "lock_left": max(0, int(lock["expires_at"] - time.time())) if lock and lock["status"] == "approved" else (None if not lock else -1),
        "total_words": total["c"],
        "total_sum": total["s"],
        "now": time.time(),
    }


def history(limit=200):
    return [dict(r) for r in _q(
        "SELECT * FROM words WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- заявки ----------
def create_order(word, url, business, contact="", ip="", logo=None):
    with _lock:
        w = clean_word(word)
        u = clean_url(url)
        contact = _clip(contact, 80)  # необязателен: заказ открывается по секретной ссылке
        if ip:
            n = _one("SELECT COUNT(*) c FROM orders WHERE ip=? AND created_at>? AND status IN ('pending','approved','claimed')",
                     (ip, time.time() - 3600))["c"]
            if n >= 3:
                raise OrderError("Слишком много заявок с вашего адреса. Подожди немного")
        if _one("SELECT COUNT(*) c FROM orders WHERE status='pending'")["c"] >= 30:
            raise OrderError("Очередь на модерацию переполнена, загляни позже")
        token = secrets.token_urlsafe(12)
        cur = _conn.execute(
            "INSERT INTO orders(token,word,url,business,logo,contact,ip,status,created_at) VALUES(?,?,?,?,?,?,?, 'pending', ?)",
            (token, w, u, _clip(business, 80), logo, contact, ip, time.time()))
        _conn.commit()
        return get_order(cur.lastrowid)


def get_order(oid):
    return _one("SELECT * FROM orders WHERE id=?", (oid,))


def get_order_by_token(token):
    return _one("SELECT * FROM orders WHERE token=?", (token,))


def approve(oid):
    """Модератор одобрил: фиксируем цену и даём 5 минут на оплату."""
    with _lock:
        o = get_order(oid)
        if not o:
            raise OrderError("Заявка не найдена")
        if o["status"] != "pending":
            raise OrderError(f"Заявка уже в статусе «{o['status']}»")
        lock = slot_lock()
        if lock:
            raise OrderError(f"Слот занят заявкой #{lock['id']}, подожди окончания её таймера")
        now = time.time()
        _conn.execute("UPDATE orders SET status='approved', price=?, approved_at=?, expires_at=? WHERE id=?",
                      (next_price(), now, now + config.PAY_WINDOW, oid))
        _conn.commit()
        return get_order(oid)


def reject(oid):
    with _lock:
        o = get_order(oid)
        if not o or o["status"] != "pending":
            raise OrderError("Заявка уже обработана")
        _conn.execute("UPDATE orders SET status='rejected', logo=NULL WHERE id=?", (oid,))
        _conn.commit()
        images.delete_logo(o["logo"])
        return get_order(oid)


def claim_paid(token, method):
    """Клиент нажал «Я оплатил»: таймер замораживается, ждём подтверждения модератора."""
    with _lock:
        o = get_order_by_token(token)
        if not o:
            raise OrderError("Заявка не найдена")
        if o["status"] != "approved" or o["expires_at"] < time.time():
            raise OrderError("Время на оплату вышло или заявка уже не активна")
        if method not in ("bizum", "crypto", "mono"):
            raise OrderError("Выбери способ оплаты")
        _conn.execute("UPDATE orders SET status='claimed', method=?, claimed_at=? WHERE id=?",
                      (method, time.time(), o["id"]))
        _conn.commit()
        return get_order(o["id"])


def cancel(token):
    with _lock:
        o = get_order_by_token(token)
        if not o or o["status"] not in ("pending", "approved"):
            raise OrderError("Отменить уже нельзя")
        _conn.execute("UPDATE orders SET status='cancelled', logo=NULL WHERE id=?", (o["id"],))
        _conn.commit()
        images.delete_logo(o["logo"])
        return get_order(o["id"])


def confirm_payment(oid):
    """Модератор подтвердил оплату: слово сразу выходит на сайт, цена следующего x2."""
    with _lock:
        o = get_order(oid)
        if not o:
            raise OrderError("Заявка не найдена")
        if o["status"] not in ("claimed", "approved"):
            raise OrderError(f"Заявка в статусе «{o['status']}», подтверждать нечего")
        now = time.time()
        _conn.execute("UPDATE words SET ended_at=? WHERE ended_at IS NULL", (now,))
        _conn.execute("INSERT INTO words(text,url,business,logo,price,started_at) VALUES(?,?,?,?,?,?)",
                      (o["word"], o["url"], o["business"], o["logo"], o["price"], now))
        _conn.execute("UPDATE orders SET status='live', paid_at=? WHERE id=?", (now, oid))
        _conn.commit()
        return get_order(oid)


def deny_payment(oid):
    """Оплата не пришла."""
    with _lock:
        o = get_order(oid)
        if not o or o["status"] != "claimed":
            raise OrderError("Нечего отклонять")
        _conn.execute("UPDATE orders SET status='expired', logo=NULL WHERE id=?", (oid,))
        _conn.commit()
        images.delete_logo(o["logo"])
        return get_order(oid)


def sweep_expired():
    """Просроченные заявки без «Я оплатил» -> expired. Возвращает их для уведомлений."""
    with _lock:
        rows = _q("SELECT * FROM orders WHERE status='approved' AND expires_at<=?", (time.time(),))
        ids = [r["id"] for r in rows]
        for i in ids:
            _conn.execute("UPDATE orders SET status='expired', logo=NULL WHERE id=?", (i,))
        _conn.commit()
        for r in rows:
            images.delete_logo(r["logo"])
        return [dict(r) for r in rows]


# ---------- оплата ----------
def pay_info(price):
    return {
        "eur": f"{price:g}",
        "uah": ceil(price * config.UAH_RATE),
        "bizum_phone": config.BIZUM_PHONE,
        "crypto_address": config.CRYPTO_ADDRESS,
        "crypto_network": config.CRYPTO_NETWORK,
        "mono_card": config.MONO_CARD,
    }


# ---------- онлайн ----------
_online: dict[str, float] = {}


def ping(vid: str) -> int:
    now = time.time()
    if vid and len(vid) <= 64:
        _online[vid] = now
    for k in [k for k, t in _online.items() if now - t > 45]:
        del _online[k]
    return max(1, len(_online))


def drop_logo(oid):
    """Модератор убрал логотип (заявка остаётся)."""
    with _lock:
        o = get_order(oid)
        if not o or o["status"] not in ("pending", "approved", "claimed"):
            raise OrderError("Заявка уже обработана")
        _conn.execute("UPDATE orders SET logo=NULL WHERE id=?", (oid,))
        _conn.commit()
        images.delete_logo(o["logo"])
        return get_order(oid)


def reset_all():
    """Полная очистка перед запуском: слова, заявки, файлы логотипов. Возвращает (слов, заявок)."""
    with _lock:
        n_words = _one("SELECT COUNT(*) c FROM words")["c"]
        n_orders = _one("SELECT COUNT(*) c FROM orders")["c"]
        logos = [r["logo"] for r in _q("SELECT logo FROM orders WHERE logo IS NOT NULL UNION SELECT logo FROM words WHERE logo IS NOT NULL")]
        _conn.execute("DELETE FROM words")
        _conn.execute("DELETE FROM orders")
        _conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('words','orders')")
        _conn.commit()
        for name in logos:
            images.delete_logo(name)
        return n_words, n_orders


def make_backup(with_logos=True) -> bytes:
    """Zip: согласованная копия базы (wordsite.db) + логотипы. Безопасно при работающем сайте."""
    from pathlib import Path
    with _lock, tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "wordsite.db"
        out = sqlite3.connect(dst)
        _conn.backup(out)
        out.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(dst, "wordsite.db")
            logos = Path(config.LOGO_DIR)
            if with_logos and logos.is_dir():
                for f in sorted(logos.glob("*.png")):
                    z.write(f, f"logos/{f.name}")
        return buf.getvalue()
