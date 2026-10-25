from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 full_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','user')),
 env_managed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_login_attempts ON login_attempts(key, created_at);
CREATE TABLE IF NOT EXISTS recurring_payments (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 currency TEXT NOT NULL CHECK(currency IN ('RUB','USD','EUR')),
 initial_due_date TEXT NOT NULL, next_materialize_date TEXT NOT NULL,
 anchor_day INTEGER NOT NULL CHECK(anchor_day BETWEEN 1 AND 31),
 frequency TEXT NOT NULL DEFAULT 'monthly', active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, created_by INTEGER
);
CREATE TABLE IF NOT EXISTS payment_occurrences (
 id INTEGER PRIMARY KEY, recurring_payment_id INTEGER NOT NULL REFERENCES recurring_payments(id),
 name TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
 currency TEXT NOT NULL, due_date TEXT NOT NULL, is_paid INTEGER NOT NULL DEFAULT 0,
 paid_at TEXT, updated_at TEXT NOT NULL,
 UNIQUE(recurring_payment_id,due_date),
 CHECK((is_paid=0 AND paid_at IS NULL) OR (is_paid=1 AND paid_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_occurrences_date ON payment_occurrences(due_date);
CREATE TABLE IF NOT EXISTS reminder_outbox (
 id INTEGER PRIMARY KEY, occurrence_id INTEGER NOT NULL UNIQUE REFERENCES payment_occurrences(id),
 state TEXT NOT NULL CHECK(state IN ('pending','sending','retry','sent','cancelled','unknown')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until INTEGER, sent_at TEXT, last_error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT NOT NULL,
 action TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE, full_name TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL, created_at TEXT NOT NULL,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 accepted_at INTEGER, accepted_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_invitations_username ON invitations(username);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_user_id(db):
    """Reserve historical author identities instead of reusing deleted ROWIDs.

    Call inside BEGIN IMMEDIATE. Existing legacy created_by values stay intact,
    including missing users, and must never become a different new participant.
    """
    highest = db.execute("""SELECT MAX(identifier) FROM (
      SELECT id AS identifier FROM users
      UNION ALL SELECT created_by FROM recurring_payments WHERE typeof(created_by)='integer' AND created_by>0
      UNION ALL SELECT user_id FROM audit_events WHERE typeof(user_id)='integer' AND user_id>0
    )""").fetchone()[0] or 0
    if highest >= 2**63 - 1:
        raise RuntimeError("Невозможно выделить новый идентификатор пользователя.")
    return highest + 1


@contextmanager
def connect(settings):
    connection = sqlite3.connect(settings.database_path, timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def initialize(settings):
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(settings) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        with transaction(db):
            admin = db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()
            collision = db.execute("SELECT id FROM users WHERE username=? AND env_managed=0", (settings.admin_username,)).fetchone()
            if collision:
                raise RuntimeError("ADMIN_USERNAME уже занят обычным пользователем. Выберите другой логин в .env.")
            if admin:
                if admin["username"] != settings.admin_username or admin["password_hash"] != settings.admin_password_hash:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (admin["id"],))
                    db.execute("UPDATE users SET username=?,password_hash=? WHERE id=?", (settings.admin_username, settings.admin_password_hash, admin["id"]))
            else:
                db.execute("INSERT INTO users(id,username,full_name,password_hash,role,env_managed,created_at) VALUES(?,?,?,?,'admin',1,?)",
                           (next_user_id(db), settings.admin_username, "Администратор", settings.admin_password_hash, utcnow()))
            admin_id = db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()["id"]
            columns = {row["name"] for row in db.execute("PRAGMA table_info(recurring_payments)")}
            if "owner_id" not in columns:
                # SQLite cannot add a NOT NULL foreign key without a constant
                # default. Backfill in this transaction, then enforce nullability
                # with triggers for legacy tables; fresh tables also use NOT NULL.
                db.execute("ALTER TABLE recurring_payments ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT")
            db.execute("""UPDATE recurring_payments SET owner_id=CASE
              WHEN EXISTS(SELECT 1 FROM users WHERE users.id=recurring_payments.created_by)
              THEN created_by ELSE ? END WHERE owner_id IS NULL""", (admin_id,))
            db.execute("CREATE INDEX IF NOT EXISTS ix_recurring_owner ON recurring_payments(owner_id)")
            db.execute("""CREATE TRIGGER IF NOT EXISTS recurring_owner_required_insert
              BEFORE INSERT ON recurring_payments WHEN NEW.owner_id IS NULL
              BEGIN SELECT RAISE(ABORT,'owner_id is required'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS recurring_owner_required_update
              BEFORE UPDATE OF owner_id ON recurring_payments WHEN NEW.owner_id IS NULL
              BEGIN SELECT RAISE(ABORT,'owner_id is required'); END""")
            from .telegram_connections import initialize_telegram
            initialize_telegram(db, settings)


def audit(db, user, action, description):
    db.execute("INSERT INTO audit_events(user_id,username,action,description,created_at) VALUES(?,?,?,?,?)",
               (user["id"] if user else None, user["username"] if user else "system", action, description, utcnow()))
