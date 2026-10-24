import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .db import utcnow

CURRENCIES = ("RUB", "USD", "EUR")


def next_month(value: date, anchor_day: int) -> date:
    year = value.year + (value.month == 12)
    month = value.month % 12 + 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def amount_minor(value) -> int:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal("999999999.99"):
            raise ValueError("Сумма должна быть от 0.01 до 999999999.99.")
        if number != number.quantize(Decimal("0.01")):
            raise ValueError("У суммы может быть не более двух знаков после запятой.")
        return int(number * 100)
    except (InvalidOperation, TypeError):
        raise ValueError("Некорректная сумма.") from None


def money(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


def add_recurring(db, *, name, amount, currency, next_due_date, created_by=None):
    due = date.fromisoformat(str(next_due_date))
    cursor = db.execute("INSERT INTO recurring_payments(name,amount_minor,currency,initial_due_date,next_materialize_date,anchor_day,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)",
                        (name, amount_minor(amount), currency, due.isoformat(), next_month(due, due.day).isoformat(), due.day, utcnow(), created_by))
    recurring_id = cursor.lastrowid
    db.execute("INSERT INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,updated_at) VALUES(?,?,?,?,?,?)",
               (recurring_id, name, amount_minor(amount), currency, due.isoformat(), utcnow()))
    return recurring_id


def materialize(db, today: date, horizon: date | None = None):
    horizon = max(horizon or today, month_end(next_month(today, today.day)))
    for row in db.execute("SELECT * FROM recurring_payments WHERE active=1").fetchall():
        due = date.fromisoformat(row["next_materialize_date"])
        while due <= horizon:
            db.execute("INSERT OR IGNORE INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,updated_at) VALUES(?,?,?,?,?,?)",
                       (row["id"], row["name"], row["amount_minor"], row["currency"], due.isoformat(), utcnow()))
            due = next_month(due, row["anchor_day"])
        db.execute("UPDATE recurring_payments SET next_materialize_date=? WHERE id=?", (due.isoformat(), row["id"]))


def occurrence_json(row, today):
    due = date.fromisoformat(row["due_date"])
    paid = bool(row["is_paid"])
    until = date.fromisoformat(row["paid_at"]) if paid else today
    late = max(0, (until - due).days)
    return {"id": row["id"], "recurring_payment_id": row["recurring_payment_id"],
            "name": row["name"], "amount": money(row["amount_minor"]), "currency": row["currency"],
            "due_date": row["due_date"], "is_paid": paid, "paid_at": row["paid_at"],
            "status": "paid" if paid else ("overdue" if due < today else "pending"),
            "overdue_days": late, "late_days": late,
            "recurring_active": bool(row["recurring_active"]) if "recurring_active" in row.keys() else True}


def schedule_reminders(db, today):
    cutoff = (today + timedelta(days=3)).isoformat()
    db.execute("""INSERT OR IGNORE INTO reminder_outbox(occurrence_id,state,created_at)
      SELECT o.id,'pending',? FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id
      WHERE o.is_paid=0 AND r.active=1 AND o.due_date<=?""", (utcnow(), cutoff))
    db.execute("""UPDATE reminder_outbox SET state='cancelled',lease_owner=NULL,lease_until=NULL
      WHERE state IN ('pending','retry') AND occurrence_id IN
      (SELECT o.id FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id WHERE o.is_paid=1 OR r.active=0)""")
