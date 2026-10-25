"""One-use, 72-hour invitations. Raw secrets are returned once, never stored."""
from datetime import datetime, timezone
import secrets
import sqlite3
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Path as ApiPath, Request, Response

from .db import audit, connect, next_user_id, transaction, utcnow
from .models import InvitationAccept, InvitationCreate, InvitationToken
from .security import csrf_token, digest, hash_password

TTL_SECONDS = 72 * 3600
COOKIE = "ik_session"


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def public_invitation(row, now):
    status = "accepted" if row["accepted_at"] is not None else (
        "revoked" if row["revoked_at"] is not None else ("expired" if row["expires_at"] <= now else "active"))
    return {"id": row["id"], "username": row["username"], "full_name": row["full_name"],
            "created_at": row["created_at"], "expires_at": timestamp(row["expires_at"]), "status": status}


def active_invitation(db, token_hash, now):
    row = db.execute("""SELECT * FROM invitations WHERE token_hash=? AND accepted_at IS NULL
      AND revoked_at IS NULL AND expires_at>?""", (token_hash, now)).fetchone()
    if not row:
        raise HTTPException(404, "Приглашение недействительно или срок его действия истёк.")
    return row


def public_access(request, token):
    cfg = request.app.state.settings
    if request.headers.get("origin") != cfg.origin:
        raise HTTPException(403, "Откройте приглашение с адреса этого приложения.")
    now = int(time.time())
    host = request.client.host if request.client else "unknown"
    limits = [(digest(cfg.app_secret, "invitation:ip:" + host), 30),
              (digest(cfg.app_secret, "invitation:token:" + token), 10)]
    with connect(cfg) as db, transaction(db):
        db.execute("DELETE FROM login_attempts WHERE created_at<?", (now - 900,))
        for key, maximum in limits:
            count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?", (key, now - 600)).fetchone()[0]
            if count >= maximum:
                raise HTTPException(429, "Слишком много обращений к приглашению. Повторите через 10 минут.", headers={"Retry-After": "600"})
        db.executemany("INSERT INTO login_attempts(key,created_at) VALUES(?,?)", [(key, now) for key, _ in limits])
    return cfg


def register_routes(app, current, admin):
    @app.post("/api/v1/invitations", status_code=201, tags=["Пользователи"], summary="Пригласить участника", description="Только администратор. Резервирует свободный логин и создаёт одноразовую ссылку на 72 часа. Участник задаёт пароль самостоятельно. Ссылка возвращается только при создании; сервер хранит её хеш.")
    def create(body: InvitationCreate, request: Request, user=Depends(admin)):
        cfg, now = request.app.state.settings, int(time.time())
        token = secrets.token_urlsafe(32)
        with connect(cfg) as db, transaction(db):
            if db.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone():
                raise HTTPException(409, "Пользователь с таким логином уже существует.")
            if db.execute("SELECT 1 FROM invitations WHERE username=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?", (body.username, now)).fetchone():
                raise HTTPException(409, "Для этого логина уже есть действующее приглашение.")
            identifier = db.execute("""INSERT INTO invitations(username,full_name,token_hash,expires_at,created_at,created_by,team_id)
              VALUES(?,?,?,?,?,?,?)""", (body.username, body.full_name, digest(cfg.app_secret, "invitation:" + token), now + TTL_SECONDS, utcnow(), user["id"], user["team_id"])).lastrowid
            row = db.execute("SELECT * FROM invitations WHERE id=?", (identifier,)).fetchone()
            audit(db, user, "invitation_created", "Создано приглашение: " + body.username)
            return public_invitation(row, now) | {"invitation_url": cfg.origin + "/#invite=" + token}

    @app.get("/api/v1/invitations", tags=["Пользователи"], summary="Получить приглашения", description="Только администратор. Последние 100 приглашений со статусами active, accepted, revoked или expired. Секретные ссылки и хеши не возвращаются.")
    def listing(request: Request, user=Depends(admin)):
        with connect(request.app.state.settings) as db:
            return [public_invitation(row, int(time.time())) for row in db.execute("SELECT * FROM invitations WHERE team_id=? ORDER BY id DESC LIMIT 100", (user["team_id"],))]

    @app.delete("/api/v1/invitations/{invitation_id}", status_code=204, tags=["Пользователи"], summary="Отозвать приглашение", description="Только администратор. Запрещает использование ещё не принятой ссылки. Уже созданный по приглашению пользователь этим действием не удаляется.")
    def revoke(invitation_id: Annotated[int, ApiPath(title="Идентификатор приглашения", description="Числовой идентификатор приглашения из списка администратора.")], request: Request, user=Depends(admin)):
        with connect(request.app.state.settings) as db, transaction(db):
            row = db.execute("SELECT * FROM invitations WHERE id=? AND team_id=?", (invitation_id, user["team_id"])).fetchone()
            if not row:
                raise HTTPException(404, "Приглашение не найдено.")
            if row["accepted_at"] is not None:
                raise HTTPException(409, "Приглашение уже принято. Управляйте созданным пользователем в списке участников.")
            if row["revoked_at"] is None:
                db.execute("UPDATE invitations SET revoked_at=? WHERE id=?", (int(time.time()), invitation_id))
                audit(db, user, "invitation_revoked", "Отозвано приглашение: " + row["username"])
        return Response(status_code=204)

    @app.post("/api/v1/invitations/inspect", tags=["Пользователи"], summary="Проверить одноразовую ссылку", description="Без входа. Требует точного Origin приложения. Возвращает только имя, логин и срок действующего приглашения; финансовые данные недоступны. Обращения ограничены по частоте.")
    def inspect(body: InvitationToken, request: Request):
        cfg = public_access(request, body.token)
        with connect(cfg) as db:
            row = active_invitation(db, digest(cfg.app_secret, "invitation:" + body.token), int(time.time()))
            return {"username": row["username"], "full_name": row["full_name"], "expires_at": timestamp(row["expires_at"])}

    @app.post("/api/v1/invitations/accept", tags=["Пользователи"], summary="Принять приглашение", description="Без входа. Требует точного Origin приложения и одноразового токена. Создаёт обычного участника с выбранным паролем, использует приглашение и выдаёт новую сессию. Старая сессия этого браузера отзывается. Повторное принятие запрещено.")
    def accept(body: InvitationAccept, request: Request, response: Response):
        cfg = public_access(request, body.token)
        token_hash = digest(cfg.app_secret, "invitation:" + body.token)
        # Reject bad/expired tokens before expensive password hashing. Recheck
        # inside the write transaction after hashing to prevent concurrent reuse.
        with connect(cfg) as db:
            active_invitation(db, token_hash, int(time.time()))
        encoded = hash_password(body.password)
        session_token = secrets.token_urlsafe(32)
        try:
            with connect(cfg) as db, transaction(db):
                now = int(time.time())
                invitation = active_invitation(db, token_hash, now)
                if db.execute("SELECT 1 FROM users WHERE username=?", (invitation["username"],)).fetchone():
                    raise HTTPException(409, "Логин приглашения уже занят. Обратитесь к администратору.")
                identifier = db.execute("""INSERT INTO users(id,username,full_name,password_hash,role,created_at,team_id)
                  VALUES(?,?,?,?,'user',?,?)""", (next_user_id(db), invitation["username"], invitation["full_name"], encoded, utcnow(), invitation["team_id"])).lastrowid
                db.execute("UPDATE invitations SET accepted_at=?,accepted_user_id=? WHERE id=?", (now, identifier, invitation["id"]))
                old_token = request.cookies.get(COOKIE, "")
                if old_token:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(cfg.app_secret, old_token),))
                db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (digest(cfg.app_secret, session_token), identifier, now + cfg.session_hours * 3600, now))
                row = db.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
                audit(db, row, "invitation_accepted", "Принято приглашение: " + row["username"])
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Приглашение не может быть принято. Обратитесь к администратору.") from None
        response.set_cookie(COOKIE, session_token, max_age=cfg.session_hours * 3600, httponly=True, secure=cfg.cookie_secure, samesite="strict", path="/")
        return {"user": {key: row[key] for key in ("id", "username", "full_name", "role")}, "csrf_token": csrf_token(cfg.app_secret, session_token)}
