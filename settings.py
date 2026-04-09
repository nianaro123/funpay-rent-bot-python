import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _get_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value is not None else default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.replace("\\n", "\n")


def _get_path(name: str, default: str) -> str:
    raw = os.getenv(name, default).strip()
    path = Path(raw)
    if path.is_absolute():
        return str(path)
    return str((BASE_DIR / path).resolve())


GOLDEN_KEY = _require("GOLDEN_KEY")
USER_AGENT = _require("USER_AGENT")
REQUESTS_DELAY = _get_int("REQUESTS_DELAY", 3)

TELEGRAM_ADMIN_BOT_TOKEN = _require("TELEGRAM_ADMIN_BOT_TOKEN")
TELEGRAM_ADMIN_USER_ID = _get_int("TELEGRAM_ADMIN_USER_ID", 0)

DB_PATH = _get_path("DB_PATH", "/app/data/rent_bot.sqlite3")

WELCOME_TEXT = _get_text("WELCOME_TEXT", "🤖 Привет! Я робот-помощник по выдаче аккаунтов в аренду. Напиши /help, чтобы увидеть доступные команды.")
HELP_TEXT = _get_text(
    "HELP_TEXT",
    "🤖 Доступные команды:\n"
    "/help — список команд\n"
    "/free — список свободных аккаунтов с рейтингом\n"
    "/acc — данные аккаунта\n"
    "/code — Steam Guard код\n"
    "/time — время аренды\n"
    "/admin — позвать продавца",
)

STEAM_SIGN_OUT_ENABLED = _get_bool("STEAM_SIGN_OUT_ENABLED", True)
STEAM_SIGN_OUT_NODE_BIN = _get_str("STEAM_SIGN_OUT_NODE_BIN", "node")
STEAM_SIGN_OUT_WORKER_PATH = _get_path(
    "STEAM_SIGN_OUT_WORKER_PATH",
    "steam_sign_out_worker/steam_kick_all_sessions_worker.js",
)
STEAM_SIGN_OUT_TIMEOUT_SEC = _get_int("STEAM_SIGN_OUT_TIMEOUT_SEC", 420)

AUTO_RAISE_ENABLED = _get_bool("AUTO_RAISE_ENABLED", False)
AUTO_RAISE_INTERVAL_SEC = _get_int("AUTO_RAISE_INTERVAL_SEC", 120 * 60)
