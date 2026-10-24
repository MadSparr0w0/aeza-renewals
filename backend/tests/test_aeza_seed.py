"""Infrastructure demo data: isolated databases, no real settings or network."""
from datetime import date, timedelta
import socket

import pytest

from backend.config import Settings
from backend.db import connect, initialize, transaction
from backend.domain import add_recurring
from backend.seed import seed


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 24)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_PASSWORD", "")
    def forbidden(*_, **__):
        pytest.fail("The seed attempted a live network request")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    return FrozenSettings(tmp_path / "aeza-demo.sqlite3", "admin", "synthetic-admin-hash-no-login", "test-secret-" * 8)


def snapshot(settings):
    with connect(settings) as db:
        return {table: [dict(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY id")]
                for table in ("users", "recurring_payments", "payment_occurrences", "reminder_outbox", "audit_events")}


def test_aeza_seed_has_monthly_synthetic_infrastructure_and_independent_history(settings):
    seed(settings)
    state = snapshot(settings)
    definitions = state["recurring_payments"]
    assert len(definitions) == 6
    names = [row["name"] for row in definitions]
    assert any(name.startswith("VPS") for name in names)
    assert any("Хранилище" in name for name in names)
    assert any("Выделенный сервер" in name for name in names)
    assert any("Домены" in name and "ежемесячный" in name for name in names)
    assert not any("офис" in name.lower() or "графика" in name.lower() for name in names)
    assert {row["currency"] for row in definitions} == {"RUB", "USD", "EUR"}
    assert all(row["frequency"] == "monthly" for row in definitions)
    assert all(isinstance(row["amount_minor"], int) and row["amount_minor"] > 0 for row in definitions)
    periods = state["payment_occurrences"]
    assert any(not row["is_paid"] and row["due_date"] < settings.today.isoformat() for row in periods)
    assert any(not row["is_paid"] and row["due_date"] == (settings.today + timedelta(days=2)).isoformat() for row in periods)
    for definition in definitions:
        owned = [row for row in periods if row["recurring_payment_id"] == definition["id"]]
        assert any(row["due_date"] < definition["initial_due_date"] and row["is_paid"] for row in owned)
        if definition["initial_due_date"][:7] <= settings.today.strftime("%Y-%m"):
            assert any(row["due_date"] > definition["initial_due_date"] and not row["is_paid"] for row in owned)
    audit = next(row for row in state["audit_events"] if row["action"] == "demo_seeded")
    assert "Aéza" in audit["description"] and "Суммы условные" in audit["description"] and "не тарифы" in audit["description"]
    assert [row["role"] for row in state["users"]] == ["admin"]


def test_seed_is_idempotent_and_does_not_create_a_default_demo_password(settings):
    seed(settings)
    first = snapshot(settings)
    seed(settings)
    assert snapshot(settings) == first


def test_seed_never_rebrands_existing_series_or_historical_snapshots(settings):
    initialize(settings)
    with connect(settings) as db, transaction(db):
        identifier = add_recurring(db, name="Сохранённая история", amount="123.45", currency="RUB", next_due_date=settings.today)
        before = dict(db.execute("SELECT * FROM payment_occurrences WHERE recurring_payment_id=?", (identifier,)).fetchone())
    seed(settings)
    state = snapshot(settings)
    assert len(state["recurring_payments"]) == 1
    assert state["recurring_payments"][0]["name"] == "Сохранённая история"
    assert next(row for row in state["payment_occurrences"] if row["id"] == before["id"]) == before
    assert not any(row["action"] == "demo_seeded" for row in state["audit_events"])


def test_demo_user_requires_an_explicit_password(settings, monkeypatch):
    monkeypatch.setenv("DEMO_USER_PASSWORD", "Synthetic demo password 2026!")
    seed(settings)
    users = snapshot(settings)["users"]
    demo = next(row for row in users if row["username"] == "demo")
    assert demo["role"] == "user" and demo["env_managed"] == 0
    assert demo["password_hash"].startswith("scrypt$16384$8$5$")
    assert "Synthetic demo password" not in demo["password_hash"]
