import calendar
from datetime import date
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


def add_recurring(db, *, name, amount, currency, next_due_date, created_by=None, owner_id=None):
    due = date.fromisoformat(str(next_due_date))
    if owner_id is None:
        creator = db.execute("SELECT id FROM users WHERE id=?", (created_by,)).fetchone()
        owner_id = creator["id"] if creator else db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()["id"]
    cursor = db.execute("INSERT INTO recurring_payments(name,amount_minor,currency,initial_due_date,next_materialize_date,anchor_day,created_at,created_by,owner_id) VALUES(?,?,?,?,?,?,?,?,?)",
                        (name, amount_minor(amount), currency, due.isoformat(), next_month(due, due.day).isoformat(), due.day, utcnow(), created_by, owner_id))
    recurring_id = cursor.lastrowid
    db.execute("INSERT INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,updated_at) VALUES(?,?,?,?,?,?)",
               (recurring_id, name, amount_minor(amount), currency, due.isoformat(), utcnow()))
    return recurring_id


def materialize(db, today: date, horizon: date | None = None, owner_id=None, team_id=None):
    horizon = max(horizon or today, month_end(next_month(today, today.day)))
    query = "SELECT * FROM recurring_payments WHERE active=1 AND next_materialize_date<=? AND id>?" + (" AND owner_id=?" if owner_id is not None else "") + (' AND owner_id IN (SELECT id FROM users WHERE team_id=?)' if team_id is not None else '') + ' ORDER BY id LIMIT 128'
    # Never modify a table under an unfinished SELECT cursor. The keyset moves
    # through bounded batches; already materialized series need no UPDATE.
    last_id=0
    while True:
        args=[horizon.isoformat(),last_id]
        if owner_id is not None:args.append(owner_id)
        if team_id is not None:args.append(team_id)
        rows=db.execute(query,args).fetchall()
        if not rows:break
        for row in rows:
            due = date.fromisoformat(row["next_materialize_date"])
            while due <= horizon:
                db.execute("INSERT OR IGNORE INTO payment_occurrences(recurring_payment_id,name,amount_minor,currency,due_date,updated_at) VALUES(?,?,?,?,?,?)",
                           (row["id"], row["name"], row["amount_minor"], row["currency"], due.isoformat(), utcnow()))
                due = next_month(due, row["anchor_day"])
            db.execute("UPDATE recurring_payments SET next_materialize_date=? WHERE id=?", (due.isoformat(), row["id"]))
        last_id=rows[-1]['id']


def occurrence_json(row, today, viewer=None):
    due = date.fromisoformat(row["due_date"])
    paid = bool(row["is_paid"])
    until = date.fromisoformat(row["paid_at"]) if paid else today
    late = max(0, (until - due).days)
    result = {"id": row["id"], "recurring_payment_id": row["recurring_payment_id"],
            "name": row["name"], "amount": money(row["amount_minor"]), "currency": row["currency"],
            "due_date": row["due_date"], "is_paid": paid, "paid_at": row["paid_at"],
            "status": "paid" if paid else ("overdue" if due < today else "pending"),
            "overdue_days": late, "late_days": late,
            "recurring_active": bool(row["recurring_active"]) if "recurring_active" in row.keys() else True}
    if "owner_id" in row.keys():
        result.update(owner_id=row["owner_id"], owner_name=row["owner_name"],
                      can_manage=bool(viewer and (viewer["role"] == "admin" or viewer["id"] == row["owner_id"])))
    return result


def schedule_reminders(db, today, owner_id=None):
    from .telegram_connections import schedule_group_reminders
    return schedule_group_reminders(db, today, owner_id=owner_id)
