from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.config import Settings
from backend.db import connect, initialize, transaction
from backend.domain import add_recurring, amount_minor, materialize, next_month, occurrence_json, schedule_reminders
from backend.security import hash_password, verify_password
from backend.worker import SendResult, claim, deliver, finish, tick
from backend.telegram_connections import token_scope


def linked_group(db, settings):
    db.execute("""INSERT OR REPLACE INTO telegram_connection
      (id,chat_id,chat_title,chat_type,bot_scope,active,connected_at,connected_on,connected_by)
      VALUES(1,'-1000000000','Команда','supergroup',?,1,'2000-01-01T00:00:00+00:00','2000-01-01',1)""",
               (token_scope(settings.telegram_bot_token),))


def delivery_row(settings, **extra):
    return {"name": "Тест", "amount_minor": 100, "currency": "RUB", "due_date": "2026-10-23",
            "owner_name": "Участник", "recipient_chat_id": "-1000000000",
            "delivery_scope": token_scope(settings.telegram_bot_token), **extra}


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "test.sqlite3", "admin", hash_password("CorrectHorseBattery12"), "s" * 64,
                    telegram_bot_token="123456789:example-fake-token-not-real", telegram_chat_id="-1000000000")


def add_due(settings, due=None):
    initialize(settings)
    with connect(settings) as db, transaction(db):
        if settings.telegram_bot_token:
            linked_group(db, settings)
        identifier = add_recurring(db, name="Тест", amount="125.67", currency="RUB", next_due_date=due or settings.today + timedelta(days=2))
        schedule_reminders(db, settings.today)
    return identifier


def test_month_anchor_survives_february_and_leap_year():
    assert next_month(date(2025, 1, 31), 31) == date(2025, 2, 28)
    assert next_month(date(2025, 2, 28), 31) == date(2025, 3, 31)
    assert next_month(date(2024, 1, 31), 31) == date(2024, 2, 29)
    assert next_month(date(2025, 12, 31), 31) == date(2026, 1, 31)


def test_forward_periods_survive_unpaid_history_and_keep_snapshot(settings):
    add_due(settings, date(2025, 1, 31))
    with connect(settings) as db, transaction(db):
        materialize(db, date(2025, 2, 10))
        db.execute("UPDATE recurring_payments SET name='Новое имя',amount_minor=99900")
        rows = db.execute("SELECT * FROM payment_occurrences ORDER BY due_date").fetchall()
        assert [r["due_date"] for r in rows][:3] == ["2025-01-31", "2025-02-28", "2025-03-31"]
        assert all(not r["is_paid"] for r in rows)
        assert all(r["name"] == "Тест" and r["amount_minor"] == 12567 for r in rows)
        materialize(db, date(2025, 2, 10))
        assert db.execute("SELECT COUNT(*) FROM payment_occurrences").fetchone()[0] == len(rows)


@pytest.mark.parametrize("value", ["0", "-1", "0.001", "NaN", "Infinity", "1000000000.00"])
def test_invalid_money_rejected(value):
    with pytest.raises(ValueError):
        amount_minor(value)


def test_money_is_integer_minor_units():
    assert amount_minor("0.29") == 29
    assert amount_minor("999999999.99") == 99999999999


def test_paid_date_recalculates_lateness_and_due_day_not_overdue():
    row = {"id": 1, "recurring_payment_id": 1, "name": "Тест", "amount_minor": 100, "currency": "EUR", "due_date": "2026-10-20", "is_paid": 0, "paid_at": None}
    assert occurrence_json(row, date(2026, 10, 20))["status"] == "pending"
    assert occurrence_json(row, date(2026, 10, 21))["overdue_days"] == 1
    row.update(is_paid=1, paid_at="2026-10-23")
    assert occurrence_json(row, date(2026, 10, 25))["overdue_days"] == 3
    row["paid_at"] = "2026-10-20"
    assert occurrence_json(row, date(2026, 10, 25))["overdue_days"] == 0


def test_password_hash_salted_no_plaintext(settings):
    first = hash_password("Boundary correct password  ")
    second = hash_password("Boundary correct password  ")
    assert first != second
    assert "Boundary" not in first
    assert verify_password("Boundary correct password  ", first)
    assert not verify_password("Boundary correct password", first)
    assert not verify_password("wrong", first)


def test_atomic_claim_across_workers_and_single_send(settings):
    add_due(settings)
    def run(owner):
        with connect(settings) as db:
            return claim(db, owner, 1000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, ["worker1", "worker2"]))
    assert sum(row is not None for row in (a, b)) == 1
    row, owner = (a, "worker1") if a else (b, "worker2")
    with connect(settings) as db:
        finish(db, row, owner, SendResult("sent"), 1001)
        assert claim(db, "worker3", 2000) is None
        schedule_reminders(db, settings.today)
        assert db.execute("SELECT COUNT(*) FROM reminder_outbox").fetchone()[0] == 1


def test_expired_lease_becomes_unknown_without_retry(settings):
    add_due(settings)
    with connect(settings) as db:
        assert claim(db, "crashed", 1000)
        assert claim(db, "replacement", 1061) is None
        assert db.execute("SELECT state FROM reminder_outbox").fetchone()[0] == "unknown"


def test_paid_and_archived_do_not_send(settings):
    add_due(settings)
    with connect(settings) as db, transaction(db):
        db.execute("UPDATE payment_occurrences SET is_paid=1,paid_at=?", (settings.today.isoformat(),))
        schedule_reminders(db, settings.today)
    assert not tick(settings, sender=lambda *_: pytest.fail("paid reminder sent"))
    with connect(settings) as db, transaction(db):
        db.execute("UPDATE payment_occurrences SET is_paid=0,paid_at=NULL")
        db.execute("UPDATE recurring_payments SET active=0")
    assert not tick(settings, sender=lambda *_: pytest.fail("archived reminder sent"))


def test_no_credentials_means_no_network(settings):
    from dataclasses import replace
    settings = replace(settings, telegram_bot_token="", telegram_chat_id="")
    add_due(settings)
    assert not tick(settings, sender=lambda *_: pytest.fail("unconfigured Telegram invoked"))


def test_reminder_escapes_owner_and_name_and_links_to_web_panel(settings):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        from dataclasses import replace
        settings = replace(settings, origin="https://example.com")
        result = deliver(settings, delivery_row(settings, name='<a href="https://example.invalid">Название</a>',
                         owner_name="<b>Иван</b>", amount_minor=8500001), client)
    assert result.state == "sent"
    payload = captured[0]
    assert payload["chat_id"] == settings.telegram_chat_id
    assert payload["parse_mode"] == "HTML"
    assert '<a href=' not in payload["text"] and '&lt;a href=' in payload["text"]
    assert '85 000,01 ₽' in payload["text"] and '23.10.2026' in payload["text"]
    assert "Регулярный платёж" in payload["text"] and "&lt;b&gt;Иван&lt;/b&gt;" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == settings.origin
    assert "callback_data" not in json.dumps(payload)


@pytest.mark.parametrize("body", [[], {"ok": True}, {"ok": True, "result": {"message_id": True}}])
def test_malformed_delivery_acknowledgement_is_unknown(settings, body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        result = deliver(settings, delivery_row(settings), client)
    assert result.state == "unknown" and result.error == "telegram_invalid_response"


@pytest.mark.parametrize("outcome,expected", [("success", "sent"), ("rate", "retry"), ("read", "unknown"), ("connect", "retry")])
def test_telegram_delivery_classification(settings, outcome, expected):
    def handler(request):
        if outcome == "read":
            raise httpx.ReadTimeout("test", request=request)
        if outcome == "connect":
            raise httpx.ConnectError("test", request=request)
        if outcome == "rate":
            return httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 75}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = deliver(settings, delivery_row(settings, amount_minor=199, currency="USD"), client)
    assert result.state == expected
    if outcome == "rate":
        assert result.retry_after == 75


def test_worker_reuses_client_and_closes_on_graceful_stop(settings, monkeypatch, tmp_path):
    from backend import worker
    marker = tmp_path / "stop"
    calls = []
    class SharedClient:
        closed = False
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.closed = True
    client = SharedClient()
    monkeypatch.setattr(worker, "load_settings", lambda: settings)
    monkeypatch.setattr(worker, "initialize", lambda _: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(worker, "make_telegram_client", lambda: client)
    def deliver_once(cfg, row, shared):
        assert cfg is settings and shared is client
        calls.append(row)
        if len(calls) == 2:
            marker.write_text("stop")
        return SendResult("sent")
    def synthetic_tick(cfg, owner, sender):
        assert sender(cfg, {"id": len(calls) + 1}).state == "sent"
        return True
    monkeypatch.setattr(worker, "deliver", deliver_once)
    monkeypatch.setattr(worker, "tick", synthetic_tick)
    worker.main(["--stop-file", str(marker)])
    assert len(calls) == 2 and client.closed


def test_telegram_client_retries_only_connection_and_has_bounded_timeouts(monkeypatch):
    from backend import worker
    captured = {}
    def transport(**kwargs):
        captured.update(kwargs)
        return httpx.MockTransport(lambda _: httpx.Response(200))
    monkeypatch.setattr(worker.httpx, "HTTPTransport", transport)
    with worker.make_telegram_client() as client:
        assert client.timeout.connect == 5 and client.timeout.write == 5
        assert client.timeout.read == 15 and client.timeout.pool == 5
        assert not client.follow_redirects
    assert captured["retries"] == 1
    assert captured["limits"].max_connections == 1
    assert captured["limits"].keepalive_expiry == 60
