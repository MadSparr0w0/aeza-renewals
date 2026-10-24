"""Future-condition changes use isolated databases and never contact Telegram."""
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import connect, transaction, utcnow
from backend.domain import add_recurring, materialize
from backend.security import hash_password

ADMIN_PASSWORD = "Synthetic administrator password 2026!"
USER_PASSWORD = "Synthetic ordinary user password 2026!"
ADMIN_HASH = hash_password(ADMIN_PASSWORD)
CHANGED = {"name": "Новые условия", "amount": "250.29", "currency": "USD"}


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 24)


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("The API test attempted a live HTTP request")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


@pytest.fixture
def settings(tmp_path):
    return FrozenSettings(tmp_path / "recurring-update.sqlite3", "administrator", ADMIN_HASH,
                          "synthetic-recurring-secret-" * 3, origin="http://testserver")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as result:
        yield result


def login(client, username="administrator", password=ADMIN_PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": "http://testserver"}


def seed_series(settings, *, horizon=date(2027, 1, 31), paid_future=False):
    with connect(settings) as db, transaction(db):
        identifier = add_recurring(db, name="Старые условия", amount="100.00", currency="RUB",
                                   next_due_date=date(2026, 8, 31))
        materialize(db, settings.today, horizon)
        if paid_future:
            db.execute("""UPDATE payment_occurrences SET is_paid=1,paid_at='2026-10-20'
              WHERE recurring_payment_id=? AND due_date='2026-12-31'""", (identifier,))
    return identifier


def snapshot(settings, identifier):
    with connect(settings) as db:
        definition = dict(db.execute("SELECT * FROM recurring_payments WHERE id=?", (identifier,)).fetchone())
        occurrences = {row["id"]: dict(row) for row in db.execute(
            "SELECT * FROM payment_occurrences WHERE recurring_payment_id=? ORDER BY due_date", (identifier,))}
        outbox = [dict(row) for row in db.execute("SELECT * FROM reminder_outbox ORDER BY id")]
        changes = [dict(row) for row in db.execute("SELECT * FROM audit_events WHERE action='payment_updated' ORDER BY id")]
    return definition, occurrences, outbox, changes


def add_outbox(settings, identifier, due_date, state):
    with connect(settings) as db, transaction(db):
        occurrence = db.execute("""SELECT id FROM payment_occurrences
          WHERE recurring_payment_id=? AND due_date=?""", (identifier, due_date)).fetchone()[0]
        db.execute("""INSERT INTO reminder_outbox
          (occurrence_id,state,attempts,next_attempt_at,lease_owner,lease_until,sent_at,last_error,created_at)
          VALUES(?,?,2,123,'synthetic-owner',9999,?,'synthetic-diagnostic',?)""",
                   (occurrence, state, utcnow() if state == "sent" else None, utcnow()))


def test_future_edit_preserves_history_paid_periods_and_schedule_and_generates_new_defaults(client, settings):
    headers = login(client)
    identifier = seed_series(settings, paid_future=True)
    unrelated = seed_series(settings)
    unrelated_before = snapshot(settings, unrelated)[:2]
    before_definition, before_periods, _, _ = snapshot(settings, identifier)
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED)
    assert response.status_code == 200, response.text
    assert response.json() | CHANGED == response.json()
    assert response.json()["effective_from"] == "2026-11-01"

    definition, periods, _, changes = snapshot(settings, identifier)
    assert snapshot(settings, unrelated)[:2] == unrelated_before
    assert definition == before_definition | {"name": CHANGED["name"], "amount_minor": 25029, "currency": "USD"}
    assert periods.keys() == before_periods.keys()
    for key, before in before_periods.items():
        current = periods[key]
        if before["due_date"] < "2026-11-01" or before["is_paid"]:
            assert current == before
        else:
            assert (current["name"], current["amount_minor"], current["currency"]) == ("Новые условия", 25029, "USD")
            for field in ("id", "recurring_payment_id", "due_date", "is_paid", "paid_at"):
                assert current[field] == before[field]
    assert len(changes) == 1
    assert "Новые условия" in changes[0]["description"] and "2026-11-01" in changes[0]["description"]
    assert "100.00 RUB → 250.29 USD" in changes[0]["description"]

    # Periods beyond the saved cursor inherit the new defaults and the original 31st.
    with connect(settings) as db, transaction(db):
        materialize(db, settings.today, date(2027, 3, 31))
        generated = [dict(row) for row in db.execute("""SELECT * FROM payment_occurrences
          WHERE recurring_payment_id=? AND due_date>='2027-02-01' ORDER BY due_date""", (identifier,))]
    assert [row["due_date"] for row in generated] == ["2027-02-28", "2027-03-31"]
    assert all((row["name"], row["amount_minor"], row["currency"], row["is_paid"]) ==
               ("Новые условия", 25029, "USD", 0) for row in generated)


def test_outbox_states_and_delivery_history_are_not_reset(client, settings):
    headers = login(client)
    identifier = seed_series(settings, horizon=date(2027, 3, 31))
    for due, state in zip(("2026-11-30", "2026-12-31", "2027-01-31", "2027-02-28", "2027-03-31"),
                          ("pending", "retry", "sent", "unknown", "cancelled")):
        add_outbox(settings, identifier, due, state)
    before = snapshot(settings, identifier)[2]
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED)
    assert response.status_code == 200, response.text
    assert snapshot(settings, identifier)[2] == before


def test_sending_future_reminder_conflict_is_atomic(client, settings):
    headers = login(client)
    identifier = seed_series(settings)
    add_outbox(settings, identifier, "2026-11-30", "sending")
    before = snapshot(settings, identifier)
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED)
    assert response.status_code == 409 and "отправляется" in response.json()["detail"]
    assert snapshot(settings, identifier) == before


@pytest.mark.parametrize("due_date,paid_future", [("2026-10-31", False), ("2026-12-31", True)])
def test_sending_period_outside_the_updated_set_does_not_block(client, settings, due_date, paid_future):
    headers = login(client)
    identifier = seed_series(settings, paid_future=paid_future)
    add_outbox(settings, identifier, due_date, "sending")
    before = snapshot(settings, identifier)
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED)
    assert response.status_code == 200, response.text
    after = snapshot(settings, identifier)
    assert after[2] == before[2]
    unaffected = next(key for key, row in before[1].items() if row["due_date"] == due_date)
    assert after[1][unaffected] == before[1][unaffected]


def test_archived_and_missing_series_are_rejected_without_mutation(client, settings):
    headers = login(client)
    identifier = seed_series(settings)
    with connect(settings) as db, transaction(db):
        db.execute("UPDATE recurring_payments SET active=0 WHERE id=?", (identifier,))
    before = snapshot(settings, identifier)
    assert client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED).status_code == 409
    assert client.patch("/api/v1/recurring-payments/999999", headers=headers, json=CHANGED).status_code == 404
    assert snapshot(settings, identifier) == before


def test_canonical_noop_writes_nothing_even_with_an_inflight_future_reminder(client, settings):
    headers = login(client)
    identifier = seed_series(settings)
    add_outbox(settings, identifier, "2026-11-30", "sending")
    before = snapshot(settings, identifier)
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers,
                            json={"name": "  Старые условия  ", "amount": "100.0", "currency": "RUB"})
    assert response.status_code == 200 and response.json()["amount"] == "100.00"
    assert snapshot(settings, identifier) == before


def test_stale_cursor_conflict_preserves_old_defaults_until_list_refresh(client, settings):
    headers = login(client)
    with connect(settings) as db, transaction(db):
        identifier = add_recurring(db, name="Старые условия", amount="100.00", currency="RUB",
                                   next_due_date=date(2026, 8, 31))
    before = snapshot(settings, identifier)
    assert before[0]["next_materialize_date"] == "2026-09-30"
    assert len(before[1]) == 1
    path = f"/api/v1/recurring-payments/{identifier}"

    response = client.patch(path, headers=headers, json=CHANGED)
    assert response.status_code == 409
    assert response.json()["detail"] == "Сначала обновите список платежей, затем повторите изменение условий."
    assert snapshot(settings, identifier) == before
    no_change = client.patch(path, headers=headers,
                             json={"name": "Старые условия", "amount": 100, "currency": "RUB"})
    assert no_change.status_code == 200
    assert snapshot(settings, identifier) == before

    # The ordinary list refresh generates missed history under the OLD defaults.
    assert client.get("/api/v1/recurring-payments").status_code == 200
    refreshed = snapshot(settings, identifier)
    assert [row["due_date"] for row in refreshed[1].values()] == [
        "2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30"]
    assert all((row["name"], row["amount_minor"], row["currency"]) == ("Старые условия", 10000, "RUB")
               for row in refreshed[1].values())

    response = client.patch(path, headers=headers, json=CHANGED)
    assert response.status_code == 200, response.text
    after = snapshot(settings, identifier)
    assert after[0]["next_materialize_date"] == refreshed[0]["next_materialize_date"]
    assert after[2] == refreshed[2]
    for key, row in refreshed[1].items():
        if row["due_date"] < "2026-11-01":
            assert after[1][key] == row
        else:
            assert (after[1][key]["name"], after[1][key]["amount_minor"], after[1][key]["currency"]) == (
                "Новые условия", 25029, "USD")
    assert len(after[3]) == 1


def test_authentication_csrf_origin_and_server_role_are_required(client, settings):
    identifier = seed_series(settings)
    path = f"/api/v1/recurring-payments/{identifier}"
    assert client.patch(path, json=CHANGED).status_code == 401
    admin_headers = login(client)
    assert client.patch(path, json=CHANGED).status_code == 403
    assert client.patch(path, headers=admin_headers | {"Origin": "https://foreign.example"}, json=CHANGED).status_code == 403
    created = client.post("/api/v1/users", headers=admin_headers,
                          json={"username": "operator", "full_name": "Оператор", "password": USER_PASSWORD})
    assert created.status_code == 201, created.text
    ordinary_headers = login(client, "operator", USER_PASSWORD)
    before = snapshot(settings, identifier)
    assert client.patch(path, headers=ordinary_headers, json=CHANGED).status_code == 403
    assert snapshot(settings, identifier) == before


@pytest.mark.parametrize("body", [
    {"name": "Новое", "amount": "0", "currency": "RUB"},
    {"name": "Новое", "amount": "0.001", "currency": "RUB"},
    {"name": "Новое", "amount": "NaN", "currency": "RUB"},
    {"name": "Новое", "amount": "1000000000.00", "currency": "RUB"},
    {"name": "Новое", "amount": "100", "currency": "GBP"},
    {"name": " ", "amount": "100", "currency": "RUB"},
    {"name": "Новое", "currency": "RUB"},
    CHANGED | {"next_due_date": "2026-11-30"},
    CHANGED | {"anchor_day": 15},
    CHANGED | {"frequency": "monthly"},
    CHANGED | {"active": True},
])
def test_invalid_or_extra_fields_are_rejected_atomically(client, settings, body):
    headers = login(client)
    identifier = seed_series(settings)
    before = snapshot(settings, identifier)
    response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=body)
    assert response.status_code == 422, response.text
    assert all(any("А" <= character <= "я" or character in "Ёё" for character in error["msg"])
               for error in response.json()["detail"])
    assert snapshot(settings, identifier) == before


def test_openapi_has_only_the_three_required_edit_fields(client):
    schema = client.app.openapi()
    operation = schema["paths"]["/api/v1/recurring-payments/{payment_id}"]["patch"]
    reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    model = schema["components"]["schemas"][reference]
    assert set(model["required"]) == set(model["properties"]) == {"name", "amount", "currency"}
    assert model["additionalProperties"] is False
    assert operation["security"] == [{"sessionCookie": [], "csrfHeader": []}]


def test_effective_date_rolls_over_the_year_without_changing_the_current_period(tmp_path):
    class YearEndSettings(Settings):
        @property
        def today(self):
            return date(2026, 12, 31)

    cfg = YearEndSettings(tmp_path / "year-end.sqlite3", "administrator", ADMIN_HASH,
                          "synthetic-year-end-secret-" * 3, origin="http://testserver")
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        created = client.post("/api/v1/recurring-payments", headers=headers,
                              json={"name": "Декабрь", "amount": "1.00", "currency": "RUB", "next_due_date": "2026-12-31"})
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        before = snapshot(cfg, identifier)[1]
        response = client.patch(f"/api/v1/recurring-payments/{identifier}", headers=headers, json=CHANGED)
        assert response.status_code == 200 and response.json()["effective_from"] == "2027-01-01"
        after = snapshot(cfg, identifier)[1]
        current = next(key for key, row in before.items() if row["due_date"] == "2026-12-31")
        assert after[current] == before[current]
