"""Synthetic Aéza infrastructure-renewal examples, never official tariffs.

Only an empty financial database receives these examples. Existing recurring
payments and historical snapshots are never renamed by this seed command.
"""
from datetime import timedelta
import os
import sys

from .config import load_settings
from .db import audit, connect, initialize, transaction, utcnow
from .domain import add_recurring, materialize, schedule_reminders
from .security import hash_password


def seed(settings):
    initialize(settings)
    today = settings.today
    with connect(settings) as db, transaction(db):
        admin = db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()
        if not db.execute("SELECT 1 FROM recurring_payments LIMIT 1").fetchone():
            # Conditional monthly amounts for the case, not a provider price
            # catalogue. Domain costs also use the case's monthly accounting.
            definitions = [("VPS · основной проект", "1490.00", "RUB", today - timedelta(days=5)),
                           ("VPS · тестовая среда", "12.00", "USD", today + timedelta(days=2)),
                           ("Хранилище резервных копий", "4.90", "EUR", today + timedelta(days=6)),
                           ("Выделенный сервер · обработка", "8990.00", "RUB", today + timedelta(days=3)),
                           ("Домены · ежемесячный учёт", "9.00", "USD", today + timedelta(days=9)),
                           ("Резервное копирование · проект", "3.50", "EUR", today - timedelta(days=2))]
            for name, amount, currency, due in definitions:
                previous_month = due.month - 1 or 12
                previous_year = due.year - (due.month == 1)
                import calendar
                previous_due = due.replace(year=previous_year, month=previous_month, day=min(due.day, calendar.monthrange(previous_year, previous_month)[1]))
                recurring_id = add_recurring(db, name=name, amount=amount, currency=currency, next_due_date=due, created_by=admin["id"])
                db.execute("INSERT INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,is_paid,paid_at,updated_at) SELECT id,name,amount_minor,currency,?,1,?,? FROM recurring_payments WHERE id=?", (previous_due.isoformat(), previous_due.isoformat(), utcnow(), recurring_id))
                if name == "Резервное копирование · проект":
                    db.execute("UPDATE payment_occurrences SET is_paid=1,paid_at=? WHERE recurring_payment_id=? AND due_date=?", (due.isoformat(), recurring_id, due.isoformat()))
            audit(db, admin, "demo_seeded", "Aéza: добавлены демонстрационные продления и история. Суммы условные, это не тарифы провайдера.")
        demo_password = os.getenv("DEMO_USER_PASSWORD", "")
        if demo_password and not db.execute("SELECT 1 FROM users WHERE username='demo'").fetchone():
            if not 12 <= len(demo_password) <= 256:
                raise RuntimeError("DEMO_USER_PASSWORD: требуется 12–256 символов. Данные не изменены.")
            db.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES('demo','Алексей Смирнов',?,'user',?)", (hash_password(demo_password), utcnow()))
        materialize(db, today)
        schedule_reminders(db, today)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    seed(load_settings())
    print("Демонстрационные продления Aéza готовы. Суммы условные и не являются тарифами провайдера. Пользователь demo создаётся только при заданном DEMO_USER_PASSWORD. Telegram не отправлялся.")
