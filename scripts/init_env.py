"""Create local .env without exposing credentials in command arguments or output.

This script uses only Python's standard library; run before installing packages.
"""
import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.security import hash_password


def password_from(name, prompt, optional=False):
    if name:
        value = os.getenv(name, "")
        if not value:
            raise ValueError("Указанная переменная пароля не задана.")
    else:
        value = getpass(prompt)
        if optional and not value:
            return ""
        if getpass("Повторите пароль: ") != value:
            raise ValueError("Пароли не совпадают.")
    if not 12 <= len(value) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов.")
    if "\n" in value or "\r" in value or "'" in value or "\\" in value:
        raise ValueError("Для .env не используйте одинарную кавычку, обратный слеш и перенос строки.")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password-env", help="Имя переменной процесса с паролем администратора.")
    parser.add_argument("--demo-password-env", help="Имя переменной процесса с паролем demo-пользователя; без флага пароль будет запрошен.")
    parser.add_argument("--no-demo", action="store_true", help="Не создавать пароль для demo-пользователя.")
    args = parser.parse_args()
    destination = ROOT / ".env"
    if destination.exists():
        parser.error(".env уже существует. Файл не изменён. Отредактируйте его вручную или сохраните под другим именем.")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,64}", args.admin_username):
        parser.error("Логин: 3–64 латинских символа, цифры, _, -, ., @.")
    try:
        admin_password = password_from(args.admin_password_env, "Пароль администратора (12+ символов): ")
        demo_password = "" if args.no_demo else password_from(args.demo_password_env, "Пароль demo-пользователя (Enter — не создавать): ", optional=True)
    except ValueError as exc:
        parser.error(str(exc))
    lines = ["# Локальные секреты. Не добавляйте этот файл в Git.",
             "ADMIN_USERNAME='" + args.admin_username + "'",
             "ADMIN_PASSWORD_HASH='" + hash_password(admin_password) + "'",
             "APP_SECRET='" + secrets.token_hex(32) + "'",
             "APP_ORIGIN=http://127.0.0.1:8000", "APP_ALLOWED_ORIGINS=", "TIMEZONE=Europe/Moscow",
             "DATABASE_PATH=data/invoice_keeper.db", "COOKIE_SECURE=false", "SESSION_HOURS=12",
             "TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=", "DEMO_USER_PASSWORD='" + demo_password + "'", ""]
    # Exclusive create protects an existing secret file, even if another process races.
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines))
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    print("Создан .env. Секреты не выводятся. Заполните Telegram при необходимости; подробности в README.md.")


if __name__ == "__main__":
    main()
