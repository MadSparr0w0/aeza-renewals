"""Durable reminder outbox. Start independently: python -m backend.worker."""
from dataclasses import dataclass
from datetime import date
import argparse
from html import escape
import logging
from pathlib import Path
import signal
import sys
import time
import uuid

import httpx

from .config import load_settings
from .db import connect, initialize, transaction, utcnow
from .domain import materialize, money, schedule_reminders
from .telegram_connections import group_id, recover_expired_deliveries, token_scope

logger = logging.getLogger("invoice_keeper.worker")
LEASE_SECONDS = 60


@dataclass
class SendResult:
    state: str
    error: str | None = None
    retry_after: int = 0


def make_telegram_client():
    """Keep one connection; retry only failure to connect before HTTP is sent.

    HTTPTransport's connect retry does not replay a read/write failure. An
    ambiguous send still becomes unknown and is never automatically repeated.
    """
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=60)
    return httpx.Client(
        transport=httpx.HTTPTransport(retries=1, limits=limits),
        timeout=httpx.Timeout(connect=5, write=5, read=15, pool=5),
        follow_redirects=False,
    )


def claim(db, owner: str, now: int, scope=None):
    """BEGIN IMMEDIATE makes the claim atomic across processes on one host.

    An expired lease is ambiguous: a previous process may have sent the message.
    Keep it unknown for human investigation instead of risking a duplicate.
    """
    with transaction(db):
        recover_expired_deliveries(db,now)
        db.execute("""UPDATE reminder_outbox SET state='cancelled',lease_owner=NULL,lease_until=NULL
          WHERE state IN ('pending','retry') AND occurrence_id IN
          (SELECT o.id FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id WHERE o.is_paid=1 OR r.active=0)""")
        row = db.execute("""SELECT b.id,o.id AS occurrence_id,o.name,o.amount_minor,o.currency,o.due_date,b.attempts,
          b.recipient_chat_id,b.delivery_scope,u.full_name AS owner_name
          FROM reminder_outbox b JOIN payment_occurrences o ON o.id=b.occurrence_id
          JOIN recurring_payments r ON r.id=o.recurring_payment_id
          JOIN users u ON u.id=r.owner_id JOIN telegram_connection c ON c.id=u.team_id AND c.active=1
          WHERE b.state IN ('pending','retry') AND b.next_attempt_at<=? AND o.is_paid=0 AND r.active=1
          AND c.chat_type IN ('group','supergroup') AND b.recipient_chat_id=c.chat_id AND b.delivery_scope=c.bot_scope
          AND (? IS NULL OR c.bot_scope=?) ORDER BY o.due_date,b.id LIMIT 1""", (now,scope,scope)).fetchone()
        if not row:
            return None
        db.execute("UPDATE reminder_outbox SET state='sending',lease_owner=?,lease_until=?,attempts=attempts+1 WHERE id=?", (owner, now + LEASE_SECONDS, row["id"]))
        return dict(row)


def deliver(settings, row, client=None):
    """Only explicit rejection / pre-send connect failure is retryable.

    Telegram sendMessage has no idempotency key. Read/write timeout, malformed
    success response or worker crash cannot safely be retried automatically.
    """
    recipient = group_id(row.get("recipient_chat_id"))
    if (recipient is None or not settings.telegram_bot_token
            or row.get("delivery_scope") != token_scope(settings.telegram_bot_token)):
        return SendResult("cancelled", "unconfirmed_group_destination")
    whole, cents = money(row["amount_minor"]).split(".")
    amount = f"{int(whole):,}".replace(",", " ") + ("," + cents if cents != "00" else "")
    currency = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(row["currency"], row["currency"])
    due = date.fromisoformat(row["due_date"])
    text = ("⏰ <b>Регулярный платёж</b>\n\n👤 " + escape(row["owner_name"])
            + "\n🧾 <b>" + escape(row["name"]) + "</b>\n"
            + amount + " " + escape(currency) + "\nСрок оплаты: " + due.strftime("%d.%m.%Y")
            + "\n\nОплата пока не отмечена.")
    payload = {"chat_id": recipient, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    from .telegram_bot import module_url
    url = module_url(settings.origin)
    if url:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть веб-панель", "url": url}]]}
    own = client is None
    if own:
        client = make_telegram_client()
    try:
        # URL and exceptions are deliberately never logged: the URL contains a secret.
        with client.stream("POST", "https://api.telegram.org/bot" + settings.telegram_bot_token + "/sendMessage", json=payload) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 65536:
                    return SendResult("unknown", "telegram_response_too_large")
            import json
            try:
                data = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                return SendResult("unknown", "telegram_invalid_response")
            if not isinstance(data, dict):
                return SendResult("unknown", "telegram_invalid_response")
            if response.status_code == 200 and data.get("ok") is True:
                result = data.get("result")
                if (not isinstance(result, dict) or not isinstance(result.get("message_id"), int)
                        or isinstance(result["message_id"], bool) or result["message_id"] <= 0):
                    return SendResult("unknown", "telegram_invalid_response")
                return SendResult("sent")
            if data.get("ok") is False:
                code = data.get("error_code", response.status_code)
                if code == 429:
                    parameters = data.get("parameters")
                    delay = parameters.get("retry_after", 60) if isinstance(parameters, dict) else 60
                    delay = delay if isinstance(delay, int) and not isinstance(delay, bool) else 60
                    return SendResult("retry", "telegram_rate_limited", min(max(delay, 1), 86400))
                if isinstance(code, int) and code >= 500:
                    return SendResult("retry", "telegram_unavailable")
                # Invalid bot/chat permissions require configuration repair, not a hot loop.
                return SendResult("retry", "telegram_rejected", 3600)
            return SendResult("unknown", "telegram_delivery_unknown")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return SendResult("retry", "telegram_connect_failed")
    except httpx.HTTPError:
        return SendResult("unknown", "telegram_delivery_unknown")
    finally:
        if own:
            client.close()


def finish(db, row, owner: str, result: SendResult, now: int):
    with transaction(db):
        # A paid/archive action may have cancelled an in-flight claim. Do not revive it.
        delay = result.retry_after or min(30 * 2 ** min(row["attempts"], 10), 21600)
        db.execute("""UPDATE reminder_outbox SET state=?,last_error=?,sent_at=?,next_attempt_at=?,lease_owner=NULL,lease_until=NULL
          WHERE id=? AND state='sending' AND lease_owner=?""",
                   (result.state, result.error, utcnow() if result.state == "sent" else None, now + delay, row["id"], owner))


def tick(settings, owner=None, sender=deliver, now=None):
    owner = owner or uuid.uuid4().hex
    now = int(time.time()) if now is None else now
    with connect(settings) as db:
        with transaction(db):
            recover_expired_deliveries(db,now)
            materialize(db, settings.today)
            schedule_reminders(db, settings.today)
        scope = token_scope(settings.telegram_bot_token)
        if not db.execute("SELECT 1 FROM telegram_connection WHERE active=1 AND bot_scope=? LIMIT 1", (scope,)).fetchone():
            return False
        row = claim(db, owner, now, scope)
        if not row:
            return False
        # Recheck immediately before the external side effect. A concurrent edit
        # after this check cannot retract an already sent Telegram message.
        valid = db.execute("""SELECT 1 FROM reminder_outbox b JOIN payment_occurrences o ON o.id=b.occurrence_id JOIN recurring_payments r ON r.id=o.recurring_payment_id
          JOIN users account ON account.id=r.owner_id
          JOIN telegram_connection c ON c.id=account.team_id AND c.active=1
          WHERE b.id=? AND b.state='sending' AND b.lease_owner=? AND o.is_paid=0 AND r.active=1
          AND b.recipient_chat_id=c.chat_id AND b.delivery_scope=c.bot_scope AND c.bot_scope=?
          AND c.chat_type IN ('group','supergroup')""", (row["id"], owner,token_scope(settings.telegram_bot_token))).fetchone()
        if not valid:
            with transaction(db):
                db.execute("UPDATE reminder_outbox SET state='cancelled',lease_owner=NULL,lease_until=NULL WHERE id=? AND state='sending' AND lease_owner=?", (row["id"], owner))
            return False
        result = sender(settings, row)
        finish(db, row, owner, result, now)
        logger.info("Напоминание %s: %s", row["id"], result.state)
        return True


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Обработка напоминаний Telegram")
    parser.add_argument("--stop-file", type=Path, help="Завершиться после текущего запроса, когда появится файл")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTPX request logging would expose a token embedded in Telegram's URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = load_settings()
    initialize(settings)
    if not settings.telegram_bot_token:
        logger.info("Бот не настроен: сетевые запросы не выполняются.")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    owner = uuid.uuid4().hex
    def should_stop():
        return stopping or (args.stop_file is not None and args.stop_file.exists())

    with make_telegram_client() as client:
        def send_with_shared_client(delivery_settings, row):
            return deliver(delivery_settings, row, client)

        while not should_stop():
            try:
                if tick(settings, owner, sender=send_with_shared_client):
                    continue
            except Exception:
                # No exception text: an HTTP exception can include a secret URL.
                logger.error("Ошибка обработки напоминания; повторная проверка через 15 секунд.")
            for _ in range(15):
                if should_stop():
                    break
                time.sleep(1)


if __name__ == "__main__":
    main()
