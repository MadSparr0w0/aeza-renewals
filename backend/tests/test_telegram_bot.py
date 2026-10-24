"""Telegram menu acceptance tests. Synthetic DBs and mocked transport only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
import logging
import socket
import threading
import time

import httpx
import pytest

from backend.config import Settings
from backend.db import connect, initialize, transaction
from backend.domain import add_recurring
from backend import telegram_bot as bot


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 24)


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("A unit test tried to use a real network socket")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def settings(tmp_path):
    result = FrozenSettings(tmp_path / "bot.sqlite3", "admin", "test-only-no-login", "test-secret" * 8,
                            telegram_bot_token="123456:TEST_TOKEN_NEVER_REAL", telegram_chat_id="77")
    initialize(result)
    bot.ensure_schema(result)
    return result


@pytest.fixture
def db(settings):
    with connect(settings) as connection:
        yield connection


def payment(db, name="Аренда", amount="100.00", currency="RUB", due="2026-10-24", paid=False):
    with transaction(db):
        identifier = add_recurring(db, name=name, amount=amount, currency=currency, next_due_date=due)
        if paid:
            db.execute("UPDATE payment_occurrences SET is_paid=1,paid_at='2026-10-24' WHERE recurring_payment_id=?", (identifier,))
    return identifier


def command(update_id=1, text="/menu", chat_id=77):
    return {"update_id": update_id, "message": {"message_id": 10, "chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id}, "text": text}}


def callback(update_id=1, data="ik:payments:2026-10:0", chat_id=77, sender_id=77):
    return {"update_id": update_id, "callback_query": {"id": "callback-test", "data": data, "from": {"id": sender_id},
            "message": {"message_id": 10, "chat": {"id": chat_id, "type": "private"}}}}


class FakeClient:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def call(self, method, payload=None):
        self.calls.append((method, payload))
        if self.callback:
            return self.callback(method, payload)
        return True


def owned(db, settings, now=1000):
    scope = bot.token_scope(settings.telegram_bot_token)
    assert bot.acquire_lease(db, scope, "owner", now)
    return scope, "owner"


def test_summary_keeps_integer_totals_and_currencies_separate(settings, db):
    payment(db, amount="0.10")
    payment(db, amount="0.20")
    payment(db, name="Сервер", amount="12.99", currency="USD", due="2026-10-20")
    payment(db, name="Подписка", amount="9.95", currency="EUR", paid=True)
    result = bot.build_view(db, settings, "summary")
    assert "<b>RUB</b>\nПлан: 0,30" in result.text
    assert "<b>USD</b>\nПлан: 12,99" in result.text
    assert "<b>EUR</b>\nПлан: 9,95\nОплачено: 9,95" in result.text
    assert "Периодов: 4 · оплачено: 1" in result.text
    assert "Валюты не конвертируются" in result.text
    assert len(result.text) < bot.MAX_TEXT


def test_menu_links_public_https_panel_but_keeps_separate_provider(settings, db):
    configured = replace(settings, origin="https://138.124.14.93")
    for view in ("menu", "help"):
        result = bot.build_view(db, configured, view)
        links = [button for row in result.reply_markup["inline_keyboard"] for button in row if "url" in button]
        assert links == [{"text": "🗂 Модуль продлений", "url": configured.origin},
                         {"text": "🔗 Кабинет Aéza", "url": "https://my.aeza.ru/"}]


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "https://127.0.0.1", "https://localhost",
                                      "https://10.0.0.1", "https://user:secret@example.com", "https://example.com/path",
                                      "https://example.com?secret=value", "https://example.com#token"])
def test_bot_never_links_local_or_credential_containing_panel(origin):
    assert bot.module_url(origin) is None


def test_future_month_view_materializes_original_anchor_without_payment_edits(settings, db):
    identifier = payment(db, due="2026-01-31", paid=True)
    bot.build_view(db, settings, "payments", "2027-03")
    dates = [row[0] for row in db.execute("SELECT due_date FROM payment_occurrences WHERE recurring_payment_id=? ORDER BY due_date", (identifier,))]
    assert "2026-02-28" in dates and "2026-03-31" in dates and "2027-03-31" in dates
    original = db.execute("SELECT is_paid,paid_at FROM payment_occurrences WHERE recurring_payment_id=? AND due_date='2026-01-31'", (identifier,)).fetchone()
    assert tuple(original) == (1, "2026-10-24")
    assert db.execute("SELECT COUNT(*) FROM reminder_outbox").fetchone()[0] == 0


def test_upcoming_and_overdue_have_calendar_boundaries_and_skip_paid(settings, db):
    payment(db, name="Вчера", due="2026-10-23")
    payment(db, name="Сегодня", due="2026-10-24")
    payment(db, name="Через семь", due="2026-10-31")
    payment(db, name="Через восемь", due="2026-11-01")
    payment(db, name="Уже оплачено", due="2026-10-25", paid=True)
    upcoming = bot.build_view(db, settings, "upcoming").text
    assert "Сегодня" in upcoming and "Через семь" in upcoming
    assert all(name not in upcoming for name in ("Вчера", "Через восемь", "Уже оплачено"))
    overdue = bot.build_view(db, settings, "overdue").text
    assert "Вчера" in overdue and "просрочка: 1 дн." in overdue
    assert "Сегодня" not in overdue


def test_pagination_html_escaping_and_message_bounds(settings, db):
    for index in range(14):
        payment(db, name=f"Запись {index:02d} <b> & " + "&" * 160)
    first = bot.build_view(db, settings, "payments", page=0)
    second = bot.build_view(db, settings, "payments", page=1)
    last = bot.build_view(db, settings, "payments", page=999)
    assert first.text.count("&lt;b&gt;") == bot.PAGE_SIZE
    assert "Запись 00" in first.text and "Запись 06" not in first.text
    assert "Запись 06" in second.text and "Запись 00" not in second.text
    assert "Страница 3 из 3" in last.text
    assert all(len(rendered.text) <= bot.MAX_TEXT for rendered in (first, second, last))
    for row in first.reply_markup["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode()) <= 64
            assert bot.parse_callback(button["callback_data"], settings.today)


@pytest.mark.parametrize("data", ["ik:delete:2026-10:0", "ik:payments:2026-10:-1", "ik:payments:2026-10:1000",
                                     "ik:summary:9999-12:0", "ik:summary:2026-13:0", "ik:menu:1999-12:0",
                                     "ik:menu:2026-10:0;DROP TABLE users", "я" * 64, None])
def test_callback_allowlist_rejects_unknown_actions_and_unbounded_inputs(settings, data):
    assert bot.parse_callback(data, settings.today) is None


def test_exact_chat_authorization_and_private_callback_sender(settings, db, monkeypatch):
    def no_data(*_, **__):
        pytest.fail("Unauthorized update accessed financial data")
    monkeypatch.setattr(bot, "build_view", no_data)
    assert bot.handle_update(settings, db, command(chat_id=78)) is None
    assert bot.handle_update(settings, db, command(chat_id="077")) is None
    assert bot.handle_update(settings, db, callback(sender_id=78)) is None
    inline = {"callback_query": {"id": "inline", "inline_message_id": "hidden", "from": {"id": 77}, "data": "ik:summary:2026-10:0"}}
    assert bot.handle_update(settings, db, inline) is None
    missing_type = callback()
    del missing_type["callback_query"]["message"]["chat"]["type"]
    assert bot.handle_update(settings, db, missing_type) is None


@pytest.mark.parametrize("text", ["/start", "/menu", "/summary", "/payments", "/upcoming", "/overdue", "/help", "/summary@InKeepbot"])
def test_command_views_have_html_and_compact_read_only_menu(settings, db, text):
    action = bot.handle_update(settings, db, command(text=text), bot_username="InKeepbot")
    assert action.method == "sendMessage"
    assert action.payload["parse_mode"] == "HTML"
    assert action.payload["chat_id"] == 77
    assert len(action.payload["text"]) <= bot.MAX_TEXT
    rows = action.payload["reply_markup"]["inline_keyboard"]
    assert len(rows[0]) == len(rows[1]) == len(rows[-1]) == 2
    assert [b["text"] for b in rows[-1]] == ["🔄 Обновить", "🏠 Меню"]


@pytest.mark.parametrize("view", ["menu", "help"])
def test_aeza_menu_is_manual_and_account_link_does_not_authenticate(settings, db, view):
    result = bot.build_view(db, settings, view)
    assert "Aéza · Контроль продлений" in result.text
    assert "вручную" in result.text and "вход" in result.text
    buttons = [button for row in result.reply_markup["inline_keyboard"] for button in row]
    links = [button for button in buttons if "url" in button]
    assert links == [{"text": "🔗 Кабинет Aéza", "url": "https://my.aeza.ru/"}]
    assert all("callback_data" not in button for button in links)
    assert any(button["text"] == "🧾 Продления" and button["callback_data"].startswith("ik:payments:") for button in buttons)


def test_callback_edits_original_and_unknown_mutation_command_only_shows_help(settings, db):
    identifier = payment(db)
    edit = bot.handle_update(settings, db, callback())
    assert edit.method == "editMessageText" and edit.payload["message_id"] == 10
    assert edit.callback_id == "callback-test"
    help_action = bot.handle_update(settings, db, command(text="/paid 1"))
    assert "<b>ℹ️ Помощь</b>" in help_action.payload["text"]
    assert db.execute("SELECT is_paid FROM payment_occurrences WHERE recurring_payment_id=?", (identifier,)).fetchone()[0] == 0
    invalid = bot.handle_update(settings, db, callback(data="invalid"))
    assert invalid.method == "answerCallbackQuery" and "План" not in str(invalid.payload)


def test_targeted_commands_require_verified_matching_username(settings, db):
    assert bot.handle_update(settings, db, command(text="/summary@OtherBot"), bot_username="InKeepbot") is None
    assert bot.handle_update(settings, db, command(text="/summary@InKeepbot")) is None
    assert bot.handle_update(settings, db, command(text="/summary@inkeepBOT"), bot_username="InKeepbot").method == "sendMessage"


def test_offset_commits_before_send_and_unknown_reply_never_replays_after_restart(settings, db):
    scope, owner = owned(db, settings)
    def fail_after_committed_offset(method, payload):
        with connect(settings) as reader:
            assert reader.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 43
        raise bot.TelegramError("request_timeout")
    client = FakeClient(fail_after_committed_offset)
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=42), now=1000) == "unknown"
    with connect(settings) as restarted:
        assert bot.process_update(settings, restarted, client, scope, owner, command(update_id=42), now=1000) == "duplicate"
        assert restarted.execute("SELECT outcome FROM telegram_updates WHERE scope=? AND update_id=42", (scope,)).fetchone()[0] == "unknown"
    assert len(client.calls) == 1


def test_callback_answers_once_and_edits_without_new_messages(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    assert bot.process_update(settings, db, client, scope, owner, callback(update_id=4), now=1000) == "answered"
    assert [method for method, _ in client.calls] == ["answerCallbackQuery", "editMessageText"]
    assert client.calls[1][1]["message_id"] == 10


def test_lease_loss_between_callback_ack_and_edit_suppresses_data_send(settings, db):
    scope, owner = owned(db, settings)
    def lose_lease(method, payload):
        if method == "answerCallbackQuery":
            db.execute("UPDATE telegram_pollers SET lease_owner='replacement' WHERE scope=?", (scope,))
    client = FakeClient(lose_lease)
    assert bot.process_update(settings, db, client, scope, owner, callback(), now=1000) == "failed"
    assert [method for method, _ in client.calls] == ["answerCallbackQuery"]


def test_durable_rate_limit_consumes_offsets_but_bounds_replies(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    results = [bot.process_update(settings, db, client, scope, owner, command(update_id=i), now=1000) for i in range(1, 12)]
    assert results.count("answered") == bot.RATE_COUNT
    assert results.count("throttled") == 11 - bot.RATE_COUNT
    assert len(client.calls) == bot.RATE_COUNT
    assert db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 12
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=12), now=1011) == "answered"


def test_unauthorized_updates_are_acknowledged_durably_without_financial_response(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=9, chat_id=99), now=1000) == "ignored"
    assert not client.calls
    assert db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 10


def test_receipt_retention_is_bounded_and_old_updates_still_cannot_repeat(settings, db):
    scope, owner = owned(db, settings)
    now = 1_000_000
    with transaction(db):
        db.executemany("INSERT INTO telegram_updates(scope,update_id,processed_at,outcome) VALUES(?,?,?,'answered')",
                       [(scope, index, now) for index in range(1, 1101)])
        db.execute("INSERT INTO telegram_updates(scope,update_id,processed_at,outcome) VALUES(?,2000,?,'answered')", (scope, now - 8 * 86400))
        db.execute("UPDATE telegram_pollers SET next_offset=2001 WHERE scope=?", (scope,))
    bot.prune_receipts(db, scope, now)
    count = db.execute("SELECT COUNT(*) FROM telegram_updates WHERE scope=?", (scope,)).fetchone()[0]
    assert count <= bot.RECEIPT_LIMIT
    assert db.execute("SELECT 1 FROM telegram_updates WHERE scope=? AND update_id IN(1,2000)", (scope,)).fetchone() is None
    assert db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 2001
    client = FakeClient()
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=1), now=1000) == "duplicate"
    assert not client.calls


def test_singleton_claim_is_atomic_and_heartbeat_cannot_revive_expired_owner(settings, db):
    scope = bot.token_scope(settings.telegram_bot_token)
    def attempt(owner):
        with connect(settings) as connection:
            return bot.acquire_lease(connection, scope, owner, 1000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert results.count(True) == 1
    owner = "one" if results[0] else "two"
    assert bot.heartbeat(db, scope, owner, 1050)
    assert not bot.acquire_lease(db, scope, "replacement", 1100)
    assert not bot.heartbeat(db, scope, owner, 1141)
    assert bot.acquire_lease(db, scope, "replacement", 1141)
    bot.release_lease(db, scope, owner)
    assert bot.lease_owned(db, scope, "replacement", 1141)
    assert settings.telegram_bot_token not in scope and len(scope) == 64


def test_webhook_prevents_polling_without_deleting_it(settings, db):
    client = FakeClient(lambda method, payload: {"url": "https://example.invalid/existing-hook"})
    assert bot.run_polling(settings, client=client) == 1
    assert [method for method, _ in client.calls] == ["getWebhookInfo"]
    assert db.execute("SELECT lease_owner FROM telegram_pollers").fetchone()[0] is None


def test_second_live_poller_performs_no_network(settings, db):
    scope = bot.token_scope(settings.telegram_bot_token)
    assert bot.acquire_lease(db, scope, "existing", int(time.time()))
    client = FakeClient()
    assert bot.run_polling(settings, client=client) == 2
    assert not client.calls


@pytest.mark.parametrize("startup_method", ["getWebhookInfo", "getMe"])
def test_transient_bootstrap_timeout_recovers_with_same_lease(settings, db, startup_method):
    class FastRetryEvent(threading.Event):
        def wait(self, timeout=None):
            # Skip only the retry's one-second waits, leaving heartbeat and
            # thread shutdown semantics real and independently exercised.
            return self.is_set() if timeout == 1 else super().wait(timeout)

    stop = FastRetryEvent()
    attempts = 0
    owners = []
    def reply(method, payload):
        nonlocal attempts
        if method == startup_method:
            attempts += 1
            with connect(settings) as reader:
                owners.append(reader.execute("SELECT lease_owner FROM telegram_pollers").fetchone()[0])
            if attempts == 1:
                raise bot.TelegramError("request_timeout")
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getMe":
            return {"username": "InKeepbot"}
        assert method == "getUpdates"
        stop.set()
        return []
    client = FakeClient(reply)
    assert bot.run_polling(settings, client=client, stop_event=stop) == 0
    assert attempts == 2 and owners[0] == owners[1] and owners[0] is not None
    assert any(method == "getUpdates" for method, _ in client.calls)
    assert db.execute("SELECT lease_owner FROM telegram_pollers").fetchone()[0] is None


@pytest.mark.parametrize("stop_mode", ["event", "file"])
def test_bootstrap_backoff_honors_stop_event_and_stop_file(settings, db, tmp_path, stop_mode):
    stop_file = tmp_path / "bootstrap.stop"
    class StopDuringBackoff(threading.Event):
        def wait(self, timeout=None):
            if timeout == 1:
                if stop_mode == "event":
                    self.set()
                else:
                    stop_file.write_text("stop", encoding="utf-8")
                return self.is_set()
            return super().wait(timeout)

    stop = StopDuringBackoff()
    def offline(method, payload):
        raise bot.TelegramError("network_error", retry_after=300)
    client = FakeClient(offline)
    started = time.monotonic()
    assert bot.run_polling(settings, client=client, stop_event=stop, stop_file=stop_file) == 0
    assert time.monotonic() - started < 2
    assert [method for method, _ in client.calls] == ["getWebhookInfo"]
    assert db.execute("SELECT lease_owner FROM telegram_pollers").fetchone()[0] is None
    if stop_mode == "file":
        assert stop_file.exists()


@pytest.mark.parametrize("error", ["access_rejected", "polling_conflict"])
def test_permanent_bootstrap_errors_are_not_retried(settings, error):
    def denied(method, payload):
        raise bot.TelegramError(error)
    client = FakeClient(denied)
    assert bot.run_polling(settings, client=client) == 1
    assert [method for method, _ in client.calls] == ["getWebhookInfo"]


def test_poll_restart_uses_committed_offset_and_short_long_poll(settings, db):
    stop = threading.Event()
    calls = 0
    def reply(method, payload):
        nonlocal calls
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getMe":
            return {"username": "InKeepbot"}
        if method == "getUpdates":
            calls += 1
            assert payload["timeout"] <= 20
            if calls == 1:
                return [command(update_id=42)]
            assert payload["offset"] == 43
            stop.set()
            return []
        return True
    first = FakeClient(reply)
    assert bot.run_polling(settings, client=first, stop_event=stop) == 0
    assert sum(method == "sendMessage" for method, _ in first.calls) == 1
    restarted_stop = threading.Event()
    def restarted_reply(method, payload):
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getMe":
            return {"username": "InKeepbot"}
        assert method == "getUpdates" and payload["offset"] == 43
        restarted_stop.set()
        return []
    restarted = FakeClient(restarted_reply)
    assert bot.run_polling(settings, client=restarted, stop_event=restarted_stop) == 0
    assert not any(method == "sendMessage" for method, _ in restarted.calls)


def test_stop_file_prevents_new_requests_and_is_not_deleted(settings, tmp_path):
    stop_file = tmp_path / "stop.signal"
    stop_file.write_text("stop", encoding="utf-8")
    client = FakeClient()
    assert bot.run_polling(settings, client=client, stop_file=stop_file) == 0
    assert stop_file.exists() and not client.calls


@pytest.mark.parametrize("kind,expected", [("timeout", "request_timeout"), ("network", "network_error"), ("invalid", "invalid_response"), ("rate", "rate_limited"), ("conflict", "polling_conflict"), ("forbidden", "access_rejected")])
def test_http_errors_have_fixed_safe_diagnostics(settings, kind, expected, caplog):
    secret = settings.telegram_bot_token
    caplog.set_level(logging.INFO)
    def transport(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("secret-url-" + secret, request=request)
        if kind == "network":
            raise httpx.ConnectError("secret-url-" + secret, request=request)
        if kind == "invalid":
            return httpx.Response(200, json=["unexpected", secret])
        code = {"rate": 429, "conflict": 409, "forbidden": 403}[kind]
        return httpx.Response(code, json={"ok": False, "error_code": code, "description": secret, "parameters": {"retry_after": 17}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = bot.TelegramClient(secret, http)
        with pytest.raises(bot.TelegramError) as raised:
            client.call("sendMessage", {"chat_id": 77, "text": "Проверка"})
    assert raised.value.code == expected
    assert secret not in str(raised.value) and secret not in caplog.text


def test_unchanged_callback_edit_is_success_and_methods_are_allowlisted(settings):
    calls = []
    def transport(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = bot.TelegramClient(settings.telegram_bot_token, http)
        assert client.call("editMessageText", {}) is True
        with pytest.raises(bot.TelegramError, match="method_not_allowed"):
            client.call("deleteWebhook")
    assert calls == ["editMessageText"]
