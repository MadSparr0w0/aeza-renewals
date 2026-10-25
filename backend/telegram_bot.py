"""Corporate group connection handler: python -m backend.telegram_bot.

The polling offset is committed BEFORE any Telegram reply. Ambiguous replies
are never replayed automatically. A database lease permits one poller per token;
the bot does not consume or modify the reminder worker's outbox.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import ipaddress
from html import escape
import json
import logging
from pathlib import Path
import re
import signal
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx

from .config import load_settings
from .db import connect, initialize, transaction
from .telegram_connections import GROUP_TYPES, disconnect, group_id, record_candidate

logger = logging.getLogger("invoice_keeper.telegram_bot")
POLL_SECONDS = 20
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 10
RATE_WINDOW = 10
RATE_COUNT = 8
RECEIPT_DAYS = 7
RECEIPT_LIMIT = 1000
PRODUCT_NAME = "Aéza · Регулярные платежи"
SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_pollers (
 scope TEXT PRIMARY KEY, next_offset INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
 heartbeat_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS telegram_updates (
 scope TEXT NOT NULL, update_id INTEGER NOT NULL, processed_at INTEGER NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(scope,update_id)
);
CREATE TABLE IF NOT EXISTS telegram_rate_limits (
 scope TEXT PRIMARY KEY, window_started INTEGER NOT NULL, request_count INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class BotAction:
    method: str
    payload: dict
    callback_id: str | None = None


class TelegramError(Exception):
    """Only a fixed diagnostic code is retained; never a secret URL/body."""

    def __init__(self, code: str, retry_after: int = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def token_scope(token: str) -> str:
    return hashlib.sha256(b"invoice-keeper-poller\0" + token.encode()).hexdigest()


def ensure_schema(settings):
    with connect(settings) as db:
        db.executescript(SCHEMA)


def acquire_lease(db, scope: str, owner: str, now: int) -> bool:
    with transaction(db):
        db.execute("INSERT OR IGNORE INTO telegram_pollers(scope) VALUES(?)", (scope,))
        changed = db.execute("""UPDATE telegram_pollers SET lease_owner=?,lease_until=?,heartbeat_at=?
          WHERE scope=? AND (lease_owner=? OR lease_until<=?)""",
                             (owner, now + LEASE_SECONDS, now, scope, owner, now))
        return changed.rowcount == 1


def heartbeat(db, scope: str, owner: str, now: int) -> bool:
    with transaction(db):
        changed = db.execute("""UPDATE telegram_pollers SET lease_until=?,heartbeat_at=?
          WHERE scope=? AND lease_owner=? AND lease_until>?""",
                             (now + LEASE_SECONDS, now, scope, owner, now))
        return changed.rowcount == 1


def release_lease(db, scope: str, owner: str):
    with transaction(db):
        db.execute("UPDATE telegram_pollers SET lease_owner=NULL,lease_until=0 WHERE scope=? AND lease_owner=?", (scope, owner))


def lease_owned(db, scope: str, owner: str, now: int) -> bool:
    return db.execute("SELECT 1 FROM telegram_pollers WHERE scope=? AND lease_owner=? AND lease_until>?", (scope, owner, now)).fetchone() is not None


def reserve_update(db, scope: str, owner: str, update_id: int, now: int) -> bool:
    """Persist acknowledgement before send/edit/answerCallbackQuery."""
    with transaction(db):
        row = db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=? AND lease_owner=? AND lease_until>?", (scope, owner, now)).fetchone()
        if row is None:
            raise TelegramError("lease_lost")
        if update_id < row["next_offset"]:
            return False
        receipt = db.execute("INSERT OR IGNORE INTO telegram_updates(scope,update_id,processed_at,outcome) VALUES(?,?,?,'processing')", (scope, update_id, now))
        db.execute("UPDATE telegram_pollers SET next_offset=? WHERE scope=? AND lease_owner=?", (update_id + 1, scope, owner))
        return receipt.rowcount == 1


def record_outcome(db, scope: str, update_id: int, outcome: str):
    db.execute("UPDATE telegram_updates SET outcome=? WHERE scope=? AND update_id=?", (outcome, scope, update_id))


def prune_receipts(db, scope: str, now: int):
    """The monotonic offset deduplicates old updates after audit cleanup."""
    with transaction(db):
        db.execute("""DELETE FROM telegram_updates WHERE scope=? AND
          (processed_at<? OR update_id NOT IN
          (SELECT update_id FROM telegram_updates WHERE scope=? ORDER BY update_id DESC LIMIT ?))""",
                   (scope, now - RECEIPT_DAYS * 86400, scope, RECEIPT_LIMIT))


def within_rate_limit(db, scope: str, now: int, limit=RATE_COUNT) -> bool:
    with transaction(db):
        row = db.execute("SELECT * FROM telegram_rate_limits WHERE scope=?", (scope,)).fetchone()
        if row is None or now - row["window_started"] >= RATE_WINDOW or now < row["window_started"]:
            db.execute("INSERT INTO telegram_rate_limits(scope,window_started,request_count) VALUES(?,?,1) ON CONFLICT(scope) DO UPDATE SET window_started=excluded.window_started,request_count=1", (scope, now))
            return True
        if row["request_count"] >= limit:
            return False
        db.execute("UPDATE telegram_rate_limits SET request_count=request_count+1 WHERE scope=?", (scope,))
        return True


def module_url(origin: str) -> str | None:
    """Only link the public HTTPS panel, without credentials or local addresses."""
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
                or host.lower() == "localhost" or host.lower().endswith(".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return origin
    except ValueError:
        return None


def authorized_context(settings, update):
    """Accept setup/service events only. No chat can read financial tables."""
    if not isinstance(update,dict):
        return None
    event=update.get("my_chat_member")
    if isinstance(event,dict) and isinstance(event.get("chat"),dict):
        return event,None
    callback=update.get("callback_query")
    if isinstance(callback,dict):
        message=callback.get("message")
        if (isinstance(message,dict) and isinstance(message.get("chat"),dict)
                and isinstance(callback.get("id"),str) and len(callback["id"])<=128):
            return message,callback
        return None
    message=update.get("message")
    if not isinstance(message,dict) or not isinstance(message.get("chat"),dict):
        return None
    if message.get("migrate_to_chat_id") or message.get("migrate_from_chat_id"):
        return message,None
    content=message.get("text")
    if not isinstance(content,str) or len(content)>512 or not content.startswith("/"):
        return None
    command=content.split(maxsplit=1)[0].split("@",1)[0].lower()
    if command not in {"/connect","/start","/help"}:
        return None
    return message,None


def handle_update(settings,db,update,bot_username=None,client=None,bot_id=None,now=None):
    context=authorized_context(settings,update)
    if context is None:
        return None
    message,callback=context
    chat=message["chat"]
    cid=group_id(chat.get("id"))
    if callback:
        return BotAction("answerCallbackQuery",{"callback_query_id":callback["id"],"text":"Работа с платежами доступна в веб-панели."})
    event=update.get("my_chat_member")
    if isinstance(event,dict):
        member=event.get("new_chat_member") or {}
        status=member.get("status")
        can_send=status in {"creator","administrator","member"} or (status=="restricted" and member.get("is_member") is True and member.get("can_send_messages") is True)
        if not can_send:
            with transaction(db):
                active=db.execute("SELECT * FROM telegram_connection WHERE chat_id=? AND active=1 AND bot_scope=?", (cid,token_scope(settings.telegram_bot_token))).fetchone()
                if active:
                    disconnect(db,reason="bot_removed_or_restricted",team_id=active['id'])
                db.execute("UPDATE telegram_link_requests SET state='revoked' WHERE chat_id=? AND state IN ('pending','candidate')",(cid,))
        return None
    if message.get("migrate_to_chat_id") or message.get("migrate_from_chat_id"):
        # A migration changes the physical recipient. Require a fresh explicit
        # confirmation instead of forwarding an old queue to a new chat ID.
        old=group_id(message.get("migrate_from_chat_id")) or cid
        with transaction(db):
            active=db.execute("SELECT * FROM telegram_connection WHERE chat_id=? AND active=1 AND bot_scope=?", (old,token_scope(settings.telegram_bot_token))).fetchone()
            if active:
                disconnect(db,reason="group_migrated_reconnect_required",team_id=active['id'])
        return None
    content=message.get("text","").split(maxsplit=1)
    addressed=content[0].split("@",1)
    if len(addressed)>1 and (not bot_username or addressed[1].casefold()!=bot_username.casefold()):
        return None
    command=addressed[0].lower()
    sender=message.get("from") or {}
    sender_id=sender.get("id")
    if isinstance(sender_id,bool) or not isinstance(sender_id,int) or sender_id<=0 or sender.get("is_bot"):
        return None
    def reply(text):
        payload={"chat_id":chat["id"],"text":text,"disable_web_page_preview":True}
        url=module_url(settings.origin)
        if url:
            payload["reply_markup"]={"inline_keyboard":[[{"text":"Открыть веб-панель","url":url}]]}
        return BotAction("sendMessage",payload)
    if command in {"/start","/help"}:
        if chat.get("type")=="private" and str(chat.get("id"))==str(sender_id):
            return reply("📋 Aéza · Регулярные платежи\n\nБот отправляет напоминания в корпоративную группу. Личные кабинеты, создание платежей и отметки об оплате доступны на сайте.\n\nЧтобы подключить группу, администратор создаёт код в разделе Telegram веб-панели.")
        if command=="/start" and cid and chat.get("type") in GROUP_TYPES:
            return reply("Бот добавлен. Администратор может подключить эту группу кодом из раздела Telegram веб-панели.")
        return None
    if command!="/connect" or not cid or chat.get("type") not in GROUP_TYPES or message.get("sender_chat"):
        return None
    if len(content)!=2 or not re.fullmatch(r"[A-Za-z0-9_-]{32}",content[1]):
        return reply("Создайте код в разделе Telegram веб-панели и отправьте команду подключения целиком.")
    if client is None or not isinstance(bot_id,int):
        return None
    administrators=client.call("getChatAdministrators",{"chat_id":cid})
    allowed=isinstance(administrators,list) and any(isinstance(a,dict) and a.get("status") in {"creator","administrator"} and isinstance(a.get("user"),dict) and a["user"].get("id")==sender_id for a in administrators)
    if not allowed:
        return reply("Подключить чат может его администратор.")
    membership=client.call("getChatMember",{"chat_id":cid,"user_id":bot_id})
    member_status=membership.get("status") if isinstance(membership,dict) else None
    writable=member_status in {"creator","administrator"} or (member_status=="restricted" and membership.get("is_member") is True and membership.get("can_send_messages") is True)
    if member_status == "member":
        details=client.call("getChat",{"chat_id":cid})
        permissions=details.get("permissions") if isinstance(details,dict) else None
        writable=isinstance(permissions,dict) and permissions.get("can_send_messages") is True
    if not writable:
        return reply("Разрешите боту отправлять сообщения в этой группе и повторите подключение.")
    if not record_candidate(db,settings,content[1],chat,sender_id,now=now):
        return reply("Код истёк или уже использован. Создайте новый код в веб-панели.")
    return reply("✅ Группа найдена. Подтвердите её название в разделе Telegram веб-панели. После подтверждения здесь будут появляться напоминания о платежах команды.")


class TelegramClient:
    METHODS = frozenset({"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "answerCallbackQuery", "getChatAdministrators", "getChatMember", "getChat"})

    def __init__(self, token: str, client=None):
        # HTTPX's INFO request line embeds the bot token in the URL. Keep the
        # protection when this helper is imported outside the CLI as well.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._token = token
        self._owned = client is None
        self._client = client or httpx.Client(timeout=httpx.Timeout(30, connect=5, write=10, pool=5), follow_redirects=False)

    def close(self):
        if self._owned:
            self._client.close()

    def call(self, method: str, payload=None):
        if method not in self.METHODS:
            raise TelegramError("method_not_allowed")
        try:
            with self._client.stream("POST", "https://api.telegram.org/bot" + self._token + "/" + method, json=payload or {}) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise TelegramError("response_too_large")
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    raise TelegramError("invalid_response") from None
                if not isinstance(data, dict):
                    raise TelegramError("invalid_response")
                if response.status_code == 200 and data.get("ok") is True:
                    return data.get("result")
                if data.get("error_code") == 400 and method == "editMessageText" and "message is not modified" in str(data.get("description", "")).lower():
                    return True
                code = data.get("error_code", response.status_code)
                if code == 429:
                    parameters = data.get("parameters")
                    delay = parameters.get("retry_after", 10) if isinstance(parameters, dict) else 10
                    delay = min(max(delay, 1), 300) if isinstance(delay, int) else 10
                    raise TelegramError("rate_limited", delay)
                if code == 409:
                    raise TelegramError("polling_conflict")
                if code in {401, 403}:
                    raise TelegramError("access_rejected")
                raise TelegramError("telegram_rejected")
        except httpx.TimeoutException:
            raise TelegramError("request_timeout") from None
        except httpx.HTTPError:
            raise TelegramError("network_error") from None


def process_update(settings, db, client, scope, owner, update, now=None, bot_username=None, bot_id=None):
    current_time = (lambda: int(time.time())) if now is None else (lambda: now)
    observed_at = current_time()
    update_id = update.get("update_id") if isinstance(update, dict) else None
    if isinstance(update_id, bool) or not isinstance(update_id, int) or not 0 <= update_id < 2**63 - 1:
        return "ignored"
    if not reserve_update(db, scope, owner, update_id, observed_at):
        return "duplicate"
    if authorized_context(settings, update) is None:
        record_outcome(db, scope, update_id, "ignored")
        return "ignored"
    service = isinstance(update.get("my_chat_member"), dict) or any(
        authorized_context(settings, update)[0].get(key) for key in ("migrate_to_chat_id", "migrate_from_chat_id"))
    if not service and (not within_rate_limit(db, scope+":global", observed_at, limit=60) or not within_rate_limit(db, scope+":"+str(authorized_context(settings, update)[0]["chat"].get("id")), observed_at)):
        # No financial data and no repeated API responses during a flood.
        record_outcome(db, scope, update_id, "throttled")
        return "throttled"
    try:
        action = handle_update(settings, db, update, bot_username, client=client, bot_id=bot_id, now=observed_at)
        if action is None:
            record_outcome(db, scope, update_id, "ignored")
            return "ignored"
        if not lease_owned(db, scope, owner, current_time()):
            raise TelegramError("lease_lost")
        if action.callback_id:
            client.call("answerCallbackQuery", {"callback_query_id": action.callback_id})
        if not lease_owned(db, scope, owner, current_time()):
            raise TelegramError("lease_lost")
        client.call(action.method, action.payload)
        record_outcome(db, scope, update_id, "answered")
        return "answered"
    except TelegramError as exc:
        outcome = "unknown" if exc.code in {"request_timeout", "network_error", "invalid_response", "response_too_large"} else "failed"
        record_outcome(db, scope, update_id, outcome)
        logger.warning("Ответ бота не подтверждён: %s. Автоматического повтора нет.", exc.code)
        return outcome
    except Exception:
        record_outcome(db, scope, update_id, "failed")
        logger.error("Не удалось обработать команду бота; секреты и содержимое запроса не журналируются.")
        return "failed"


def run_polling(settings, client=None, stop_event=None, stop_file: Path | None = None):
    """Run one leased poller. No webhook deletion or automatic reply retries."""
    if not settings.telegram_bot_token:
        logger.error("Для подключения корпоративной группы укажите TELEGRAM_BOT_TOKEN.")
        return 1
    if (stop_event and stop_event.is_set()) or (stop_file and stop_file.exists()):
        return 0
    ensure_schema(settings)
    scope, owner = token_scope(settings.telegram_bot_token), uuid.uuid4().hex
    stop_event = stop_event or threading.Event()
    client_owned = client is None
    client = client or TelegramClient(settings.telegram_bot_token)
    heartbeat_thread = None

    def stopping():
        return stop_event.is_set() or bool(stop_file and stop_file.exists())

    def maintain_lease():
        while not stop_event.wait(HEARTBEAT_SECONDS):
            if stopping():
                stop_event.set()
                return
            try:
                with connect(settings) as pulse:
                    if heartbeat(pulse, scope, owner, int(time.time())):
                        continue
            except Exception:
                pass
            logger.error("Аренда процесса бота потеряна; опрос остановлен.")
            stop_event.set()
            return

    try:
        with connect(settings) as db:
            if not acquire_lease(db, scope, owner, int(time.time())):
                logger.error("Другой процесс уже управляет опросом этого бота. Второй опрос не запущен.")
                return 2
            heartbeat_thread = threading.Thread(target=maintain_lease, name="telegram-poller-heartbeat", daemon=True)
            heartbeat_thread.start()
            try:
                def bootstrap_call(method):
                    # Both startup methods are read-only, so a transient network
                    # failure is safe to retry. The independent heartbeat keeps
                    # the same lease while waiting for connectivity to recover.
                    while not stopping():
                        if not lease_owned(db, scope, owner, int(time.time())):
                            raise TelegramError("lease_lost")
                        try:
                            return client.call(method)
                        except TelegramError as exc:
                            if exc.code in {"access_rejected", "polling_conflict", "lease_lost"}:
                                raise
                            logger.warning("Подключение к Telegram временно недоступна: %s. Повтор после паузы.", exc.code)
                            for _ in range(min(max(exc.retry_after or 5, 1), 300)):
                                if stopping():
                                    return None
                                stop_event.wait(1)
                    return None

                webhook = bootstrap_call("getWebhookInfo")
                if stopping():
                    return 0
                if not isinstance(webhook, dict) or webhook.get("url"):
                    logger.error("У бота установлен webhook или его состояние неизвестно. Опрос не запущен; webhook не изменён.")
                    return 1
                identity = bootstrap_call("getMe")
                if stopping():
                    return 0
                bot_username = identity.get("username") if isinstance(identity, dict) else None
                if not isinstance(bot_username, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username):
                    raise TelegramError("identity_unknown")
                bot_id = identity.get("id")
                if not isinstance(bot_id,int) or isinstance(bot_id,bool) or bot_username.casefold()!=settings.telegram_bot_username.casefold():
                    raise TelegramError("identity_unknown")
                logger.info("Обработчик подключения корпоративной группы запущен.")
                while not stopping():
                    if not lease_owned(db, scope, owner, int(time.time())):
                        logger.error("Аренда процесса бота потеряна; опрос остановлен.")
                        return 2
                    offset = db.execute("SELECT next_offset FROM telegram_pollers WHERE scope=?", (scope,)).fetchone()[0]
                    try:
                        updates = client.call("getUpdates", {"offset": offset, "timeout": POLL_SECONDS, "limit": 20, "allowed_updates": ["message", "callback_query", "my_chat_member"]})
                        if not isinstance(updates, list):
                            raise TelegramError("invalid_response")
                    except TelegramError as exc:
                        if exc.code in {"polling_conflict", "access_rejected"}:
                            logger.error("Опрос бота остановлен: %s. Проверьте отдельные процессы и права токена.", exc.code)
                            return 1
                        logger.warning("Опрос Telegram временно недоступен: %s.", exc.code)
                        for _ in range(exc.retry_after or 5):
                            if stopping():
                                break
                            stop_event.wait(1)
                        continue
                    if stopping():
                        break
                    # Telegram normally orders updates; sorting avoids dropping a
                    # lower identifier if a proxy/mock returns a shuffled batch.
                    valid = [u for u in updates[:20] if isinstance(u, dict) and isinstance(u.get("update_id"), int) and not isinstance(u["update_id"], bool)]
                    for update in sorted(valid, key=lambda item: item["update_id"]):
                        if stopping():
                            break
                        process_update(settings, db, client, scope, owner, update, bot_username=bot_username, bot_id=bot_id)
                    prune_receipts(db, scope, int(time.time()))
                return 0
            finally:
                stop_event.set()
                if heartbeat_thread is not None:
                    heartbeat_thread.join(timeout=2)
                release_lease(db, scope, owner)
    except TelegramError as exc:
        logger.error("Обработчик бота не запущен: %s. Секреты не журналируются.", exc.code)
        return 1
    except Exception:
        logger.error("Обработчик бота остановлен из-за внутренней ошибки. Проверьте конфигурацию и доступ к базе.")
        return 1
    finally:
        stop_event.set()
        if client_owned:
            client.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Aéza · Регулярные платежи: подключение корпоративной группы для уведомлений.")
    parser.add_argument("--stop-file", type=Path, help="Путь к файлу мягкой остановки; процесс только проверяет его наличие.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    try:
        settings = load_settings()
        initialize(settings)
        code = run_polling(settings, stop_event=stop_event, stop_file=args.stop_file)
    except Exception:
        logger.error("Не удалось запустить обработчик бота. Проверьте окружение и базу; секреты не выводятся.")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()

