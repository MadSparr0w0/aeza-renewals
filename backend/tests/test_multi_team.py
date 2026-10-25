"""Independent administrator sessions, data boundaries and Telegram routing."""
from contextlib import ExitStack
from datetime import date
import sqlite3
import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import SCHEMA, connect, initialize, transaction
from backend.security import digest, hash_password
from backend.telegram_connections import confirm_link, record_candidate, start_link, token_scope
from backend.worker import SendResult, tick


PASSWORD = "Synthetic admin password 123!"


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 25)


def login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_administrators_have_independent_sessions_and_team_data(tmp_path):
    cfg = FrozenSettings(tmp_path / "teams.sqlite", "rootadmin", hash_password(PASSWORD),
                         "synthetic-secret-" * 5, telegram_bot_token="123456:FAKE_TEAM_TOKEN")
    initialize(cfg)
    with ExitStack() as stack:
        root = stack.enter_context(TestClient(create_app(cfg)))
        root_auth = login(root, "rootadmin")
        created = root.post("/api/v1/admins", json={"username": "secondadmin", "full_name": "Вторая команда",
                                                       "password": PASSWORD}, headers=root_auth)
        assert created.status_code == 201, created.text
        second_id = created.json()["id"]
        second = stack.enter_context(TestClient(create_app(cfg)))
        second_auth = login(second, "secondadmin")
        assert root.get("/api/v1/auth/me").status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 200
        assert second.get("/api/v1/admins").status_code == 403
        assert {u["username"] for u in root.get("/api/v1/users").json()} == {"rootadmin"}
        assert {u["username"] for u in second.get("/api/v1/users").json()} == {"secondadmin"}

        root_member = root.post("/api/v1/users", json={"username": "rootmember", "full_name": "Участник 1",
                                                         "password": PASSWORD}, headers=root_auth)
        other_member = second.post("/api/v1/users", json={"username": "othermember", "full_name": "Участник 2",
                                                            "password": PASSWORD}, headers=second_auth)
        assert root_member.status_code == other_member.status_code == 201
        root_member_id, other_member_id = root_member.json()["id"], other_member.json()["id"]
        assert second.patch(f"/api/v1/users/{root_member_id}", json={"full_name": "Подмена"}, headers=second_auth).status_code == 404
        assert root.delete(f"/api/v1/users/{other_member_id}", headers=root_auth).status_code == 404

        def payment(client, headers, name, owner_id):
            result = client.post("/api/v1/recurring-payments", json={
                "name": name, "amount": "7.00", "currency": "RUB",
                "next_due_date": "2026-10-25", "owner_id": owner_id}, headers=headers)
            assert result.status_code == 201, result.text
            return result.json()

        first_payment = payment(root, root_auth, "Только первая", root_member_id)
        second_payment = payment(second, second_auth, "Только вторая", other_member_id)
        assert root.post("/api/v1/recurring-payments", json={
            "name": "Чужой владелец", "amount": "1.00", "currency": "RUB",
            "next_due_date": "2026-10-25", "owner_id": other_member_id}, headers=root_auth).status_code == 422
        assert [p["id"] for p in root.get("/api/v1/recurring-payments").json()] == [first_payment["id"]]
        assert [p["id"] for p in second.get("/api/v1/recurring-payments").json()] == [second_payment["id"]]
        assert second.patch(f"/api/v1/recurring-payments/{first_payment['id']}",
                            json={"name": "Подмена", "amount": "1.00", "currency": "RUB"},
                            headers=second_auth).status_code == 404
        assert root.get("/api/v1/payments/summary?month=2026-10").json()["total_count"] == 1
        assert second.get("/api/v1/payments/summary?month=2026-10").json()["total_count"] == 1
        first_occurrence = root.get("/api/v1/payment-occurrences?month=2026-10").json()[0]
        assert second.get(f"/api/v1/payment-occurrences/{first_occurrence['id']}").status_code == 404
        assert second.patch(f"/api/v1/payment-occurrences/{first_occurrence['id']}",
                            json={"is_paid": True}, headers=second_auth).status_code == 404
        assert all(item["description"].find("Только первая") < 0 for item in second.get("/api/v1/activity").json())

        root_invite = root.post("/api/v1/invitations", json={"username": "firstinvite", "full_name": "Первый"},
                                headers=root_auth)
        assert root_invite.status_code == 201
        assert len(root.get("/api/v1/invitations").json()) == 1
        assert second.get("/api/v1/invitations").json() == []
        assert second.delete(f"/api/v1/invitations/{root_invite.json()['id']}", headers=second_auth).status_code == 404

        with connect(cfg) as db:
            admins = [db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
                      for name in ("rootadmin", "secondadmin")]
            for index, admin in enumerate(admins):
                now = 1000 + index * 1000
                chat = {"id": -100001 - index, "title": f"Группа {index}", "type": "supergroup"}
                with transaction(db):
                    request = start_link(db, cfg, admin, now)
                assert record_candidate(db, cfg, request["code"], chat, 42, now + 1)
                with transaction(db):
                    confirm_link(db, cfg, admin, request["request_id"], now + 2)
            rows = db.execute("""SELECT owner.team_id,b.recipient_chat_id FROM reminder_outbox b
                JOIN payment_occurrences o ON o.id=b.occurrence_id
                JOIN recurring_payments r ON r.id=o.recurring_payment_id
                JOIN users owner ON owner.id=r.owner_id ORDER BY owner.team_id""").fetchall()
            assert [(r["team_id"], r["recipient_chat_id"]) for r in rows] == [
                (admins[0]["id"], "-100001"), (second_id, "-100002")]
        assert root.get("/api/v1/telegram/connection").json()["chat"]["id"] == "-100001"
        assert second.get("/api/v1/telegram/connection").json()["chat"]["id"] == "-100002"
        assert len(root.get("/api/v1/reminders").json()) == 1
        assert len(second.get("/api/v1/reminders").json()) == 1
        destinations = []
        def fake_send(_, row):
            destinations.append(row["recipient_chat_id"])
            return SendResult("sent")
        assert tick(cfg, sender=fake_send, now=3000)
        assert tick(cfg, sender=fake_send, now=3000)
        assert sorted(destinations) == ["-100001", "-100002"]
        assert root.delete("/api/v1/telegram/connection", headers=root_auth).status_code == 204
        assert not root.get("/api/v1/telegram/connection").json()["connected"]
        assert second.get("/api/v1/telegram/connection").json()["connected"]


def test_legacy_group_and_sessions_survive_team_migration(tmp_path):
    cfg = FrozenSettings(tmp_path / "legacy.sqlite", "rootadmin", hash_password(PASSWORD),
                         "synthetic-secret-" * 5, telegram_bot_token="123456:FAKE_TEAM_TOKEN")
    legacy = SCHEMA.replace(" team_id INTEGER REFERENCES users(id),\n", "", 1)
    legacy = legacy.replace(" created_at TEXT NOT NULL,\n team_id INTEGER REFERENCES users(id)\n", " created_at TEXT NOT NULL\n", 1)
    legacy = legacy.replace(" revoked_at INTEGER, team_id INTEGER REFERENCES users(id)\n", " revoked_at INTEGER\n", 1)
    db = sqlite3.connect(cfg.database_path)
    db.executescript(legacy)
    db.execute("INSERT INTO users(id,username,full_name,password_hash,role,env_managed,created_at) VALUES(1,?,?,?,'admin',1,?)",
               ("rootadmin", "Администратор", cfg.admin_password_hash, "2026-10-24T00:00:00+00:00"))
    db.execute("INSERT INTO users(id,username,full_name,password_hash,role,created_at) VALUES(2,?,?,?,'user',?)",
               ("member", "Участник", cfg.admin_password_hash, "2026-10-24T00:00:00+00:00"))
    db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
               (digest(cfg.app_secret, "existing-session"), 1, int(time.time()) + 3600, int(time.time())))
    db.execute("INSERT INTO audit_events(user_id,username,action,description,created_at) VALUES(1,'rootadmin','login','Старое событие','2026-10-24')")
    db.execute("""CREATE TABLE telegram_connection (
        id INTEGER PRIMARY KEY CHECK(id=1), chat_id TEXT NOT NULL, chat_title TEXT NOT NULL,
        chat_type TEXT NOT NULL, bot_scope TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        connected_at TEXT NOT NULL, connected_on TEXT NOT NULL, connected_by INTEGER NOT NULL,
        unavailable_reason TEXT)""")
    db.execute("""INSERT INTO telegram_connection(id,chat_id,chat_title,chat_type,bot_scope,active,
        connected_at,connected_on,connected_by) VALUES(1,'-100001','Старая группа','supergroup',?,1,
        '2026-10-24T00:00:00+00:00','2026-10-24',1)""", (token_scope(cfg.telegram_bot_token),))
    db.commit()
    db.close()

    initialize(cfg)
    with connect(cfg) as db:
        assert [(r["id"], r["team_id"]) for r in db.execute("SELECT id,team_id FROM users ORDER BY id")] == [(1, 1), (2, 1)]
        assert db.execute("SELECT team_id FROM audit_events").fetchone()[0] == 1
        assert db.execute("SELECT chat_id FROM telegram_connection WHERE id=1").fetchone()[0] == "-100001"
        assert "CHECK(id=1)" not in db.execute("SELECT sql FROM sqlite_master WHERE name='telegram_connection'").fetchone()[0]
        with transaction(db):
            db.execute("INSERT INTO users(id,username,full_name,password_hash,role,team_id,created_at) VALUES(3,'secondadmin','Вторая команда',?,'admin',3,'2026-10-25')",
                       (cfg.admin_password_hash,))
            db.execute("""INSERT INTO telegram_connection(id,chat_id,chat_title,chat_type,bot_scope,active,
                connected_at,connected_on,connected_by) VALUES(3,'-100002','Новая группа','supergroup',?,1,
                '2026-10-25T00:00:00+00:00','2026-10-25',3)""", (token_scope(cfg.telegram_bot_token),))
    with TestClient(create_app(cfg)) as client:
        client.cookies.set("ik_session", "existing-session")
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.get("/api/v1/telegram/connection").json()["chat"]["id"] == "-100001"
