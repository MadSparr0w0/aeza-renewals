"""Ownership, legacy migration and one-use signup. Synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.db import SCHEMA, connect, initialize, transaction, utcnow
from backend.security import hash_password

PASSWORD = "Synthetic participants password!"
ENCODED = hash_password(PASSWORD)


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 24)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("A backend test attempted a real HTTP request")
    # Windows asyncio uses a local socketpair for its event loop. Blocking the
    # external HTTP transport keeps ASGI tests offline without breaking it.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


@pytest.fixture
def cfg(tmp_path):
    return FrozenSettings(tmp_path / "isolated.sqlite", "admin", ENCODED, "synthetic-secret-" * 5)


def login(client, username, password=PASSWORD):
    result = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert result.status_code == 200, result.text
    return {"X-CSRF-Token": result.json()["csrf_token"], "Origin": "http://127.0.0.1:8000"}


@pytest.fixture
def team(cfg):
    initialize(cfg)
    with connect(cfg) as db, transaction(db):
        ids = {"admin": db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()[0]}
        for username, name in (("alice", "Алиса"), ("boris", "Борис")):
            ids[username] = db.execute("INSERT INTO users(username,full_name,password_hash,role,created_at,team_id) VALUES(?,?,?,'user',?,?)", (username, name, ENCODED, utcnow(), ids["admin"])).lastrowid
    with ExitStack() as stack:
        clients = {name: stack.enter_context(TestClient(create_app(cfg))) for name in ids}
        headers = {name: login(client, name) for name, client in clients.items()}
        yield clients, headers, ids


def create(client, headers, *, name="Расход", amount="100.00", owner_id=None):
    body = {"name": name, "amount": amount, "currency": "RUB", "next_due_date": "2026-10-31"}
    if owner_id is not None:
        body["owner_id"] = owner_id
    response = client.post("/api/v1/recurring-payments", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def invite(client, headers, username="invited"):
    response = client.post("/api/v1/invitations", json={"username": username, "full_name": "Приглашённый участник"}, headers=headers)
    assert response.status_code == 201, response.text
    result = response.json()
    return result, result["invitation_url"].split("#invite=", 1)[1]


def test_two_users_have_isolated_lists_search_summaries_and_direct_ids(team):
    clients, headers, ids = team
    alice = create(clients["alice"], headers["alice"], name="Личный расход Алисы", amount="10.10")
    boris = create(clients["boris"], headers["boris"], name="Личный расход Бориса", amount="20.20")
    for username, owned, other in (("alice", alice, boris), ("boris", boris, alice)):
        client = clients[username]
        assert [r["id"] for r in client.get("/api/v1/recurring-payments").json()] == [owned["id"]]
        rows = client.get("/api/v1/payment-occurrences?month=2026-10").json()
        assert {r["recurring_payment_id"] for r in rows} == {owned["id"]}
        assert all(r["owner_id"] == ids[username] and r["can_manage"] for r in rows)
        assert client.get("/api/v1/payment-occurrences?month=2026-10&search=" + other["name"]).json() == []
        sums = client.get("/api/v1/payments/summary?month=2026-10").json()
        assert sums["currencies"]["RUB"]["planned"] == owned["amount"]
        assert sums["total_count"] == 1
    all_rows = clients["admin"].get("/api/v1/payment-occurrences?month=2026-10").json()
    assert {r["owner_id"] for r in all_rows} == {ids["alice"], ids["boris"]}
    other_period = next(r for r in all_rows if r["owner_id"] == ids["boris"])
    assert clients["alice"].get(f"/api/v1/payment-occurrences/{other_period['id']}").status_code == 404
    assert clients["alice"].patch(f"/api/v1/payment-occurrences/{other_period['id']}", json={"is_paid": True}, headers=headers["alice"]).status_code == 404
    assert clients["alice"].patch(f"/api/v1/payment-occurrences/{other_period['id']}", json={}, headers=headers["alice"]).status_code == 404
    changed = {"name": "Подмена", "amount": "1.00", "currency": "EUR"}
    assert clients["alice"].patch(f"/api/v1/recurring-payments/{boris['id']}", json=changed, headers=headers["alice"]).status_code == 404
    assert clients["alice"].delete(f"/api/v1/recurring-payments/{boris['id']}", headers=headers["alice"]).status_code == 404
    admin_sums = clients["admin"].get("/api/v1/payments/summary?month=2026-10").json()
    assert admin_sums["currencies"]["RUB"]["planned"] == "30.30"


def test_owner_cannot_be_spoofed_and_admin_assignment_preserves_author(team, cfg):
    clients, headers, ids = team
    assigned = create(clients["admin"], headers["admin"], owner_id=ids["boris"])
    assert assigned["owner_id"] == ids["boris"] and assigned["owner_name"] == "Борис" and assigned["can_manage"] is True
    with connect(cfg) as db:
        row = db.execute("SELECT owner_id,created_by FROM recurring_payments WHERE id=?", (assigned["id"],)).fetchone()
        assert tuple(row) == (ids["boris"], ids["admin"])
    body = {"name": "Подмена", "amount": "1.00", "currency": "RUB", "next_due_date": "2026-10-24", "owner_id": ids["boris"]}
    assert clients["alice"].post("/api/v1/recurring-payments", json=body, headers=headers["alice"]).status_code == 403
    body["owner_id"] = ids["alice"]
    body["created_by"] = ids["boris"]
    assert clients["alice"].post("/api/v1/recurring-payments", json=body, headers=headers["alice"]).status_code == 422
    del body["created_by"]
    body["owner_id"] = 99999
    assert clients["admin"].post("/api/v1/recurring-payments", json=body, headers=headers["admin"]).status_code == 422


def test_owner_can_edit_future_mark_correct_cancel_and_archive(team):
    clients, headers, ids = team
    client, auth = clients["alice"], headers["alice"]
    owned = create(client, auth)
    periods = client.get("/api/v1/payment-occurrences?month=2026-11").json()
    future = next(row for row in periods if row["recurring_payment_id"] == owned["id"])
    current = client.get("/api/v1/payment-occurrences?month=2026-10").json()[0]
    changed = client.patch(f"/api/v1/recurring-payments/{owned['id']}", json={"name": "Новые условия", "amount": "150.00", "currency": "EUR"}, headers=auth)
    assert changed.status_code == 200 and changed.json()["effective_from"] == "2026-11-01"
    assert client.get(f"/api/v1/payment-occurrences/{current['id']}").json()["amount"] == "100.00"
    assert client.get(f"/api/v1/payment-occurrences/{future['id']}").json()["amount"] == "150.00"
    marked = client.patch(f"/api/v1/payment-occurrences/{current['id']}", json={"is_paid": True, "paid_at": "2026-10-23"}, headers=auth)
    assert marked.status_code == 200 and marked.json()["paid_at"] == "2026-10-23"
    corrected = client.patch(f"/api/v1/payment-occurrences/{current['id']}", json={"paid_at": "2026-10-24"}, headers=auth)
    assert corrected.json()["paid_at"] == "2026-10-24"
    undone = client.patch(f"/api/v1/payment-occurrences/{current['id']}", json={"is_paid": False}, headers=auth)
    assert undone.json()["paid_at"] is None and undone.json()["is_paid"] is False
    assert client.delete(f"/api/v1/recurring-payments/{owned['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/recurring-payments").json() == []
    assert client.get(f"/api/v1/payment-occurrences/{current['id']}").json()["recurring_active"] is False
    assert client.get("/api/v1/recurring-payments?include_inactive=true").json()[0]["owner_id"] == ids["alice"]


def test_deleting_user_transfers_ownership_without_rewriting_author_or_history(team, cfg):
    clients, headers, ids = team
    owned = create(clients["boris"], headers["boris"])
    with connect(cfg) as db:
        before = [dict(row) for row in db.execute("SELECT * FROM payment_occurrences WHERE recurring_payment_id=? ORDER BY id", (owned["id"],))]
    response = clients["admin"].delete(f"/api/v1/users/{ids['boris']}", headers=headers["admin"])
    assert response.status_code == 204
    with connect(cfg) as db:
        series = db.execute("SELECT owner_id,created_by FROM recurring_payments WHERE id=?", (owned["id"],)).fetchone()
        assert tuple(series) == (ids["admin"], ids["boris"])
        after = [dict(row) for row in db.execute("SELECT * FROM payment_occurrences WHERE recurring_payment_id=? ORDER BY id", (owned["id"],))]
        assert after == before
        event = db.execute("SELECT description FROM audit_events WHERE action='user_deleted'").fetchone()[0]
        assert "передано платежей администратору: 1" in event
    assert clients["boris"].get("/api/v1/auth/me").status_code == 401
    transferred = clients["admin"].get("/api/v1/recurring-payments").json()[0]
    assert transferred["owner_id"] == ids["admin"]
    assert clients["alice"].get("/api/v1/recurring-payments").json() == []
    replacement = clients["admin"].post("/api/v1/users", json={"username": "replacement", "full_name": "Новый участник", "password": PASSWORD}, headers=headers["admin"])
    assert replacement.status_code == 201 and replacement.json()["id"] > ids["boris"]
    with connect(cfg) as db:
        assert db.execute("SELECT created_by FROM recurring_payments WHERE id=?", (owned["id"],)).fetchone()[0] == ids["boris"]


def test_archive_keeps_confirmed_ambiguous_and_inflight_delivery_history(team, cfg):
    clients, headers, _ = team
    owned = create(clients["alice"], headers["alice"])
    clients["alice"].get("/api/v1/payment-occurrences?month=2027-01")
    states = ("pending", "sent", "unknown", "sending")
    with connect(cfg) as db, transaction(db):
        periods = db.execute("SELECT id FROM payment_occurrences WHERE recurring_payment_id=? ORDER BY due_date", (owned["id"],)).fetchall()
        for period, state in zip(periods, states):
            db.execute("INSERT INTO reminder_outbox(occurrence_id,state,attempts,lease_owner,lease_until,last_error,created_at) VALUES(?,?,1,'existing',9999999999,'existing-result',?)", (period["id"], state, utcnow()))
    assert clients["alice"].delete(f"/api/v1/recurring-payments/{owned['id']}", headers=headers["alice"]).status_code == 204
    with connect(cfg) as db:
        rows = db.execute("SELECT state,last_error,lease_owner FROM reminder_outbox ORDER BY id").fetchall()
        assert tuple(rows[0]) == ("cancelled", "payment_not_required", None)
        assert [tuple(row) for row in rows[1:]] == [(state, "existing-result", "existing") for state in states[1:]]


def test_legacy_ownership_migration_preserves_ids_history_creators_and_delivery(cfg):
    legacy = SCHEMA.replace(" owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,\n", "")
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cfg.database_path) as db:
        db.executescript(legacy)
        db.executemany("INSERT INTO users(id,username,full_name,password_hash,role,env_managed,created_at) VALUES(?,?,?,?,?,?,?)", [(1,"admin","Администратор",ENCODED,"admin",1,utcnow()), (2,"legacy","Участник",ENCODED,"user",0,utcnow())])
        for identifier, creator in ((10, 2), (11, None), (12, 777)):
            db.execute("""INSERT INTO recurring_payments(id,name,amount_minor,currency,initial_due_date,next_materialize_date,anchor_day,created_at,created_by)
              VALUES(?, 'Старая серия',12345,'RUB','2026-01-31','2026-03-31',31,?,?)""", (identifier, utcnow(), creator))
            db.execute("INSERT INTO payment_occurrences(id,recurring_payment_id,name,amount_minor,currency,due_date,is_paid,paid_at,updated_at) VALUES(?,?,'Старый снимок',12345,'RUB','2026-01-31',1,'2026-02-02',?)", (identifier + 100, identifier, utcnow()))
        db.execute("INSERT INTO reminder_outbox(id,occurrence_id,state,attempts,sent_at,created_at) VALUES(70,110,'sent',2,?,?)", (utcnow(), utcnow()))
        db.execute("INSERT INTO reminder_outbox(id,occurrence_id,state,attempts,last_error,created_at) VALUES(71,111,'unknown',1,'telegram_delivery_unknown',?)", (utcnow(),))
        before = list(db.execute("SELECT * FROM payment_occurrences ORDER BY id"))
    initialize(cfg)
    initialize(cfg)
    with connect(cfg) as db:
        assert [tuple(row) for row in db.execute("SELECT id,owner_id,created_by FROM recurring_payments ORDER BY id")] == [(10,2,2),(11,1,None),(12,1,777)]
        assert [tuple(row) for row in db.execute("SELECT * FROM payment_occurrences ORDER BY id")] == before
        assert [tuple(row) for row in db.execute("SELECT id,state,attempts FROM reminder_outbox ORDER BY id")] == [(70,"sent",2),(71,"unknown",1)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE recurring_payments SET owner_id=NULL WHERE id=10")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE recurring_payments SET owner_id=99999 WHERE id=10")


def test_invitation_is_hashed_private_one_use_and_replaces_browser_session(team, cfg):
    clients, headers, ids = team
    invitation, token = invite(clients["admin"], headers["admin"])
    assert invitation["status"] == "active" and invitation["expires_at"].endswith("+00:00")
    listed = clients["admin"].get("/api/v1/invitations").json()
    assert "invitation_url" not in listed[0] and "token_hash" not in listed[0] and token not in json.dumps(listed)
    with connect(cfg) as db:
        stored = db.execute("SELECT token_hash FROM invitations WHERE id=?", (invitation["id"],)).fetchone()[0]
        assert stored != token and len(stored) == 64
        assert token not in str([dict(row) for row in db.execute("SELECT * FROM audit_events")])
    client = clients["alice"]
    old_cookie = client.cookies["ik_session"]
    inspected = client.post("/api/v1/invitations/inspect", json={"token": token}, headers={"Origin": cfg.origin})
    assert inspected.status_code == 200 and set(inspected.json()) == {"username", "full_name", "expires_at"}
    accepted = client.post("/api/v1/invitations/accept", json={"token": token, "password": PASSWORD}, headers={"Origin": cfg.origin})
    assert accepted.status_code == 200 and accepted.json()["user"]["role"] == "user"
    assert accepted.json()["csrf_token"] and client.cookies["ik_session"] != old_cookie
    assert client.get("/api/v1/auth/me").json()["user"]["username"] == "invited"
    assert client.get("/api/v1/payment-occurrences").json() == []
    with TestClient(create_app(cfg)) as old_browser:
        old_browser.cookies.set("ik_session", old_cookie)
        assert old_browser.get("/api/v1/auth/me").status_code == 401
    for endpoint in ("inspect", "accept"):
        body = {"token": token, **({"password": PASSWORD} if endpoint == "accept" else {})}
        assert client.post("/api/v1/invitations/" + endpoint, json=body, headers={"Origin": cfg.origin}).status_code == 404
    assert clients["admin"].get("/api/v1/invitations").json()[0]["status"] == "accepted"


def test_invitation_accept_is_atomic_under_concurrent_reuse(team, cfg):
    clients, headers, _ = team
    _, token = invite(clients["admin"], headers["admin"], "race_signup")
    def accept_once(_):
        with TestClient(create_app(cfg)) as client:
            return client.post("/api/v1/invitations/accept", json={"token": token, "password": PASSWORD}, headers={"Origin": cfg.origin}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept_once, range(2))) == [200, 404]
    with connect(cfg) as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE username='race_signup'").fetchone()[0] == 1


def test_invitation_expiry_revocation_reservations_and_permissions(team, cfg):
    clients, headers, _ = team
    expired, token = invite(clients["admin"], headers["admin"], "expired_signup")
    with connect(cfg) as db:
        db.execute("UPDATE invitations SET expires_at=? WHERE id=?", (int(time.time()) - 1, expired["id"]))
    assert clients["alice"].post("/api/v1/invitations/inspect", json={"token": token}, headers={"Origin": cfg.origin}).status_code == 404
    assert clients["alice"].post("/api/v1/invitations/accept", json={"token": token, "password": PASSWORD}, headers={"Origin": cfg.origin}).status_code == 404
    pending, token = invite(clients["admin"], headers["admin"], "reserved_signup")
    assert clients["admin"].post("/api/v1/users", json={"username": "reserved_signup", "full_name": "Подмена", "password": PASSWORD}, headers=headers["admin"]).status_code == 409
    assert clients["alice"].get("/api/v1/invitations").status_code == 403
    assert clients["alice"].post("/api/v1/invitations", json={"username": "forbidden", "full_name": "Запрещено"}, headers=headers["alice"]).status_code == 403
    assert clients["alice"].delete(f"/api/v1/invitations/{pending['id']}", headers=headers["alice"]).status_code == 403
    assert clients["admin"].delete(f"/api/v1/invitations/{pending['id']}", headers=headers["admin"]).status_code == 204
    assert clients["alice"].post("/api/v1/invitations/accept", json={"token": token, "password": PASSWORD}, headers={"Origin": cfg.origin}).status_code == 404
    assert {r["status"] for r in clients["admin"].get("/api/v1/invitations").json()} == {"expired", "revoked"}


def test_public_invitation_requires_exact_origin_and_bounds_attempts_without_echo(team, cfg):
    clients, _, _ = team
    client, token = clients["alice"], "A" * 43
    for path in ("inspect", "accept"):
        body = {"token": token, **({"password": PASSWORD} if path == "accept" else {})}
        for extra in ({}, {"Origin": "http://untrusted.invalid"}):
            assert client.post("/api/v1/invitations/" + path, json=body, headers=extra).status_code == 403
    for _ in range(10):
        assert client.post("/api/v1/invitations/inspect", json={"token": token}, headers={"Origin": cfg.origin}).status_code == 404
    limited = client.post("/api/v1/invitations/inspect", json={"token": token}, headers={"Origin": cfg.origin})
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "600" and token not in limited.text
    invalid = client.post("/api/v1/invitations/accept", json={"token": token, "password": "short-secret", "role": "admin"}, headers={"Origin": cfg.origin})
    assert invalid.status_code == 422 and token not in invalid.text and "short-secret" not in invalid.text


def test_own_password_change_requires_current_password_and_revokes_all_sessions(team, cfg):
    clients, headers, _ = team
    client, auth = clients["alice"], headers["alice"]
    new_password = "A different synthetic password!"
    with TestClient(create_app(cfg)) as second:
        login(second, "alice")
        wrong = client.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": new_password}, headers=auth)
        assert wrong.status_code == 400 and client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": new_password}).status_code == 403
        changed = client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": new_password}, headers=auth)
        assert changed.status_code == 204 and "ik_session" not in client.cookies
        assert second.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    login(client, "alice", new_password)
    denied = clients["admin"].post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": new_password}, headers=headers["admin"])
    assert denied.status_code == 403 and clients["admin"].get("/api/v1/auth/me").status_code == 200
