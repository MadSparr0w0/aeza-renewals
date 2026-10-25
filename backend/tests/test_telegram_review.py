"""Independent delivery regression cases. Synthetic databases; no real network."""
from dataclasses import replace
from datetime import date
import sqlite3

import pytest

from backend.config import Settings
from backend.db import SCHEMA, connect, initialize, transaction, utcnow
from backend.domain import add_recurring
from backend.telegram_connections import (
    confirm_link,
    disconnect,
    record_candidate,
    start_link,
    token_scope,
)
from backend.worker import tick


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 24)


@pytest.fixture
def review_settings(tmp_path):
    settings = FrozenSettings(
        tmp_path / "telegram-review.sqlite3", "admin", "synthetic-no-login",
        "synthetic-app-secret" * 4,
        telegram_bot_token="123456:TEST_REVIEW_TOKEN_NOT_REAL",
        telegram_chat_id="77",
    )
    initialize(settings)
    return settings


def user(db):
    return db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()


def add_payment(db, name="Платёж", due="2026-10-25"):
    return add_recurring(db, name=name, amount="10.00", currency="RUB",
                         next_due_date=due, created_by=1, owner_id=1)


def link(db, settings, now=1000, cid="-100123456789"):
    with transaction(db):
        request = start_link(db, settings, user(db), now=now)
    assert record_candidate(db, settings, request["code"],
                            {"id": cid, "title": "Команда", "type": "supergroup"},
                            42, now=now + 1)
    with transaction(db):
        return confirm_link(db, settings, user(db), request["request_id"], now=now + 2)


def test_reconnect_same_group_restores_unattempted_reminders(review_settings):
    with connect(review_settings) as db:
        with transaction(db):
            add_payment(db)
        link(db, review_settings)
        assert db.execute("SELECT state,attempts FROM reminder_outbox").fetchone()["state"] == "pending"
        with transaction(db):
            disconnect(db, user(db))
        assert db.execute("SELECT state FROM reminder_outbox").fetchone()[0] == "cancelled"
        link(db, review_settings, now=1020)
        restored = db.execute("SELECT state,attempts FROM reminder_outbox").fetchall()
        assert [tuple(row) for row in restored] == [("pending", 0)]


def test_expired_sending_recovers_without_an_active_connection(review_settings):
    with connect(review_settings) as db, transaction(db):
        add_payment(db)
        db.execute("""INSERT INTO reminder_outbox
          (occurrence_id,state,attempts,lease_owner,lease_until,created_at,recipient_chat_id,delivery_scope)
          VALUES(1,'sending',1,'crashed-worker',10,?,'77',?)""",
                   (utcnow(), token_scope(review_settings.telegram_bot_token)))
    assert not tick(review_settings, now=1000,
                    sender=lambda *_: pytest.fail("Unconfirmed recipient was sent a message"))
    with connect(review_settings) as db:
        state = db.execute("SELECT state,last_error FROM reminder_outbox").fetchone()
        assert tuple(state) == ("unknown", "lease_expired_delivery_unknown")
        assert link(db, review_settings)["connected"]


@pytest.mark.parametrize("state", ["sent", "unknown"])
def test_token_rotation_does_not_replay_same_period_to_same_group(review_settings, state):
    with connect(review_settings) as db:
        with transaction(db):
            add_payment(db)
        link(db, review_settings)
        with transaction(db):
            db.execute("UPDATE reminder_outbox SET state=?,attempts=1", (state,))
    rotated = replace(review_settings, telegram_bot_token="123456:ROTATED_TEST_TOKEN_NOT_REAL")
    initialize(rotated)
    assert not tick(rotated, now=1010,
                    sender=lambda *_: pytest.fail("New token activated the old connection"))
    with connect(rotated) as db:
        assert link(db, rotated, now=1020)["connected"]
        records = db.execute("SELECT state FROM reminder_outbox").fetchall()
        assert [row[0] for row in records] == [state]


def test_legacy_migration_never_activates_personal_destination(tmp_path):
    settings = FrozenSettings(
        tmp_path / "legacy-review.sqlite3", "admin", "synthetic-no-login",
        "synthetic-app-secret" * 4,
        telegram_bot_token="123456:LEGACY_TEST_TOKEN_NOT_REAL", telegram_chat_id="77",
    )
    with sqlite3.connect(settings.database_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        db.execute("""INSERT INTO users(id,username,full_name,password_hash,role,env_managed,created_at)
          VALUES(1,'admin','Администратор','synthetic-no-login','admin',1,?)""", (utcnow(),))
        for state in ("pending", "retry", "sent", "unknown"):
            series = add_payment(db, name=state)
            occurrence = db.execute("SELECT id FROM payment_occurrences WHERE recurring_payment_id=?", (series,)).fetchone()[0]
            db.execute("INSERT INTO reminder_outbox(occurrence_id,state,created_at) VALUES(?,?,?)",
                       (occurrence, state, utcnow()))
    initialize(settings)
    assert not tick(settings, sender=lambda *_: pytest.fail("Legacy personal destination became active"))
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM telegram_connection WHERE active=1").fetchone()[0] == 0
        rows = db.execute("SELECT state,recipient_chat_id FROM reminder_outbox ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [
            ("cancelled", "77"), ("cancelled", "77"), ("sent", "77"), ("unknown", "77"),
        ]


def test_explicit_legacy_group_confirmation_restores_unattempted_current_job(tmp_path):
    settings = FrozenSettings(
        tmp_path / "legacy-group-review.sqlite3", "admin", "synthetic-no-login",
        "synthetic-app-secret" * 4,
        telegram_bot_token="123456:LEGACY_TEST_TOKEN_NOT_REAL",
        telegram_chat_id="-100123456789",
    )
    with sqlite3.connect(settings.database_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        db.execute("""INSERT INTO users(id,username,full_name,password_hash,role,env_managed,created_at)
          VALUES(1,'admin','Администратор','synthetic-no-login','admin',1,?)""", (utcnow(),))
        add_payment(db)
        db.execute("INSERT INTO reminder_outbox(occurrence_id,state,created_at) VALUES(1,'pending',?)", (utcnow(),))
    initialize(settings)
    assert not tick(settings, sender=lambda *_: pytest.fail("Unconfirmed legacy group was sent a message"))
    with connect(settings) as db:
        assert db.execute("SELECT state FROM reminder_outbox").fetchone()[0] == "cancelled"
        link(db, settings)
        assert db.execute("SELECT state FROM reminder_outbox").fetchone()[0] == "pending"
