import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MOD_CHAT_ID = int(os.getenv("MOD_CHAT_ID", "0") or 0)
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").lstrip("@")

SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "wordsite.db")

START_PRICE = _f("START_PRICE", 1)
PAY_WINDOW = int(_f("PAY_WINDOW_SECONDS", 300))
MAX_WORD_LEN = int(_f("MAX_WORD_LEN", 30))
UAH_RATE = _f("UAH_RATE", 45)

BIZUM_PHONE = os.getenv("BIZUM_PHONE", "")
CRYPTO_ADDRESS = os.getenv("CRYPTO_ADDRESS", "")
CRYPTO_NETWORK = os.getenv("CRYPTO_NETWORK", "USDT (TRC20)")
MONO_CARD = os.getenv("MONO_CARD", "")

OPERATOR_NAME = os.getenv("OPERATOR_NAME", "[имя оператора]")
OPERATOR_EMAIL = os.getenv("OPERATOR_EMAIL", "[email]")

LOGO_DIR = os.getenv("LOGO_DIR", str(Path(DB_PATH).resolve().parent / "logos"))
MAX_LOGO_BYTES = int(_f("MAX_LOGO_BYTES", 2 * 1024 * 1024))
