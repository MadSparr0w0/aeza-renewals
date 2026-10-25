"""One confirmed Telegram group per isolated team, with fixed recipients.

The environment's legacy chat ID never activates delivery. Binding a group
requires a code from the web administrator, a group administrator, and a final
web confirmation. No financial records are returned by the bot.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .db import audit, connect, transaction, utcnow

LINK_SECONDS = 600
GROUP_TYPES = frozenset({'group', 'supergroup'})


def token_scope(token):
    return hashlib.sha256(b'invoice-keeper-poller\0' + token.encode()).hexdigest()


def code_hash(settings, code):
    return hmac.new(settings.app_secret.encode(), ('telegram-link:' + code).encode(), hashlib.sha256).hexdigest()


def group_id(value):
    if isinstance(value, bool):
        return None
    value = str(value)
    if not re.fullmatch(r'-[1-9]\d{0,18}', value):
        return None
    return value if -(2**63) < int(value) < 0 else None


def initialize_telegram(db, settings):
    """Called inside initialize's existing transaction; never commits it."""
    admin_id = db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()["id"]
    existing = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='telegram_connection'").fetchone()
    if existing and 'CHECK(id=1)' in existing['sql'].replace(' ', ''):
        db.execute('ALTER TABLE telegram_connection RENAME TO telegram_connection_legacy')
        db.execute('''CREATE TABLE telegram_connection (
          id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL,
          chat_title TEXT NOT NULL, chat_type TEXT NOT NULL,
          bot_scope TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          connected_at TEXT NOT NULL, connected_on TEXT NOT NULL,
          connected_by INTEGER NOT NULL, unavailable_reason TEXT)''')
        db.execute('''INSERT INTO telegram_connection
          SELECT ?,chat_id,chat_title,chat_type,bot_scope,active,connected_at,connected_on,
                 connected_by,unavailable_reason FROM telegram_connection_legacy''', (admin_id,))
        db.execute('DROP TABLE telegram_connection_legacy')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_connection (
      id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL,
      chat_title TEXT NOT NULL, chat_type TEXT NOT NULL,
      bot_scope TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      connected_at TEXT NOT NULL, connected_on TEXT NOT NULL,
      connected_by INTEGER NOT NULL, unavailable_reason TEXT)''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_active_telegram_chat ON telegram_connection(chat_id) WHERE active=1')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_link_requests (
      id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
      created_by INTEGER NOT NULL, created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL, bot_scope TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','candidate','confirmed','revoked')),
      chat_id TEXT, chat_title TEXT, chat_type TEXT, sender_id TEXT,
      team_id INTEGER REFERENCES users(id))''')
    if 'team_id' not in {r['name'] for r in db.execute('PRAGMA table_info(telegram_link_requests)')}:
        db.execute('ALTER TABLE telegram_link_requests ADD COLUMN team_id INTEGER REFERENCES users(id)')
    db.execute('UPDATE telegram_link_requests SET team_id=? WHERE team_id IS NULL', (admin_id,))
    db.execute('CREATE INDEX IF NOT EXISTS ix_telegram_link_team ON telegram_link_requests(team_id,state)')
    columns = {r['name'] for r in db.execute('PRAGMA table_info(reminder_outbox)')}
    if 'recipient_chat_id' not in columns:
        db.execute('''CREATE TABLE reminder_outbox_group (
          id INTEGER PRIMARY KEY, occurrence_id INTEGER NOT NULL REFERENCES payment_occurrences(id),
          state TEXT NOT NULL CHECK(state IN ('pending','sending','retry','sent','cancelled','unknown')),
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0,
          lease_owner TEXT, lease_until INTEGER, sent_at TEXT, last_error TEXT, created_at TEXT NOT NULL,
          recipient_chat_id TEXT, delivery_scope TEXT,
          UNIQUE(occurrence_id,recipient_chat_id,delivery_scope))''')
        db.execute('''INSERT INTO reminder_outbox_group
          SELECT id,occurrence_id,CASE WHEN state IN ('pending','retry') THEN 'cancelled' ELSE state END,
          attempts,next_attempt_at,lease_owner,lease_until,sent_at,
          CASE WHEN state IN ('pending','retry') THEN 'legacy_destination_unconfirmed' ELSE last_error END,
          created_at,?,? FROM reminder_outbox''',
                   (settings.telegram_chat_id or None, token_scope(settings.telegram_bot_token) if settings.telegram_bot_token else None))
        db.execute('DROP TABLE reminder_outbox')
        db.execute('ALTER TABLE reminder_outbox_group RENAME TO reminder_outbox')
    db.execute('CREATE INDEX IF NOT EXISTS ix_outbox_delivery ON reminder_outbox(state,recipient_chat_id,delivery_scope)')


def active_connection(db, settings=None, team_id=None):
    if team_id is None:
        row = db.execute("SELECT * FROM telegram_connection WHERE active=1 AND chat_type IN ('group','supergroup') ORDER BY id LIMIT 1").fetchone()
    else:
        row = db.execute("SELECT * FROM telegram_connection WHERE id=? AND active=1 AND chat_type IN ('group','supergroup')", (team_id,)).fetchone()
    if row is None or group_id(row['chat_id']) is None:
        return None
    if settings is not None and (not settings.telegram_bot_token or row['bot_scope'] != token_scope(settings.telegram_bot_token)):
        return None
    return row


def is_connected(db, settings=None, team_id=None):
    return active_connection(db, settings, team_id) is not None


def recover_expired_deliveries(db, now):
    db.execute("""UPDATE reminder_outbox SET state='unknown',last_error='lease_expired_delivery_unknown',
      lease_owner=NULL,lease_until=NULL WHERE state='sending' AND lease_until<=?""",(now,))


def schedule_group_reminders(db, today, owner_id=None):
    cutoff = (today + timedelta(days=3)).isoformat()
    scope = '' if owner_id is None else ' AND r.owner_id=?'
    params = [utcnow(), cutoff]
    if owner_id is not None:
        params.append(owner_id)
    # Each owner is routed only to its team's confirmed group. A new group
    # receives current/future obligations, not all historical overdue records.
    db.execute('''INSERT OR IGNORE INTO reminder_outbox
      (occurrence_id,state,created_at,recipient_chat_id,delivery_scope)
      SELECT o.id,'pending',?,c.chat_id,c.bot_scope FROM payment_occurrences o
      JOIN recurring_payments r ON r.id=o.recurring_payment_id
      JOIN users owner ON owner.id=r.owner_id
      JOIN telegram_connection c ON c.id=owner.team_id AND c.active=1
        AND c.chat_type IN ('group','supergroup')
      WHERE o.is_paid=0 AND r.active=1 AND o.due_date<=?
      AND (o.due_date>=c.connected_on OR r.created_at>=c.connected_at)
      AND NOT EXISTS(SELECT 1 FROM reminder_outbox prior WHERE prior.occurrence_id=o.id
        AND prior.recipient_chat_id=c.chat_id AND prior.state IN ('sent','unknown','sending'))''' + scope, params)
    db.execute('''UPDATE reminder_outbox SET state='cancelled',last_error='payment_not_required',lease_owner=NULL,lease_until=NULL
      WHERE state IN ('pending','retry') AND occurrence_id IN
      (SELECT o.id FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id WHERE o.is_paid=1 OR r.active=0)''')
    # An unpaid-again period may resume a job that was cancelled before any
    # attempt. Confirmed or ambiguous deliveries are never reset.
    db.execute('''UPDATE reminder_outbox SET state='pending',last_error=NULL
      WHERE state='cancelled' AND attempts=0 AND last_error IN
      ('payment_not_required','chat_disconnected','bot_removed_or_restricted','group_migrated_reconnect_required','legacy_destination_unconfirmed')
      AND NOT EXISTS(SELECT 1 FROM reminder_outbox prior
        WHERE prior.occurrence_id=reminder_outbox.occurrence_id
        AND prior.recipient_chat_id=reminder_outbox.recipient_chat_id AND prior.state IN ('sent','unknown','sending'))
      AND EXISTS (SELECT 1 FROM payment_occurrences o
        JOIN recurring_payments r ON r.id=o.recurring_payment_id
        JOIN users owner ON owner.id=r.owner_id
        JOIN telegram_connection c ON c.id=owner.team_id AND c.active=1
          AND c.chat_type IN ('group','supergroup')
        WHERE o.id=reminder_outbox.occurrence_id AND o.is_paid=0 AND r.active=1
          AND o.due_date<=? AND (o.due_date>=c.connected_on OR r.created_at>=c.connected_at)
          AND c.chat_id=reminder_outbox.recipient_chat_id
          AND c.bot_scope=reminder_outbox.delivery_scope)''', (cutoff,))


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


def connection_status(db, settings, now=None, team_id=None):
    now = int(time.time()) if now is None else now
    if team_id is None:
        team_id = db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()['id']
    linked = active_connection(db, settings, team_id)
    pending = db.execute("SELECT * FROM telegram_link_requests WHERE team_id=? AND state IN ('pending','candidate') AND expires_at>? AND bot_scope=? ORDER BY id DESC LIMIT 1",
                         (team_id, now, token_scope(settings.telegram_bot_token))).fetchone()
    def chat(r):
        return {'id': r['chat_id'], 'title': r['chat_title'], 'type': r['chat_type']} if r['chat_id'] else None
    return {'bot_configured': bool(settings.telegram_bot_token), 'bot_username': settings.telegram_bot_username,
            'connected': linked is not None, 'chat': chat(linked) if linked else None,
            'pending': {'id': pending['id'], 'expires_at': iso_timestamp(pending['expires_at']), 'chat': chat(pending)} if pending else None}


def start_link(db, settings, user, now=None):
    now = int(time.time()) if now is None else now
    if not settings.telegram_bot_token:
        raise HTTPException(409, 'Бот ещё не настроен на сервере.')
    last = db.execute('SELECT MAX(created_at) FROM telegram_link_requests WHERE created_by=?', (user['id'],)).fetchone()[0]
    if last is not None and now-last < 10:
        raise HTTPException(429, 'Подождите несколько секунд перед созданием нового кода.')
    code = secrets.token_urlsafe(24)
    db.execute("UPDATE telegram_link_requests SET state='revoked' WHERE team_id=? AND state IN ('pending','candidate')", (user['team_id'],))
    cur = db.execute("INSERT INTO telegram_link_requests(code_hash,created_by,created_at,expires_at,bot_scope,state,team_id) VALUES(?,?,?,?,?,'pending',?)",
                     (code_hash(settings,code),user['id'],now,now+LINK_SECONDS,token_scope(settings.telegram_bot_token),user['team_id']))
    audit(db,user,'telegram_link_requested','Создан код подключения корпоративного чата')
    return {'request_id': cur.lastrowid,'code':code,'command':f'/connect@{settings.telegram_bot_username} {code}',
            'expires_at':iso_timestamp(now+LINK_SECONDS),'add_bot_url':f'https://t.me/{settings.telegram_bot_username}?startgroup=true'}


def record_candidate(db, settings, code, chat, sender_id, now=None):
    now = int(time.time()) if now is None else now
    cid = group_id(chat.get('id')) if isinstance(chat,dict) else None
    if cid is None or chat.get('type') not in GROUP_TYPES or not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}',code):
        return False
    title = str(chat.get('title') or 'Корпоративная группа')[:160]
    with transaction(db):
        row = db.execute("SELECT id FROM telegram_link_requests WHERE code_hash=? AND state='pending' AND expires_at>? AND bot_scope=?",
                         (code_hash(settings,code),now,token_scope(settings.telegram_bot_token))).fetchone()
        if row is None:
            return False
        db.execute("UPDATE telegram_link_requests SET state='candidate',chat_id=?,chat_title=?,chat_type=?,sender_id=? WHERE id=?",
                   (cid,title,chat['type'],str(sender_id),row['id']))
    return True


def confirm_link(db, settings, user, request_id, now=None):
    now = int(time.time()) if now is None else now
    row = db.execute("SELECT * FROM telegram_link_requests WHERE id=? AND created_by=? AND team_id=? AND state='candidate' AND expires_at>? AND bot_scope=?",
                     (request_id,user['id'],user['team_id'],now,token_scope(settings.telegram_bot_token))).fetchone()
    if row is None or group_id(row['chat_id']) is None or row['chat_type'] not in GROUP_TYPES:
        raise HTTPException(409,'Код истёк или группа ещё не подтверждена. Создайте новый код и выполните команду в нужной группе.')
    recover_expired_deliveries(db,now)
    if db.execute("""SELECT 1 FROM reminder_outbox b JOIN payment_occurrences o ON o.id=b.occurrence_id
      JOIN recurring_payments r ON r.id=o.recurring_payment_id JOIN users owner ON owner.id=r.owner_id
      WHERE b.state='sending' AND owner.team_id=? LIMIT 1""", (user['team_id'],)).fetchone():
        raise HTTPException(409,'Дождитесь завершения текущего уведомления и повторите подключение.')
    if db.execute("SELECT 1 FROM telegram_connection WHERE id<>? AND chat_id=? AND active=1", (user['team_id'],row['chat_id'])).fetchone():
        raise HTTPException(409,'Эта группа уже подключена к другой команде.')
    db.execute("""UPDATE reminder_outbox SET state='cancelled',last_error='chat_disconnected',lease_owner=NULL,lease_until=NULL
      WHERE state IN ('pending','retry') AND (recipient_chat_id IS NULL OR delivery_scope IS NULL OR recipient_chat_id<>? OR delivery_scope<>?)
      AND occurrence_id IN (SELECT o.id FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id
        JOIN users owner ON owner.id=r.owner_id WHERE owner.team_id=?)""", (row['chat_id'],row['bot_scope'],user['team_id']))
    db.execute('''INSERT INTO telegram_connection(id,chat_id,chat_title,chat_type,bot_scope,active,connected_at,connected_on,connected_by)
      VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id,chat_title=excluded.chat_title,
      chat_type=excluded.chat_type,bot_scope=excluded.bot_scope,active=1,connected_at=excluded.connected_at,
      connected_on=excluded.connected_on,connected_by=excluded.connected_by,unavailable_reason=NULL''',
               (user['team_id'],row['chat_id'],row['chat_title'],row['chat_type'],row['bot_scope'],utcnow(),settings.today.isoformat(),user['id']))
    db.execute("UPDATE telegram_link_requests SET state='confirmed' WHERE id=?",(row['id'],))
    audit(db,user,'telegram_connected','Подключён корпоративный чат: '+row['chat_title'])
    schedule_group_reminders(db,settings.today)
    return connection_status(db,settings,now,user['team_id'])


def disconnect(db, user=None, reason='chat_disconnected', team_id=None):
    team_id = team_id or (user['team_id'] if user else db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()['id'])
    db.execute('UPDATE telegram_connection SET active=0,unavailable_reason=? WHERE id=?',(reason,team_id))
    db.execute("UPDATE telegram_link_requests SET state='revoked' WHERE team_id=? AND state IN ('pending','candidate')",(team_id,))
    db.execute("""UPDATE reminder_outbox SET state='cancelled',last_error=?,lease_owner=NULL,lease_until=NULL
      WHERE state IN ('pending','retry') AND occurrence_id IN
      (SELECT o.id FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id
       JOIN users owner ON owner.id=r.owner_id WHERE owner.team_id=?)""",(reason,team_id))
    if user:
        audit(db,user,'telegram_disconnected','Корпоративный чат отключён')


class ConfirmLink(BaseModel):
    model_config = ConfigDict(extra='forbid',title='Подтверждение корпоративной группы')
    request_id: int = Field(gt=0,strict=True,title='Идентификатор запроса подключения',description='Идентификатор действующего кода, по которому бот уже нашёл группу. Возвращается в состоянии подключения.')


def register_routes(app, current, admin):
    @app.get('/api/v1/telegram/connection',tags=['Система'],summary='Подключение корпоративного чата',description='Администратор получает состояние подтверждённой группы и срок текущего кода. Сам код повторно не возвращается.')
    def status(request:Request,user=Depends(admin)):
        cfg=request.app.state.settings
        with connect(cfg) as db:return connection_status(db,cfg,team_id=user['team_id'])

    @app.post('/api/v1/telegram/connection/request',tags=['Система'],summary='Создать одноразовый код подключения группы',description='Администратор получает код на десять минут и команду для администратора Telegram-группы. Предыдущий незавершённый код отзывается. Требуется CSRF.')
    def request_link(request:Request,user=Depends(admin)):
        cfg=request.app.state.settings
        with connect(cfg) as db,transaction(db):return start_link(db,cfg,user)

    @app.post('/api/v1/telegram/connection/confirm',tags=['Система'],summary='Подтвердить выбранную корпоративную группу',description='После выполнения команды ботом администратор подтверждает конкретную найденную группу. Только после этого разрешена отправка напоминаний. Требуется CSRF.')
    def confirm(body:ConfirmLink,request:Request,user=Depends(admin)):
        cfg=request.app.state.settings
        with connect(cfg) as db,transaction(db):return confirm_link(db,cfg,user,body.request_id)

    @app.delete('/api/v1/telegram/connection',status_code=204,tags=['Система'],summary='Отключить корпоративную группу',description='Администратор отключает текущую группу и отменяет ещё не отправленные напоминания. Отправленные и неоднозначные результаты сохраняются. Требуется CSRF.')
    def remove(request:Request,user=Depends(admin)):
        cfg=request.app.state.settings
        with connect(cfg) as db,transaction(db):
            recover_expired_deliveries(db,int(time.time()))
            if db.execute("""SELECT 1 FROM reminder_outbox b JOIN payment_occurrences o ON o.id=b.occurrence_id
              JOIN recurring_payments r ON r.id=o.recurring_payment_id JOIN users owner ON owner.id=r.owner_id
              WHERE b.state='sending' AND owner.team_id=? LIMIT 1""", (user['team_id'],)).fetchone():
                raise HTTPException(409,'Дождитесь завершения текущего уведомления.')
            disconnect(db,user)
