from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    admin_username: str
    admin_password_hash: str
    app_secret: str
    origin: str = "http://127.0.0.1:8000"
    allowed_origins: tuple[str, ...] = ()
    timezone: str = "Europe/Moscow"
    cookie_secure: bool = False
    session_hours: int = 12
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def zone(self):
        return ZoneInfo(self.timezone)

    @property
    def today(self):
        return datetime.now(self.zone).date()


def load_settings() -> Settings:
    # Treat values literally. A password containing ${...} must not expand into
    # another environment value; Compose single-quoted secrets behave likewise.
    load_dotenv(ROOT / ".env", override=False, interpolate=False)
    required = ("ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "APP_SECRET")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Не заданы " + ", ".join(missing) + ". Выполните python scripts/init_env.py и следуйте README.md.")
    if len(os.environ["APP_SECRET"]) < 32:
        raise RuntimeError("APP_SECRET должен содержать не менее 32 символов.")
    if not re.fullmatch(r"scrypt\$16384\$8\$5\$[0-9a-f]{32}\$[0-9a-f]{128}", os.environ["ADMIN_PASSWORD_HASH"]):
        raise RuntimeError("ADMIN_PASSWORD_HASH должен быть scrypt-хешем. Выполните scripts/init_env.py.")
    username = os.environ["ADMIN_USERNAME"].strip()
    if not 3 <= len(username) <= 64 or not all(c.isascii() and (c.isalnum() or c in "_-.@") for c in username):
        raise RuntimeError("ADMIN_USERNAME: 3–64 латинских символа, цифры, _, -, ., @.")
    zone = os.getenv("TIMEZONE", "Europe/Moscow")
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("Некорректный TIMEZONE; установите зависимости, включая tzdata.") from exc
    path = Path(os.getenv("DATABASE_PATH", "data/invoice_keeper.db"))
    origin = os.getenv("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
    from urllib.parse import urlsplit
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
        raise RuntimeError("APP_ORIGIN должен быть origin без пути, например http://127.0.0.1:8000.")
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    if parsed.scheme == "https" and not secure:
        raise RuntimeError("Для HTTPS задайте COOKIE_SECURE=true.")
    hours = int(os.getenv("SESSION_HOURS", "12"))
    if not 1 <= hours <= 168:
        raise RuntimeError("SESSION_HOURS: допустимо от 1 до 168.")
    return Settings(path if path.is_absolute() else ROOT / path, username,
                    os.environ["ADMIN_PASSWORD_HASH"], os.environ["APP_SECRET"], origin,
                    tuple(x.strip().rstrip("/") for x in os.getenv("APP_ALLOWED_ORIGINS", "").split(",") if x.strip()),
                    zone, secure, hours, os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
