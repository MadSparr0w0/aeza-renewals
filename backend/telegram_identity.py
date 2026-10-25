"""Passwordless Telegram sign-in, bound to one initiating browser.

Bot deep links carry only a short-lived challenge. The separate HttpOnly
browser secret is never sent to Telegram and is required to redeem it.
There is no signup or role assignment from Telegram usernames.
"""
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from .db import audit, connect, transaction, utcnow
from .security import csrf_token, digest
from .telegram_connections import iso_timestamp, token_scope

BROWSER_COOKIE = 'ik_telegram_browser'
COOKIE_PATH = '/api/v1/auth/telegram'
TTL = 300


def initialize_identity(db):
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_identities (
      user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      telegram_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
      linked_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_auth_challenges (
      id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, browser_hash TEXT NOT NULL,
      mode TEXT NOT NULL CHECK(mode IN ('login','link')),
      user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
      session_hash TEXT, bot_scope TEXT NOT NULL, created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
      telegram_id TEXT, display_name TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS ix_telegram_auth_expiry ON telegram_auth_challenges(expires_at)')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_bot_links (
      token_hash TEXT PRIMARY KEY,telegram_id TEXT NOT NULL,display_name TEXT NOT NULL,
      bot_scope TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
      used_at INTEGER)''')


def make_bot_link(db,cfg,sender,now=None):
    """A user-requested private bot reply; the website still asks consent."""
    now=int(time.time()) if now is None else now
    tid=sender.get('id')
    if isinstance(tid,bool) or not isinstance(tid,int) or not 0<tid<2**63 or sender.get('is_bot'):
        raise HTTPException(400,'Некорректный отправитель Telegram.')
    token=secrets.token_urlsafe(24)
    name=' '.join(str(sender.get(k) or '') for k in ('first_name','last_name')).strip()[:100] or 'Telegram'
    with transaction(db):
        db.execute('DELETE FROM telegram_bot_links WHERE expires_at<?',(now-3600,))
        db.execute('DELETE FROM telegram_bot_links WHERE telegram_id=? AND used_at IS NULL',(str(tid),))
        db.execute('INSERT INTO telegram_bot_links VALUES(?,?,?,?,?,?,NULL)',
          (digest(cfg.app_secret,'bot-link:'+token),str(tid),name,token_scope(cfg.telegram_bot_token),now,now+600))
    return {'url':cfg.origin+'/#telegram='+token,'expires_in':600}


def identity_user(db, telegram_id):
    link=db.execute('SELECT user_id FROM telegram_identities WHERE telegram_id=?',(str(telegram_id),)).fetchone()
    return db.execute('SELECT * FROM users WHERE id=?',(link['user_id'],)).fetchone() if link else None


def bot_challenge(db, cfg, code, sender, *, confirm=False, now=None):
    now = int(time.time()) if now is None else now
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', code or ''):
        return None
    tid = sender.get('id')
    if isinstance(tid, bool) or not isinstance(tid, int) or not 0 < tid < 2**63 or sender.get('is_bot'):
        return None
    with transaction(db):
        row = db.execute('''SELECT * FROM telegram_auth_challenges WHERE code_hash=?
          AND expires_at>? AND state='pending' AND bot_scope=?''',
                         (digest(cfg.app_secret, 'tg-code:'+code), now, token_scope(cfg.telegram_bot_token))).fetchone()
        if row is None:
            return None
        linked = identity_user(db, tid)
        if row['mode'] == 'login' and linked is None:
            return {'error': 'Сначала войдите на сайт по паролю и привяжите Telegram в настройках.'}
        if row['mode'] == 'link':
            target = db.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
            if target is None or (linked and linked['id'] != target['id']):
                return {'error': 'Этот Telegram уже связан с другим аккаунтом.'}
            existing = db.execute('SELECT telegram_id FROM telegram_identities WHERE user_id=?', (target['id'],)).fetchone()
            if existing and existing['telegram_id'] != str(tid):
                return {'error': 'Сначала отключите прежний Telegram в настройках сайта.'}
        else:
            target = linked
        result = {'username': target['username'], 'mode': row['mode']}
        if confirm:
            name = ' '.join(str(sender.get(k) or '') for k in ('first_name','last_name')).strip()[:100] or 'Telegram'
            db.execute('''UPDATE telegram_auth_challenges SET state='confirmed',telegram_id=?,
              display_name=?,user_id=? WHERE id=? AND state='pending' ''',
                       (str(tid), name, target['id'], row['id']))
        return result


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: str = 'login'


class ChallengeId(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str


class BotLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str


def register_routes(app, current, public_user, session_cookie):
    @app.get('/api/v1/auth/telegram/config', tags=['Авторизация'], summary='Доступность входа через Telegram')
    def config(request: Request):
        cfg = request.app.state.settings
        return {'enabled': bool(cfg.telegram_bot_token), 'bot_username': cfg.telegram_bot_username}

    @app.get('/api/v1/auth/telegram/identity', tags=['Авторизация'], summary='Моя привязка Telegram')
    def identity(request: Request, user=Depends(current)):
        with connect(request.app.state.settings) as db:
            row = db.execute('SELECT display_name,linked_at FROM telegram_identities WHERE user_id=?', (user['id'],)).fetchone()
        return {'linked': row is not None, **(dict(row) if row else {})}

    @app.delete('/api/v1/auth/telegram/identity', status_code=204, tags=['Авторизация'], summary='Отключить вход через Telegram')
    def unlink(request: Request, user=Depends(current)):
        cfg = request.app.state.settings
        with connect(cfg) as db, transaction(db):
            db.execute('DELETE FROM telegram_identities WHERE user_id=?', (user['id'],))
            db.execute("UPDATE telegram_auth_challenges SET state='revoked' WHERE user_id=? AND state IN ('pending','confirmed')", (user['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?',
              (user['id'],digest(cfg.app_secret,request.cookies.get(session_cookie,''))))
            audit(db, user, 'telegram_identity_unlinked', 'Отключён вход через Telegram')

    def bot_link_row(db,cfg,token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}',token or ''):
            raise HTTPException(404,'Ссылка привязки не найдена.')
        row=db.execute('''SELECT * FROM telegram_bot_links WHERE token_hash=? AND bot_scope=?
          AND expires_at>? AND used_at IS NULL''',
          (digest(cfg.app_secret,'bot-link:'+token),token_scope(cfg.telegram_bot_token),int(time.time()))).fetchone()
        if row is None:raise HTTPException(410,'Ссылка уже использована или истекла. Запросите новую в боте.')
        return row

    @app.get('/api/v1/auth/telegram/bot-link',tags=['Авторизация'],summary='Проверить приглашение к привязке из бота')
    def bot_link_info(request:Request,token:str):
        cfg=request.app.state.settings
        with connect(cfg) as db:row=bot_link_row(db,cfg,token)
        return {'display_name':row['display_name'],'expires_at':iso_timestamp(row['expires_at'])}

    @app.post('/api/v1/auth/telegram/bot-link',tags=['Авторизация'],summary='Подтвердить привязку к вошедшему аккаунту')
    def bot_link_accept(body:BotLink,request:Request,user=Depends(current)):
        cfg=request.app.state.settings
        with connect(cfg) as db,transaction(db):
            row=bot_link_row(db,cfg,body.token)
            existing=db.execute('SELECT user_id,telegram_id FROM telegram_identities WHERE user_id=? OR telegram_id=?',
              (user['id'],row['telegram_id'])).fetchone()
            if existing and (existing['user_id']!=user['id'] or existing['telegram_id']!=row['telegram_id']):
                raise HTTPException(409,'Telegram уже связан с другим аккаунтом. Сначала отключите прежнюю привязку.')
            db.execute('INSERT OR IGNORE INTO telegram_identities VALUES(?,?,?,?)',
              (user['id'],row['telegram_id'],row['display_name'],utcnow()))
            db.execute('UPDATE telegram_bot_links SET used_at=? WHERE token_hash=?',(int(time.time()),row['token_hash']))
            audit(db,user,'telegram_identity_linked','Подключён вход и личный кабинет Telegram')
        return {'linked':True}

    @app.post('/api/v1/auth/telegram/start', tags=['Авторизация'], summary='Начать подтверждение в Telegram')
    def start(body: ChallengeRequest, request: Request, response: Response):
        cfg = request.app.state.settings
        if not cfg.telegram_bot_token:
            raise HTTPException(409, 'Бот не настроен.')
        if body.mode not in {'login','link'}:
            raise HTTPException(422, 'Неизвестный способ авторизации.')
        user = current(request) if body.mode == 'link' else None
        now = int(time.time())
        host = request.client.host if request.client else 'unknown'
        rate = digest(cfg.app_secret, 'tg-start:'+host)
        identifier, code, browser = secrets.token_urlsafe(18), secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        with connect(cfg) as db, transaction(db):
            db.execute('DELETE FROM telegram_auth_challenges WHERE expires_at<?', (now-3600,))
            db.execute('DELETE FROM login_attempts WHERE created_at<?', (now-900,))
            if db.execute('SELECT COUNT(*) FROM login_attempts WHERE key=? AND created_at>?', (rate, now-600)).fetchone()[0] >= 20:
                raise HTTPException(429, 'Слишком много запросов. Повторите через 10 минут.')
            db.execute('INSERT INTO login_attempts(key,created_at) VALUES(?,?)', (rate,now))
            db.execute('''INSERT INTO telegram_auth_challenges
              (id,code_hash,browser_hash,mode,user_id,session_hash,bot_scope,created_at,expires_at)
              VALUES(?,?,?,?,?,?,?,?,?)''', (identifier,digest(cfg.app_secret,'tg-code:'+code),
                digest(cfg.app_secret,browser),body.mode,user['id'] if user else None,
                digest(cfg.app_secret,request.cookies.get(session_cookie,'')) if user else None,
                token_scope(cfg.telegram_bot_token),now,now+TTL))
        response.set_cookie(BROWSER_COOKIE,browser,max_age=TTL,httponly=True,secure=cfg.cookie_secure,samesite='strict',path=COOKIE_PATH)
        return {'id':identifier,'expires_in':TTL,'url':f'https://t.me/{cfg.telegram_bot_username}?start=auth_{code}'}

    def owned_challenge(db,cfg,request,identifier):
        browser = request.cookies.get(BROWSER_COOKIE,'')
        if not browser or len(browser)>128 or len(identifier)>64:
            raise HTTPException(404,'Запрос входа не найден в этом браузере.')
        row = db.execute('''SELECT * FROM telegram_auth_challenges WHERE id=? AND browser_hash=? AND bot_scope=?''',
                         (identifier,digest(cfg.app_secret,browser),token_scope(cfg.telegram_bot_token))).fetchone()
        if row is None:
            raise HTTPException(404,'Запрос входа не найден в этом браузере.')
        return row

    @app.get('/api/v1/auth/telegram/status', tags=['Авторизация'], summary='Проверить подтверждение Telegram')
    def status(request: Request, id: str):
        cfg = request.app.state.settings
        with connect(cfg) as db:
            row = owned_challenge(db,cfg,request,id)
        state = 'expired' if row['expires_at']<=int(time.time()) else row['state']
        return {'state':state}

    @app.post('/api/v1/auth/telegram/complete', tags=['Авторизация'], summary='Завершить вход в исходном браузере')
    def complete(body: ChallengeId, request: Request, response: Response):
        cfg = request.app.state.settings
        now = int(time.time())
        with connect(cfg) as db, transaction(db):
            row = owned_challenge(db,cfg,request,body.id)
            if row['state']!='confirmed' or row['expires_at']<=now:
                raise HTTPException(409,'Подтверждение недействительно или ещё не получено.')
            if row['mode']=='link':
                user = current(request)
                if user['id']!=row['user_id'] or row['session_hash']!=digest(cfg.app_secret,request.cookies.get(session_cookie,'')):
                    raise HTTPException(403,'Привязку должен завершить тот же аккаунт и браузер.')
                existing = db.execute('SELECT user_id,telegram_id FROM telegram_identities WHERE user_id=? OR telegram_id=?', (user['id'],row['telegram_id'])).fetchone()
                if existing and (existing['user_id']!=user['id'] or existing['telegram_id']!=row['telegram_id']):
                    raise HTTPException(409,'Аккаунт уже связан. Сначала отключите прежнюю привязку.')
                db.execute('INSERT OR IGNORE INTO telegram_identities VALUES(?,?,?,?)', (user['id'],row['telegram_id'],row['display_name'],utcnow()))
                audit(db,user,'telegram_identity_linked','Подключён вход через Telegram')
                result = {'linked':True}
            else:
                user = identity_user(db,row['telegram_id'])
                if user is None or user['id']!=row['user_id']:
                    raise HTTPException(409,'Привязка аккаунта изменена. Начните вход заново.')
                token = secrets.token_urlsafe(32)
                old = request.cookies.get(session_cookie,'')
                db.execute('DELETE FROM sessions WHERE token_hash=? OR expires_at<=?', (digest(cfg.app_secret,old),now))
                db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(cfg.app_secret,token),user['id'],now+cfg.session_hours*3600,now))
                response.set_cookie(session_cookie,token,max_age=cfg.session_hours*3600,httponly=True,secure=cfg.cookie_secure,samesite='strict',path='/')
                audit(db,user,'telegram_login','Вход через Telegram')
                result = {'user':public_user(user),'csrf_token':csrf_token(cfg.app_secret,token)}
            db.execute("UPDATE telegram_auth_challenges SET state='used' WHERE id=?", (row['id'],))
        response.delete_cookie(BROWSER_COOKIE,path=COOKIE_PATH,httponly=True,secure=cfg.cookie_secure,samesite='strict')
        return result
