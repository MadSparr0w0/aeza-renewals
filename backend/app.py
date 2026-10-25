"""Same-origin API. All financial data requires an authenticated session."""
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated
import hmac
import re
import secrets
import sqlite3
import time

from fastapi import Depends, FastAPI, HTTPException, Path as ApiPath, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings, load_settings
from .db import audit, connect, initialize, next_user_id, transaction, utcnow
from .domain import CURRENCIES, add_recurring, amount_minor, materialize, money, month_end, occurrence_json, schedule_reminders
from .models import Login, OccurrencePatch, PasswordChange, RecurringCreate, RecurringUpdate, UserCreate, UserPatch
from .security import csrf_token, digest, hash_password, verify_password

COOKIE = "ik_session"
OCCURRENCES = "SELECT o.*,r.active AS recurring_active,r.owner_id,owner.full_name AS owner_name FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id JOIN users owner ON owner.id=r.owner_id"
RECURRING = "SELECT r.*,owner.full_name AS owner_name FROM recurring_payments r JOIN users owner ON owner.id=r.owner_id"
# A fixed valid hash preserves the expensive password-check path for unknown users.
DUMMY_HASH = "scrypt$16384$8$5$" + "00" * 16 + "$" + "00" * 64


def validation_message(error):
    """Localize validation safely, without reflecting values or credentials."""
    kind = error["type"]
    messages = {
        "missing": "Обязательное поле не заполнено.",
        "extra_forbidden": "Это поле не предусмотрено запросом.",
        "string_type": "Ожидается текстовое значение.",
        "string_pattern_mismatch": "Недопустимые символы. Разрешены латинские буквы, цифры и символы _, -, ., @.",
        "int_type": "Ожидается целое число.", "int_parsing": "Укажите корректное целое число.",
        "float_type": "Ожидается число.", "float_parsing": "Укажите корректное число.",
        "bool_type": "Укажите логическое значение true или false.",
        "bool_parsing": "Укажите логическое значение true или false.",
        "literal_error": "Выберите одно из допустимых значений, указанных в описании поля.",
        "json_invalid": "Некорректный JSON. Проверьте формат тела запроса.",
        "model_attributes_type": "Ожидается объект с полями запроса.",
        "dict_type": "Ожидается объект с полями запроса.",
    }
    if kind in {"string_too_short", "string_too_long"}:
        context = error.get("ctx", {})
        bound = context.get("min_length" if kind == "string_too_short" else "max_length")
        return ("Минимальная длина: " if kind == "string_too_short" else "Максимальная длина: ") + str(bound) + " символов."
    if kind.startswith("date"):
        return "Укажите существующую календарную дату в формате YYYY-MM-DD."
    if kind == "value_error":
        message = error.get("msg", "").removeprefix("Value error, ")
        if re.match(r"^[А-ЯЁа-яё]", message):
            return message
    return messages.get(kind, "Некорректное значение поля. Проверьте тип данных и ограничения.")


def public_user(row):
    return {key: row[key] for key in ("id", "username", "full_name", "role")}


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or load_settings()
        initialize(app.state.settings)
        with connect(app.state.settings) as db, transaction(db):
            materialize(db, app.state.settings.today)
            schedule_reminders(db, app.state.settings.today)
        yield

    app = FastAPI(title="API регулярных платежей", version="1.1.0", description="Учёт ежемесячных расходов команды: коммунальные услуги, аренда, подписки и другие платежи. Участник видит и меняет свои записи; администратор управляет всеми платежами и участниками. Оплата отмечается вручную и не проводит банковскую операцию. Telegram отправляет уведомления только в подтверждённую общую группу, где участники видят владельца, название, сумму, валюту и срок. Сначала войдите в веб-панель и откройте документацию в том же браузере: сессионный cookie HttpOnly передаётся автоматически. Для изменяющих запросов скопируйте csrf_token из GET /api/v1/auth/me в csrfHeader окна «Авторизация». Сессионный cookie вручную вводить не нужно.", docs_url=None, openapi_url="/api/v1/openapi.json", redoc_url=None, lifespan=lifespan,
                  openapi_tags=[{"name": "Система", "description": "Состояние приложения, общие настройки и журнал действий."},
                                {"name": "Авторизация", "description": "Вход, проверка текущей учётной записи и завершение сессии."},
                                {"name": "Регулярные платежи", "description": "Ежемесячные расходы с владельцем и сохранением истории."},
                                {"name": "Периоды оплаты", "description": "Сроки, ручные отметки, фактические даты оплаты и просрочка отдельных периодов."},
                                {"name": "Итоги", "description": "Плановые, оплаченные, неоплаченные и просроченные суммы отдельно по валютам."},
                                {"name": "Пользователи", "description": "Управление обычными пользователями. Доступно только администратору."}])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo credentials or entire request payloads in validation responses.
        return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "msg": validation_message(e), "type": e["type"]} for e in exc.errors()]})

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path in {"/api/v1/docs", "/api/v1/openapi.json"}:
            try:
                current(request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 65536:
                    return JSONResponse({"detail": "Размер запроса превышает 64 КБ."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
            if size and not request.headers.get("content-type", "").lower().startswith("application/json"):
                return JSONResponse({"detail": "Ожидается application/json."}, status_code=415)
            cfg = request.app.state.settings
            origin = request.headers.get("origin")
            if origin and origin not in {cfg.origin, *cfg.allowed_origins}:
                return JSONResponse({"detail": "Источник запроса не разрешён."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Межсайтовый запрос отклонён."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/api/v1/docs":
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.app.state.settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def current(request: Request):
        cfg = request.app.state.settings
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 128:
            raise HTTPException(401, "Войдите в аккаунт.")
        with connect(cfg) as db:
            row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (digest(cfg.app_secret, token), int(time.time()))).fetchone()
        if not row:
            raise HTTPException(401, "Сессия завершена. Войдите снова.")
        user = dict(row)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            provided = request.headers.get("x-csrf-token", "")
            expected = csrf_token(cfg.app_secret, token)
            if not provided.isascii() or len(provided) != 64 or not hmac.compare_digest(provided, expected):
                raise HTTPException(403, "Недействительный CSRF-токен. Обновите страницу.")
        return user

    def admin(user=Depends(current)):
        if user["role"] != "admin":
            raise HTTPException(403, "Доступно только администратору.")
        return user

    def owner_scope(user, alias="r"):
        return ("", ()) if user["role"] == "admin" else (" AND " + alias + ".owner_id=?", (user["id"],))

    def recurring_row(db, payment_id, user):
        condition, values = owner_scope(user)
        return db.execute(RECURRING + " WHERE r.id=?" + condition, (payment_id, *values)).fetchone()

    def refresh(db, cfg, month=None, user=None):
        horizon = None
        if month:
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise HTTPException(422, "Месяц должен иметь формат YYYY-MM.")
            try:
                first = date.fromisoformat(month + "-01")
            except ValueError:
                raise HTTPException(422, "Некорректный месяц.") from None
            if first.year < 2000 or first.year > cfg.today.year + 5:
                raise HTTPException(422, "Допустимы месяцы от 2000 года до пяти лет вперёд.")
            horizon = month_end(first)
        owner_id = user["id"] if user and user["role"] != "admin" else None
        materialize(db, cfg.today, horizon, owner_id=owner_id)
        schedule_reminders(db, cfg.today, owner_id=owner_id)

    def recurring_json(db, row, today, user):
        due = db.execute("SELECT MIN(due_date) FROM payment_occurrences WHERE recurring_payment_id=? AND due_date>=?", (row["id"], today.isoformat())).fetchone()[0]
        return {"id": row["id"], "name": row["name"], "amount": money(row["amount_minor"]), "currency": row["currency"], "next_due_date": due or row["next_materialize_date"], "anchor_day": row["anchor_day"], "frequency": "monthly", "active": bool(row["active"]), "owner_id": row["owner_id"], "owner_name": row["owner_name"], "can_manage": user["role"] == "admin" or user["id"] == row["owner_id"]}

    @app.get("/api/v1/docs", include_in_schema=False)
    def swagger_docs(user=Depends(current)):
        page = get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="API регулярных платежей — документация",
                                   swagger_js_url="/api/v1/docs-assets/swagger-ui-bundle.js",
                                   swagger_css_url="/api/v1/docs-assets/swagger-ui.css",
                                   swagger_favicon_url="/favicon.svg",
                                   swagger_ui_parameters={"validatorUrl": None, "persistAuthorization": False})
        html = page.body.decode("utf-8").replace("<html>", '<html lang="ru">')
        html = html.replace("</body>", '<script src="/api/v1/docs-assets/ru.js"></script>\n</body>')
        return Response(html, media_type="text/html")

    @app.get("/api/v1/docs-assets/{filename}", include_in_schema=False)
    def swagger_asset(filename: str):
        # A strict basename allowlist prevents untrusted Windows path resolution.
        media_types = {"swagger-ui-bundle.js": "text/javascript", "swagger-ui.css": "text/css", "ru.js": "text/javascript"}
        if filename not in media_types:
            raise HTTPException(404, "Файл документации не найден.")
        return FileResponse(ROOT / "backend" / "static" / "swagger" / filename, media_type=media_types[filename])

    @app.get("/api/v1/health", tags=["Система"], summary="Проверить состояние приложения", description="Проверяет доступность HTTP-сервера. Не требует входа и не раскрывает финансовые данные.")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/auth/login", tags=["Авторизация"], summary="Войти в систему", description="Проверяет учётную запись модуля, устанавливает сессионный cookie и возвращает пользователя вместе с csrf_token. Повторные попытки входа ограничены. Вход не даёт доступа к чужим платежам обычному участнику.")
    def login(body: Login, request: Request, response: Response):
        cfg = request.app.state.settings
        now = int(time.time())
        host = request.client.host if request.client else "unknown"
        key = digest(cfg.app_secret, "login:" + host + ":" + body.username.lower())
        ipkey = digest(cfg.app_secret, "ip:" + host)
        with connect(cfg) as db:
            with transaction(db):
                db.execute("DELETE FROM login_attempts WHERE created_at<?", (now - 900,))
                db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
                count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?", (key, now - 600)).fetchone()[0]
                ipcount = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?", (ipkey, now - 600)).fetchone()[0]
                if count >= 8 or ipcount >= 60:
                    raise HTTPException(429, "Слишком много попыток входа. Повторите через 10 минут.", headers={"Retry-After": "600"})
                # Count before password work so parallel attempts cannot bypass the limit.
                db.executemany("INSERT INTO login_attempts(key,created_at) VALUES(?,?)", [(key, now), (ipkey, now)])
            row = db.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
            if not verify_password(body.password, row["password_hash"] if row else DUMMY_HASH) or not row:
                raise HTTPException(401, "Неверный логин или пароль.")
            token = secrets.token_urlsafe(32)
            with transaction(db):
                # Account edits/deletions concurrent with password verification must
                # not create a fresh session for the old credentials afterwards.
                latest = db.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
                if not latest or latest["password_hash"] != row["password_hash"] or latest["username"] != row["username"]:
                    raise HTTPException(401, "Данные аккаунта изменились. Повторите вход.")
                row = latest
                db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
                old_token = request.cookies.get(COOKIE)
                if old_token:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(cfg.app_secret, old_token),))
                db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (digest(cfg.app_secret, token), row["id"], now + cfg.session_hours * 3600, now))
                audit(db, row, "login", "Вход в систему")
        response.set_cookie(COOKIE, token, max_age=cfg.session_hours * 3600, httponly=True, secure=cfg.cookie_secure, samesite="strict", path="/")
        return {"user": public_user(row), "csrf_token": csrf_token(cfg.app_secret, token)}

    @app.get("/api/v1/auth/me", tags=["Авторизация"], summary="Получить текущего пользователя", description="Возвращает данные вошедшего пользователя и csrf_token для изменяющих запросов. Пароли и сессионный токен в ответ не передаются.")
    def me(request: Request, user=Depends(current)):
        return {"user": public_user(user), "csrf_token": csrf_token(request.app.state.settings.app_secret, request.cookies[COOKIE])}

    @app.post("/api/v1/auth/logout", status_code=204, tags=["Авторизация"], summary="Завершить текущую сессию", description="Отзывает серверную сессию и удаляет сессионный cookie браузера. Требуется действующий csrf_token в заголовке X-CSRF-Token.")
    def logout(request: Request, response: Response, user=Depends(current)):
        cfg = request.app.state.settings
        with connect(cfg) as db, transaction(db):
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(cfg.app_secret, request.cookies[COOKIE]),))
            audit(db, user, "logout", "Выход из системы")
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=cfg.cookie_secure, samesite="strict")

    @app.post("/api/v1/auth/password", status_code=204, tags=["Авторизация"], summary="Сменить свой пароль", description="Обычный участник указывает текущий и новый пароль. После успешной смены все его сессии отзываются и нужен повторный вход. Конфигурационный администратор управляется только окружением.")
    def password_change(body: PasswordChange, request: Request, response: Response, user=Depends(current)):
        cfg = request.app.state.settings
        if user["env_managed"] or user["role"] == "admin":
            raise HTTPException(403, "Пароль администратора управляется только через окружение.")
        now = int(time.time())
        key = digest(cfg.app_secret, "password-change:" + str(user["id"]))
        with connect(cfg) as db, transaction(db):
            db.execute("DELETE FROM login_attempts WHERE created_at<?", (now - 900,))
            count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?", (key, now - 600)).fetchone()[0]
            if count >= 8:
                raise HTTPException(429, "Слишком много попыток смены пароля. Повторите через 10 минут.", headers={"Retry-After": "600"})
            db.execute("INSERT INTO login_attempts(key,created_at) VALUES(?,?)", (key, now))
        if not verify_password(body.current_password, user["password_hash"]):
            raise HTTPException(400, "Текущий пароль указан неверно.")
        encoded = hash_password(body.new_password)
        with connect(cfg) as db, transaction(db):
            latest = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if not latest or latest["password_hash"] != user["password_hash"]:
                raise HTTPException(401, "Данные аккаунта изменились. Войдите снова.")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
            audit(db, user, "password_changed", "Участник изменил собственный пароль")
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=cfg.cookie_secure, samesite="strict")

    @app.get("/api/v1/recurring-payments", tags=["Регулярные платежи"], summary="Получить регулярные платежи", description="Участник получает только собственные серии, администратор — все. По умолчанию показаны действующие серии; прекращённые можно включить отдельным параметром. Ответ содержит owner_id, owner_name и can_manage.")
    def recurring_list(request: Request, include_inactive: bool = Query(default=False, title="Включить прекращённые платежи", description="true — добавить прекращённые регулярные платежи; false — показать только действующие."), user=Depends(current)):
        cfg = request.app.state.settings
        with connect(cfg) as db, transaction(db):
            refresh(db, cfg, user=user)
            condition, values = owner_scope(user)
            rows = db.execute(RECURRING + " WHERE 1=1" + ("" if include_inactive else " AND r.active=1") + condition + " ORDER BY r.id DESC", values).fetchall()
            return [recurring_json(db, row, cfg.today, user) for row in rows]

    @app.post("/api/v1/recurring-payments", status_code=201, tags=["Регулярные платежи"], summary="Добавить регулярный платёж", description="Участник создаёт собственный ежемесячный расход; администратор может указать owner_id другого участника. Автор created_by фиксируется сервером и не подменяется владельцем. Добавляет первый период оплаты. Уведомление направляется только в подтверждённую общую группу за три дня до срока или позже при позднем добавлении.")
    def recurring_create(body: RecurringCreate, request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        if not 2000 <= body.next_due_date.year <= cfg.today.year + 5:
            raise HTTPException(422, "Дата оплаты должна быть от 2000 года до пяти лет вперёд.")
        owner_id = body.owner_id if body.owner_id is not None else user["id"]
        if user["role"] != "admin" and owner_id != user["id"]:
            raise HTTPException(403, "Создавать платёж для другого участника может только администратор.")
        with connect(cfg) as db, transaction(db):
            if not db.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
                raise HTTPException(422, "Указанный владелец платежа не найден.")
            recurring_id = add_recurring(db, name=body.name, amount=body.amount, currency=body.currency, next_due_date=body.next_due_date, created_by=user["id"], owner_id=owner_id)
            refresh(db, cfg, user=user)
            audit(db, user, "payment_created", "Добавлен регулярный платёж: " + body.name)
            return recurring_json(db, recurring_row(db, recurring_id, user), cfg.today, user)

    @app.patch("/api/v1/recurring-payments/{payment_id}", tags=["Регулярные платежи"], summary="Изменить условия серии со следующего месяца", description="Владелец или администратор. Все три поля обязательны: название, сумма и валюта. Новые условия применяются к серии и её неоплаченным периодам со сроком начиная с первого числа следующего месяца. Текущий месяц, прошлые и оплаченные будущие периоды сохраняются; график, владелец, автор и идентификаторы не меняются. Напоминания не сбрасываются. Ответ содержит effective_from. Одинаковые после нормализации значения не создают событие журнала. Чужой идентификатор для обычного участника возвращает 404.", responses={404: {"description": "Регулярный платёж не найден."}, 409: {"description": "Платёж прекращён, его прошлые или текущие периоды ещё не созданы либо напоминание затрагиваемого будущего периода уже отправляется. Изменения не выполнены. Для создания пропущенных периодов сначала обновите список платежей."}})
    def recurring_update(payment_id: Annotated[int, ApiPath(title="Идентификатор регулярного платежа", description="Числовой идентификатор активной серии, условия которой нужно изменить.")], body: RecurringUpdate, request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        effective_from = (month_end(cfg.today) + timedelta(days=1)).isoformat()
        new_amount = amount_minor(body.amount)
        with connect(cfg) as db, transaction(db):
            row = recurring_row(db, payment_id, user)
            if not row:
                raise HTTPException(404, "Регулярный платёж не найден.")
            if not row["active"]:
                raise HTTPException(409, "Регулярный платёж прекращён. Его условия изменить нельзя.")
            if (row["name"], row["amount_minor"], row["currency"]) == (body.name, new_amount, body.currency):
                return recurring_json(db, row, cfg.today, user) | {"effective_from": effective_from}
            if row["next_materialize_date"] < effective_from:
                raise HTTPException(409, "Сначала обновите список платежей, затем повторите изменение условий.")
            sending = db.execute("""SELECT 1 FROM reminder_outbox b
              JOIN payment_occurrences o ON o.id=b.occurrence_id
              WHERE o.recurring_payment_id=? AND o.is_paid=0 AND o.due_date>=? AND b.state='sending'
              LIMIT 1""", (payment_id, effective_from)).fetchone()
            if sending:
                raise HTTPException(409, "Напоминание будущего периода сейчас отправляется. Повторите изменение после завершения отправки.")
            db.execute("UPDATE recurring_payments SET name=?,amount_minor=?,currency=? WHERE id=?", (body.name, new_amount, body.currency, payment_id))
            db.execute("""UPDATE payment_occurrences SET name=?,amount_minor=?,currency=?,updated_at=?
              WHERE recurring_payment_id=? AND is_paid=0 AND due_date>=?""", (body.name, new_amount, body.currency, utcnow(), payment_id, effective_from))
            audit(db, user, "payment_updated", "Будущие условия «" + body.name + "»: " + money(row["amount_minor"]) + " " + row["currency"] + " → " + money(new_amount) + " " + body.currency + ", с " + effective_from)
            updated = recurring_row(db, payment_id, user)
            return recurring_json(db, updated, cfg.today, user) | {"effective_from": effective_from}

    @app.delete("/api/v1/recurring-payments/{payment_id}", status_code=204, tags=["Регулярные платежи"], summary="Прекратить регулярный платёж", description="Владелец или администратор прекращает учёт новых периодов и напоминания, сохраняя созданные периоды, сроки и отметки оплаты. Внешняя подписка или услуга автоматически не отменяется. Чужая серия возвращает 404.")
    def recurring_delete(payment_id: Annotated[int, ApiPath(title="Идентификатор регулярного платежа", description="Числовой идентификатор доступного платежа.")], request: Request, user=Depends(current)):
        with connect(request.app.state.settings) as db, transaction(db):
            row = recurring_row(db, payment_id, user)
            if not row:
                raise HTTPException(404, "Регулярный платёж не найден.")
            db.execute("UPDATE recurring_payments SET active=0 WHERE id=?", (payment_id,))
            db.execute("UPDATE reminder_outbox SET state='cancelled',last_error='payment_not_required',lease_owner=NULL,lease_until=NULL WHERE state IN ('pending','retry') AND occurrence_id IN(SELECT id FROM payment_occurrences WHERE recurring_payment_id=?)", (payment_id,))
            audit(db, user, "payment_archived", "Прекращён регулярный платёж: " + row["name"])
        return Response(status_code=204)

    def occurrence_rows(db, cfg, user, month=None, currency=None, status=None, search=None):
        refresh(db, cfg, month, user=user)
        conditions, args = [], []
        if user["role"] != "admin":
            conditions.append("r.owner_id=?")
            args.append(user["id"])
        if month:
            conditions.append("substr(o.due_date,1,7)=?")
            args.append(month)
        if currency:
            if currency not in CURRENCIES:
                raise HTTPException(422, "Неизвестная валюта.")
            conditions.append("o.currency=?")
            args.append(currency)
        if status:
            if status not in {"paid", "unpaid", "overdue", "pending", "all"}:
                raise HTTPException(422, "Неизвестный статус.")
            if status == "paid":
                conditions.append("o.is_paid=1")
            elif status != "all":
                conditions.append("o.is_paid=0")
                if status in {"overdue", "pending"}:
                    conditions.append("o.due_date" + ("<?" if status == "overdue" else ">=?"))
                    args.append(cfg.today.isoformat())
        if search:
            conditions.append("instr(lower(o.name),lower(?))>0")
            args.append(search)
        query = OCCURRENCES + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY o.due_date,o.id"
        return db.execute(query, args).fetchall()

    @app.get("/api/v1/payment-occurrences", tags=["Периоды оплаты"], summary="Получить периоды оплаты", description="Участник получает только периоды своих серий, администратор — все. Ответ содержит владельца, сумму, валюту, срок, отметку, дату и календарную просрочку. История сохраняется независимо от следующего периода.")
    def occurrences(request: Request, month: str | None = Query(default=None, title="Месяц", description="Фильтр по месяцу срока оплаты в формате YYYY-MM. Без параметра возвращаются все созданные периоды."), currency: str | None = Query(default=None, title="Валюта", description="Фильтр по валюте: RUB — рубли, USD — доллары, EUR — евро."), status: str | None = Query(default=None, title="Статус оплаты", description="paid — оплачено, unpaid — не оплачено, overdue — просрочено, pending — срок ещё не прошёл, all — все статусы."), search: str | None = Query(default=None, max_length=160, title="Поиск по названию", description="Текст для поиска внутри названия платежа."), user=Depends(current)):
        cfg = request.app.state.settings
        with connect(cfg) as db, transaction(db):
            return [occurrence_json(row, cfg.today, user) for row in occurrence_rows(db, cfg, user, month, currency, status, search)]

    @app.get("/api/v1/payment-occurrences/{occurrence_id}", tags=["Периоды оплаты"], summary="Получить конкретный период оплаты", description="Возвращает данные одного периода, его фактическую дату оплаты и актуальную просрочку в календарных днях.")
    def occurrence_get(occurrence_id: Annotated[int, ApiPath(title="Идентификатор периода оплаты", description="Числовой идентификатор отдельного периода из списка оплат.")], request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        with connect(cfg) as db, transaction(db):
            refresh(db, cfg, user=user)
            condition, values = owner_scope(user)
            row = db.execute(OCCURRENCES + " WHERE o.id=?" + condition, (occurrence_id, *values)).fetchone()
            if not row:
                raise HTTPException(404, "Период оплаты не найден.")
            return occurrence_json(row, cfg.today, user)

    @app.patch("/api/v1/payment-occurrences/{occurrence_id}", tags=["Периоды оплаты"], summary="Изменить отметку или дату оплаты", description="Владелец или администратор может подтвердить или отменить отметку оплаты периода и исправить фактическую дату. Это ручной учёт, а не банковский платёж. Отмена очищает дату; исправление пересчитывает просрочку. Исходный срок и другие периоды не меняются. Чужой период возвращает 404.")
    def occurrence_patch(occurrence_id: Annotated[int, ApiPath(title="Идентификатор периода оплаты", description="Числовой идентификатор периода, отметку которого нужно изменить.")], body: OccurrencePatch, request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        fields = body.model_fields_set
        with connect(cfg) as db, transaction(db):
            condition, values = owner_scope(user)
            row = db.execute(OCCURRENCES + " WHERE o.id=?" + condition, (occurrence_id, *values)).fetchone()
            if not row:
                raise HTTPException(404, "Период оплаты не найден.")
            if not fields or "is_paid" in fields and body.is_paid is None:
                raise HTTPException(422, "Укажите статус или фактическую дату оплаты.")
            if body.paid_at and body.paid_at > cfg.today:
                raise HTTPException(422, "Фактическая дата оплаты не может быть в будущем.")
            paid = body.is_paid if "is_paid" in fields else bool(row["is_paid"])
            if not paid and body.paid_at is not None:
                raise HTTPException(422, "Для неоплаченного периода дата оплаты должна быть пустой.")
            paid_at = ((body.paid_at.isoformat() if body.paid_at else None) if "paid_at" in fields else row["paid_at"]) if paid else None
            if paid and not paid_at:
                paid_at = cfg.today.isoformat()
            db.execute("UPDATE payment_occurrences SET is_paid=?,paid_at=?,updated_at=? WHERE id=?", (int(paid), paid_at, utcnow(), occurrence_id))
            schedule_reminders(db, cfg.today, owner_id=user["id"] if user["role"] != "admin" else None)
            audit(db, user, "payment_paid" if paid else "payment_unpaid", ("Подтверждена оплата: " if paid else "Отменена отметка оплаты: ") + row["name"])
            updated = db.execute(OCCURRENCES + " WHERE o.id=?", (occurrence_id,)).fetchone()
            return occurrence_json(updated, cfg.today, user)

    @app.get("/api/v1/payments/summary", tags=["Итоги"], summary="Получить итоги по валютам", description="Возвращает плановые, оплаченные, неоплаченные и просроченные суммы, а также количество периодов. RUB, USD и EUR считаются отдельно, без конвертации. Суммы передаются строками с двумя знаками после запятой.")
    def summary(request: Request, month: str | None = Query(default=None, title="Месяц итогов", description="Месяц срока оплаты в формате YYYY-MM. Без параметра итоги включают все созданные периоды."), user=Depends(current)):
        cfg = request.app.state.settings
        sums = {c: {"planned": 0, "paid": 0, "unpaid": 0, "overdue": 0} for c in CURRENCIES}
        counts = {"total_count": 0, "paid_count": 0, "unpaid_count": 0, "overdue_count": 0}
        with connect(cfg) as db, transaction(db):
            for row in occurrence_rows(db, cfg, user, month):
                amounts = sums[row["currency"]]
                value = row["amount_minor"]
                amounts["planned"] += value
                amounts["paid" if row["is_paid"] else "unpaid"] += value
                counts["total_count"] += 1
                counts["paid_count" if row["is_paid"] else "unpaid_count"] += 1
                if not row["is_paid"] and row["due_date"] < cfg.today.isoformat():
                    amounts["overdue"] += value
                    counts["overdue_count"] += 1
        return {"currencies": {c: {k: money(v) for k, v in a.items()} for c, a in sums.items()}, **counts}

    @app.get("/api/v1/users", tags=["Пользователи"], summary="Получить список пользователей", description="Только администратор. Возвращает логины, имена и роли. Конфигурационная учётная запись администратора помечена как управляемая через окружение.")
    def users(request: Request, user=Depends(admin)):
        with connect(request.app.state.settings) as db:
            return [public_user(row) | {"env_managed": bool(row["env_managed"])} for row in db.execute("SELECT * FROM users ORDER BY env_managed DESC,id")]

    @app.post("/api/v1/users", status_code=201, tags=["Пользователи"], summary="Создать участника с паролем", description="Только администратор. Создаёт участника, который видит и управляет своими платежами. Альтернатива — одноразовое приглашение, где пароль задаёт сам участник. Создание администратора через этот метод запрещено.")
    def user_create(body: UserCreate, request: Request, user=Depends(admin)):
        encoded = hash_password(body.password)
        try:
            with connect(request.app.state.settings) as db, transaction(db):
                if db.execute("SELECT 1 FROM invitations WHERE username=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?", (body.username, int(time.time()))).fetchone():
                    raise HTTPException(409, "Этот логин зарезервирован действующим приглашением.")
                result = db.execute("INSERT INTO users(id,username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,'user',?)", (next_user_id(db), body.username, body.full_name, encoded, utcnow()))
                row = db.execute("SELECT * FROM users WHERE id=?", (result.lastrowid,)).fetchone()
                audit(db, user, "user_created", "Создан пользователь: " + body.username)
                return public_user(row)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Пользователь с таким логином уже существует.") from None

    @app.patch("/api/v1/users/{user_id}", tags=["Пользователи"], summary="Изменить обычного пользователя", description="Только администратор. Изменяет переданные логин, имя или пароль и завершает все сессии пользователя. Учётную запись администратора можно изменить только через окружение.")
    def user_patch(user_id: Annotated[int, ApiPath(title="Идентификатор пользователя", description="Числовой идентификатор обычного пользователя.")], body: UserPatch, request: Request, user=Depends(admin)):
        if not body.model_fields_set or any(getattr(body, key) is None for key in body.model_fields_set):
            raise HTTPException(422, "Укажите непустые поля для изменения.")
        encoded = hash_password(body.password) if body.password else None
        try:
            with connect(request.app.state.settings) as db, transaction(db):
                row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if not row:
                    raise HTTPException(404, "Пользователь не найден.")
                if row["env_managed"] or row["role"] == "admin":
                    raise HTTPException(403, "Учётная запись администратора управляется только через .env.")
                if body.username and db.execute("SELECT 1 FROM invitations WHERE username=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?", (body.username, int(time.time()))).fetchone():
                    raise HTTPException(409, "Этот логин зарезервирован действующим приглашением.")
                db.execute("UPDATE users SET username=?,full_name=?,password_hash=? WHERE id=?", (body.username or row["username"], body.full_name or row["full_name"], encoded or row["password_hash"], user_id))
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                audit(db, user, "user_updated", "Изменён пользователь: " + (body.username or row["username"]))
                return public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Пользователь с таким логином уже существует.") from None

    @app.delete("/api/v1/users/{user_id}", status_code=204, tags=["Пользователи"], summary="Удалить участника", description="Только администратор. Передаёт все платежи участника администратору и удаляет учётную запись с её сессиями. Автор created_by, суммы, сроки и история сохраняются. Удалить администратора этим методом нельзя.")
    def user_delete(user_id: Annotated[int, ApiPath(title="Идентификатор пользователя", description="Числовой идентификатор удаляемого обычного пользователя.")], request: Request, user=Depends(admin)):
        with connect(request.app.state.settings) as db, transaction(db):
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Пользователь не найден.")
            if row["env_managed"] or row["role"] == "admin":
                raise HTTPException(403, "Учётная запись администратора управляется только через .env.")
            admin_id = db.execute("SELECT id FROM users WHERE env_managed=1").fetchone()["id"]
            transferred = db.execute("UPDATE recurring_payments SET owner_id=? WHERE owner_id=?", (admin_id, user_id)).rowcount
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            audit(db, user, "user_deleted", "Удалён пользователь: " + row["username"] + "; передано платежей администратору: " + str(transferred))
        return Response(status_code=204)

    @app.get("/api/v1/settings", tags=["Система"], summary="Получить общие настройки", description="Название продукта, режим ручного учёта manual, часовой пояс, сегодняшняя дата и срок напоминания. telegram_configured отражает подтверждённую привязку общей группы к текущему боту. Секреты и токен не передаются.")
    def settings_get(request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        from .telegram_connections import is_connected
        with connect(cfg) as db:
            configured = is_connected(db, cfg)
        return {"timezone": cfg.timezone, "today": cfg.today.isoformat(), "telegram_configured": configured, "reminder_days": 3, "product_name": "Учёт регулярных платежей", "integration_mode": "manual"}

    @app.get("/api/v1/activity", tags=["Система"], summary="Получить журнал действий", description="Только администратор. Возвращает последние события входа, изменения платежей и управления пользователями. Пароли и токены в журнале не сохраняются.")
    def activity(request: Request, limit: int = Query(default=30, ge=1, le=100, title="Количество событий", description="Сколько последних событий вернуть: от 1 до 100, по умолчанию 30."), user=Depends(admin)):
        with connect(request.app.state.settings) as db:
            return [dict(row) for row in db.execute("SELECT id,username,action,description,created_at FROM audit_events ORDER BY id DESC LIMIT ?", (limit,))]

    @app.get("/api/v1/reminders", tags=["Система"], summary="Проверить очередь напоминаний", description="Только администратор. Возвращает состояние и число попыток отправки последних 100 напоминаний. Неопределённая доставка имеет статус unknown: автоматический повтор отключён до ручной проверки чата.")
    def reminders(request: Request, user=Depends(admin)):
        with connect(request.app.state.settings) as db:
            return [dict(row) for row in db.execute("""SELECT b.id,b.occurrence_id,b.state,b.attempts,b.next_attempt_at,b.sent_at,b.last_error,o.name,o.due_date
              FROM reminder_outbox b JOIN payment_occurrences o ON o.id=b.occurrence_id ORDER BY b.id DESC LIMIT 100""")]

    from .invitations import register_routes as register_invitation_routes
    register_invitation_routes(app, current, admin)
    from .telegram_connections import register_routes
    register_routes(app, current, admin)

    frontend_dist = ROOT / "frontend" / "dist"
    if (frontend_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Эндпоинт не найден.")
        # Never pass arbitrary URL paths into Windows filesystem resolution:
        # UNC paths can initiate SMB requests before a containment check runs.
        # Build assets have their own StaticFiles mount; only fixed public
        # filenames are needed here. Client routes receive the SPA below.
        if path in {"favicon.svg", "robots.txt", "manifest.webmanifest"}:
            public_file = frontend_dist / path
            if public_file.is_file():
                return FileResponse(public_file)
            raise HTTPException(404, "Файл не найден.")
        index = frontend_dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend не собран. Выполните npm ci && npm run build в frontend/. API: /api/v1/docs."}, status_code=503)

    def openapi_schema():
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes, tags=app.openapi_tags)
        schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
            "sessionCookie": {"type": "apiKey", "in": "cookie", "name": COOKIE, "description": "Защищённый сессионный cookie HttpOnly устанавливается при входе и отправляется браузером автоматически. Сначала войдите через веб-панель; вводить значение вручную не нужно."},
            "csrfHeader": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token", "description": "Скопируйте csrf_token из ответа входа или GET /api/v1/auth/me. Токен обязателен для изменяющих запросов POST, PATCH и DELETE после входа."},
        })
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if not isinstance(operation, dict) or "operationId" not in operation:
                    continue
                if path in {"/api/v1/auth/login", "/api/v1/health", "/api/v1/invitations/inspect", "/api/v1/invitations/accept"}:
                    operation["security"] = []
                else:
                    operation["security"] = [{"sessionCookie": [], **({"csrfHeader": []} if method in {"post", "patch", "put", "delete"} else {})}]
                    operation.setdefault("responses", {})["401"] = {"description": "Сессия отсутствует, истекла или была завершена. Войдите снова."}
                    operation["responses"]["403"] = {"description": "Недостаточно прав, запрещённый адрес запроса или недействительный CSRF-токен."}
                descriptions = {"200": "Запрос выполнен успешно.", "201": "Запись создана.", "204": "Действие выполнено. Тело ответа отсутствует.", "422": "Данные запроса не прошли проверку. Исправьте указанные поля."}
                for code, response in operation.get("responses", {}).items():
                    if code in descriptions:
                        response["description"] = descriptions[code]
                    for content in response.get("content", {}).values():
                        response_schema = content.get("schema", {})
                        if response_schema.get("title", "").startswith("Response "):
                            response_schema["title"] = "Результат операции «" + operation["summary"] + "»"
                if "requestBody" in operation:
                    operation["requestBody"]["description"] = "Поля запроса в формате application/json. Неизвестные поля не принимаются."

        def translate_schema_titles(node):
            # FastAPI adds these shared validation schemas automatically. Keep
            # technical field/$ref keys stable and translate their display titles.
            titles = {"HTTPValidationError": "Ошибка проверки запроса", "ValidationError": "Ошибка поля запроса",
                      "Detail": "Сведения об ошибках", "Location": "Расположение ошибки",
                      "Message": "Сообщение об ошибке", "Error Type": "Тип ошибки",
                      "Context": "Дополнительные сведения", "Input": "Исходные данные"}
            if isinstance(node, dict):
                if node.get("title") in titles:
                    node["title"] = titles[node["title"]]
                for value in node.values():
                    translate_schema_titles(value)
            elif isinstance(node, list):
                for value in node:
                    translate_schema_titles(value)

        translate_schema_titles(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_schema

    return app


app = create_app()
