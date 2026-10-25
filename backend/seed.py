"""Synthetic regular expenses: rent, utilities and subscriptions.

Only an empty financial database receives these examples. Existing recurring
payments and historical snapshots are never renamed by this seed command.
"""
from datetime import timedelta
import os
import sys

from .config import load_settings
from .db import audit, connect, initialize, next_user_id, transaction, utcnow
from .domain import add_recurring, materialize, schedule_reminders
from .security import hash_password


def seed(settings):
    initialize(settings)
    today = settings.today
    with connect(settings) as db, transaction(db):
        admin = db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()
        demo_password = os.getenv("DEMO_USER_PASSWORD", "")
        if demo_password and not db.execute("SELECT 1 FROM users WHERE username='demo'").fetchone():
            if not 12 <= len(demo_password) <= 256:
                raise RuntimeError("DEMO_USER_PASSWORD: требуется 12–256 символов. Данные не изменены.")
            db.execute("INSERT INTO users(id,username,full_name,password_hash,role,created_at) VALUES(?,'demo','Алексей Смирнов',?,'user',?)", (next_user_id(db), hash_password(demo_password), utcnow()))
        demo = db.execute("SELECT * FROM users WHERE username='demo'").fetchone()
        participant_id = demo["id"] if demo else admin["id"]
        if not db.execute("SELECT 1 FROM recurring_payments LIMIT 1").fetchone():
            definitions = [("Аренда офиса", "45000.00", "RUB", today - timedelta(days=5), admin["id"]),
                           ("Коммунальные услуги", "6800.00", "RUB", today + timedelta(days=2), participant_id),
                           ("Подписка на рабочие сервисы", "12.00", "USD", today + timedelta(days=6), participant_id),
                           ("Хранилище резервных копий", "4.90", "EUR", today + timedelta(days=3), admin["id"]),
                           ("Интернет и связь", "1500.00", "RUB", today + timedelta(days=9), participant_id),
                           ("Подписка на обучение", "9.90", "EUR", today - timedelta(days=2), admin["id"])]
            for name, amount, currency, due, owner_id in definitions:
                previous_month = due.month - 1 or 12
                previous_year = due.year - (due.month == 1)
                import calendar
                previous_due = due.replace(year=previous_year, month=previous_month, day=min(due.day, calendar.monthrange(previous_year, previous_month)[1]))
                recurring_id = add_recurring(db, name=name, amount=amount, currency=currency, next_due_date=due, created_by=admin["id"], owner_id=owner_id)
                db.execute("INSERT INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,is_paid,paid_at,updated_at) SELECT id,name,amount_minor,currency,?,1,?,? FROM recurring_payments WHERE id=?", (previous_due.isoformat(), previous_due.isoformat(), utcnow(), recurring_id))
                if name == "Подписка на обучение":
                    db.execute("UPDATE payment_occurrences SET is_paid=1,paid_at=? WHERE recurring_payment_id=? AND due_date=?", (due.isoformat(), recurring_id, due.isoformat()))
            audit(db, admin, "demo_seeded", "Добавлены демонстрационные регулярные расходы и история. Суммы условные, это не реальные счета.")
        materialize(db, today)
        schedule_reminders(db, today)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    seed(load_settings())
    print("Демонстрационные регулярные платежи готовы. Суммы условные и не являются реальными счетами. Пользователь demo создаётся только при заданном DEMO_USER_PASSWORD. Telegram не отправлялся.")
