"""Corporate group setup and durable polling; mocks and synthetic SQLite only."""
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
from backend import telegram_bot as bot
from backend.telegram_connections import active_connection, start_link


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


def command(update_id=1, text="/start", chat_id=77, sender_id=77, chat_type="private"):
    return {"update_id": update_id, "message": {"message_id": 10,
            "chat": {"id": chat_id, "type": chat_type, "title": "Учебная группа"},
            "from": {"id": sender_id, "is_bot": False}, "text": text}}


def callback(update_id=1, data="ik:payments:2026-10:0", chat_id=77, sender_id=77):
    return {"update_id": update_id, "callback_query": {"id": "callback-test", "data": data,
            "from": {"id": sender_id}, "message": {"message_id": 10,
            "chat": {"id": chat_id, "type": "private"}}}}


class FakeClient:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def call(self, method, payload=None):
        self.calls.append((method, payload))
        return self.callback(method, payload) if self.callback else True


def owned(db, settings, now=1000):
    scope = bot.token_scope(settings.telegram_bot_token)
    assert bot.acquire_lease(db, scope, "owner", now)
    return scope, "owner"


def setup_code(db, settings):
    with transaction(db):
        admin = db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()
        return start_link(db, settings, admin, now=1000)["code"]


def group_request(code, **kwargs):
    return command(text="/connect@aezainkeep_bot " + code, chat_id=-1001234567890,
                   chat_type="supergroup", **kwargs)


def group_client(sender_admin=True, bot_status="administrator", can_send=True):
    def reply(method, payload):
        if method == "getChatAdministrators":
            return [{"status": "administrator", "user": {"id": 77 if sender_admin else 88}}]
        if method == "getChatMember":
            return {"status": bot_status, "is_member": True, "can_send_messages": can_send}
        if method == "getChat":
            return {"permissions": {"can_send_messages": can_send}}
        return True
    return FakeClient(reply)


def deny_financial_reads(db):
    def authorize(action, table, *_):
        import sqlite3
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table in {
            "recurring_payments", "payment_occurrences", "users", "reminder_outbox"} else sqlite3.SQLITE_OK
    db.set_authorizer(authorize)


@pytest.mark.parametrize("text", ["/menu", "/summary", "/payments", "/upcoming", "/overdue", "/paid 1", "/create rent"])
def test_financial_commands_are_ignored_without_financial_reads(settings, db, text):
    deny_financial_reads(db)
    assert bot.handle_update(settings, db, command(text=text)) is None
    assert bot.handle_update(settings, db, command(text=text, chat_id=-1001234567890, chat_type="supergroup")) is None


@pytest.mark.parametrize("text", ["/start", "/help"])
def test_private_help_is_public_static_and_never_reads_finances(settings, db, text):
    deny_financial_reads(db)
    action = bot.handle_update(settings, db, command(text=text))
    assert action.method == "sendMessage"
    assert "корпоративную группу" in action.payload["text"]
    assert len(action.payload["text"]) < 1000
    assert "parse_mode" not in action.payload


def test_private_help_links_only_valid_public_https_panel(settings, db):
    configured = replace(settings, origin="https://example.com")
    action = bot.handle_update(configured, db, command())
    assert action.payload["reply_markup"]["inline_keyboard"][0][0]["url"] == configured.origin
    assert "reply_markup" not in bot.handle_update(settings, db, command()).payload


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "https://127.0.0.1", "https://localhost",
                                      "https://10.0.0.1", "https://user:secret@example.com", "https://example.com/path",
                                      "https://example.com?secret=value", "https://example.com#token"])
def test_bot_never_links_local_or_credential_containing_panel(origin):
    assert bot.module_url(origin) is None


def test_targeted_setup_commands_require_matching_verified_username(settings, db):
    assert bot.handle_update(settings, db, command(text="/start@OtherBot"), bot_username=settings.telegram_bot_username) is None
    assert bot.handle_update(settings, db, command(text="/start@aezainkeep_bot")) is None
    assert bot.handle_update(settings, db, command(text="/start@AEZAINKEEP_BOT"), bot_username=settings.telegram_bot_username).method == "sendMessage"


def test_old_callbacks_return_only_static_notice_no_financial_queries(settings, db):
    deny_financial_reads(db)
    action = bot.handle_update(settings, db, callback(data="ik:summary:2026-10:0"))
    assert action.method == "answerCallbackQuery"
    assert action.payload == {"callback_query_id": "callback-test", "text": "Работа с платежами доступна в веб-панели."}
    inline = {"callback_query": {"id": "inline", "inline_message_id": "missing-chat", "data": "ik:summary:2026-10:0"}}
    assert bot.handle_update(settings, db, inline) is None


def test_group_setup_requires_sender_admin_and_write_permission_then_web_confirm(settings, db):
    code = setup_code(db, settings)
    denied = group_client(sender_admin=False)
    action = bot.handle_update(settings, db, group_request(code), bot_username=settings.telegram_bot_username, client=denied, bot_id=999, now=1001)
    assert "администратор" in action.payload["text"]
    assert [method for method, _ in denied.calls] == ["getChatAdministrators"]
    assert db.execute("SELECT state FROM telegram_link_requests").fetchone()[0] == "pending"
    blocked = group_client(bot_status="member", can_send=False)
    action = bot.handle_update(settings, db, group_request(code), bot_username=settings.telegram_bot_username, client=blocked, bot_id=999, now=1001)
    assert "Разрешите боту" in action.payload["text"]
    assert [method for method, _ in blocked.calls] == ["getChatAdministrators", "getChatMember", "getChat"]
    allowed = group_client(bot_status="member", can_send=True)
    action = bot.handle_update(settings, db, group_request(code), bot_username=settings.telegram_bot_username, client=allowed, bot_id=999, now=1001)
    assert "Подтвердите" in action.payload["text"] and code not in action.payload["text"]
    row = db.execute("SELECT * FROM telegram_link_requests").fetchone()
    assert row["state"] == "candidate" and row["chat_id"] == "-1001234567890"
    assert active_connection(db, settings) is None
    repeated = bot.handle_update(settings, db, group_request(code), bot_username=settings.telegram_bot_username, client=allowed, bot_id=999, now=1002)
    assert "уже использован" in repeated.payload["text"]


@pytest.mark.parametrize("invalid", ["private", "positive_group", "anonymous", "bot_sender"])
def test_setup_rejects_private_invalid_anonymous_and_bot_senders(settings, db, invalid):
    code = setup_code(db, settings)
    update = group_request(code)
    if invalid == "private":
        update["message"]["chat"] = {"id": 77, "type": "private"}
    elif invalid == "positive_group":
        update["message"]["chat"]["id"] = 77
    elif invalid == "anonymous":
        update["message"]["sender_chat"] = {"id": -1001234567890}
    else:
        update["message"]["from"]["is_bot"] = True
    client = group_client()
    assert bot.handle_update(settings, db, update, bot_username=settings.telegram_bot_username, client=client, bot_id=999, now=1001) is None
    assert not client.calls and active_connection(db, settings) is None


def test_offset_commits_before_send_and_unknown_reply_never_replays_after_restart(settings, db):
    scope, owner = owned(db, settings)
    def timeout(method, payload):
        with connect(settings) as reader:
            assert reader.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 43
        raise bot.TelegramError("request_timeout")
    client = FakeClient(timeout)
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=42), now=1000) == "unknown"
    with connect(settings) as restarted:
        assert bot.process_update(settings, restarted, client, scope, owner, command(update_id=42), now=1000) == "duplicate"
        assert restarted.execute("SELECT outcome FROM telegram_updates WHERE scope=? AND update_id=42", (scope,)).fetchone()[0] == "unknown"
    assert len(client.calls) == 1


def test_callbacks_answer_once_and_never_edit_or_send_financial_data(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    assert bot.process_update(settings, db, client, scope, owner, callback(update_id=4), now=1000) == "answered"
    assert [method for method, _ in client.calls] == ["answerCallbackQuery"]


def test_ignored_financial_commands_still_advance_durable_offset(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=9, text="/summary"), now=1000) == "ignored"
    assert not client.calls
    assert db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == 10


def test_public_help_flood_is_bounded_per_chat_without_losing_offsets(settings, db):
    scope, owner = owned(db, settings)
    client = FakeClient()
    for index in range(1, bot.RATE_COUNT + 1):
        assert bot.process_update(settings, db, client, scope, owner, command(update_id=index), now=1000) == "answered"
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=bot.RATE_COUNT + 1), now=1000) == "throttled"
    assert bot.process_update(settings, db, client, scope, owner, command(update_id=bot.RATE_COUNT + 2, chat_id=88, sender_id=88), now=1000) == "answered"
    assert len(client.calls) == bot.RATE_COUNT + 1
    assert db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0] == bot.RATE_COUNT + 3


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
            return {"username": settings.telegram_bot_username, "id": 999}
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
            return {"username": settings.telegram_bot_username, "id": 999}
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
            return {"username": settings.telegram_bot_username, "id": 999}
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


def test_client_method_allowlist_prevents_old_financial_edit_and_webhook_mutation(settings):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"ok": True, "result": True}))) as http:
        client = bot.TelegramClient(settings.telegram_bot_token, http)
        for method in ("editMessageText", "deleteWebhook", "deleteMessage"):
            with pytest.raises(bot.TelegramError, match="method_not_allowed"):
                client.call(method)
    assert not calls
