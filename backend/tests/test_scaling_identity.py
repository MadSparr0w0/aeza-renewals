"""SQL pagination and browser-bound Telegram identity. Synthetic/offline only."""
from contextlib import ExitStack
from datetime import date
import time
from urllib.parse import parse_qs,urlsplit

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.db import connect,initialize,transaction,utcnow
from backend.security import hash_password
from backend.telegram_identity import bot_challenge,make_bot_link

PASSWORD='Offline test password for Telegram!'
ENCODED=hash_password(PASSWORD)


class Frozen(Settings):
    @property
    def today(self):return date(2026,10,25)


@pytest.fixture
def team(tmp_path):
    cfg=Frozen(tmp_path/'scale.db','admin',ENCODED,'synthetic-secret-'*5,telegram_bot_token='123456:FAKE_TEST_ONLY')
    initialize(cfg)
    with connect(cfg) as db,transaction(db):
        ids={'admin':db.execute('SELECT id FROM users WHERE env_managed=1').fetchone()[0]}
        for name in ('alice','boris'):
            ids[name]=db.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,'user',?)",
              (name,'Алиса' if name=='alice' else 'Борис',ENCODED,utcnow())).lastrowid
    with ExitStack() as stack:
        clients={name:stack.enter_context(TestClient(create_app(cfg))) for name in ids}
        headers={}
        for name,client in clients.items():
            result=client.post('/api/v1/auth/login',json={'username':name,'password':PASSWORD})
            assert result.status_code==200
            headers[name]={'X-CSRF-Token':result.json()['csrf_token'],'Origin':cfg.origin}
        yield cfg,clients,headers,ids


def seed_rows(cfg,ids,count=5037):
    with connect(cfg) as db,transaction(db):
        # Two series with already generated horizons. Thousands of periods are
        # inserted directly to measure reads without expensive password work.
        recurring=[]
        for name in ('alice','boris'):
            recurring.append(db.execute('''INSERT INTO recurring_payments
              (name,amount_minor,owner_id,currency,initial_due_date,next_materialize_date,anchor_day,created_at)
              VALUES('Счёт',125,?,'RUB','2026-10-01','2026-12-01',1,?)''',(ids[name],utcnow())).lastrowid)
        # Distinct series for each occurrence preserve the real uniqueness rule.
        db.executemany('''INSERT INTO recurring_payments
          (id,name,amount_minor,owner_id,currency,initial_due_date,next_materialize_date,anchor_day,created_at)
          VALUES(?, ?,125,?,'RUB','2026-10-01','2026-12-01',1,?)''',
          ((100+i,'ЭЛЕКТРИЧЕСТВО '+str(i),ids['alice'] if i<count-7 else ids['boris'],utcnow()) for i in range(count)))
        db.executemany('''INSERT INTO payment_occurrences
          (recurring_payment_id,name,amount_minor,currency,due_date,is_paid,paid_at,updated_at)
          VALUES(?,?,125,?,?,0,NULL,?)''',
          ((100+i,'ЭЛЕКТРИЧЕСТВО '+str(i),'USD' if i%5==0 else 'RUB',f'2026-10-{(i%28)+1:02}',utcnow()) for i in range(count)))


def test_large_page_is_bounded_totals_are_complete_and_owners_are_isolated(team):
    cfg,clients,headers,ids=team
    seed_rows(cfg,ids)
    alice=clients['alice'];admin=clients['admin']
    first=alice.get('/api/v1/payment-occurrences/page?month=2026-10').json()
    assert len(first['items'])==30 and first['total']==5030 and first['has_more']
    second=alice.get('/api/v1/payment-occurrences/page?month=2026-10&offset=30').json()
    assert not {r['id'] for r in first['items']} & {r['id'] for r in second['items']}
    assert all(r['owner_id']==ids['alice'] for r in first['items']+second['items'])
    assert len(alice.get('/api/v1/payment-occurrences?month=2026-10').json())==100
    assert alice.get('/api/v1/payment-occurrences/page?limit=101').status_code==422
    assert alice.get('/api/v1/payment-occurrences/page?offset=-1').status_code==422
    assert alice.get('/api/v1/payment-occurrences/page?sort=DROP').status_code==422
    assert alice.get('/api/v1/payment-occurrences/page?search=электричество').json()['total']==5030
    assert admin.get('/api/v1/payment-occurrences/page?search=БОРИС').json()['total']==7
    result=admin.get('/api/v1/payments/summary?month=2026-10').json()
    assert result['total_count']==5037
    assert sum(result['counts_by_currency'][c]['total_count'] for c in ('RUB','USD','EUR'))==5037
    assert float(result['currencies']['RUB']['planned'])+float(result['currencies']['USD']['planned'])==5037*1.25
    days=alice.get('/api/v1/payments/calendar?month=2026-10').json()
    assert len(days)==28 and sum(d['count'] for d in days)==5030
    chosen=alice.get('/api/v1/payment-occurrences/page?day='+days[0]['date']).json()
    assert chosen['total']==days[0]['count'] and all(r['due_date']==days[0]['date'] for r in chosen['items'])
    assert clients['boris'].get('/api/v1/users/page').status_code==403
    assert admin.get('/api/v1/users/page?search=алиса&limit=1').json()['total']==1
    with connect(cfg) as db:
        plan=' '.join(r['detail'] for r in db.execute("EXPLAIN QUERY PLAN SELECT id FROM payment_occurrences WHERE due_date>='2026-10-01' AND due_date<='2026-10-31' ORDER BY due_date,id LIMIT 30"))
        assert 'INDEX' in plan and 'SCAN payment_occurrences' not in plan


def start(client,headers,mode='link'):
    result=client.post('/api/v1/auth/telegram/start',json={'mode':mode},headers=headers)
    assert result.status_code==200,result.text
    data=result.json();code=parse_qs(urlsplit(data['url']).query)['start'][0].removeprefix('auth_')
    return data,code


def confirm(cfg,code,tid=111):
    with connect(cfg) as db:return bot_challenge(db,cfg,code,{'id':tid,'first_name':'Тест'},confirm=True)


def test_link_signin_require_browser_proof_and_are_single_use(team):
    cfg,clients,headers,ids=team
    alice=clients['alice']
    data,code=start(alice,headers['alice'])
    assert confirm(cfg,code)['username']=='alice'
    with TestClient(create_app(cfg)) as stranger:
        assert stranger.get('/api/v1/auth/telegram/status?id='+data['id']).status_code==404
        assert stranger.post('/api/v1/auth/telegram/complete',json={'id':data['id']}).status_code==404
    assert alice.post('/api/v1/auth/telegram/complete',json={'id':data['id']},headers=headers['alice']).json()=={'linked':True}
    assert alice.get('/api/v1/auth/telegram/identity').json()['linked']
    assert alice.post('/api/v1/auth/telegram/complete',json={'id':data['id']},headers=headers['alice']).status_code in (404,409)
    # Starting a new login carries no session; only original browser can finish.
    with TestClient(create_app(cfg)) as browser:
        request,code=start(browser,{'Origin':cfg.origin},'login')
        assert browser.post('/api/v1/auth/telegram/complete',json={'id':request['id']}).status_code==409
        assert confirm(cfg,code)['username']=='alice'
        result=browser.post('/api/v1/auth/telegram/complete',json={'id':request['id']})
        assert result.status_code==200,result.text
        assert result.json()['user']['id']==ids['alice']
        assert 'httponly' in result.headers['set-cookie'].lower()
        assert browser.get('/api/v1/auth/me').json()['user']['username']=='alice'
        assert confirm(cfg,code) is None


def test_telegram_cannot_register_or_take_over_linked_account(team):
    cfg,clients,headers,ids=team
    with TestClient(create_app(cfg)) as browser:
        data,code=start(browser,{},'login')
        assert 'error' in confirm(cfg,code,222)
        assert browser.get('/api/v1/auth/telegram/status?id='+data['id']).json()['state']=='pending'
    a,code=start(clients['alice'],headers['alice']);confirm(cfg,code,222)
    assert clients['alice'].post('/api/v1/auth/telegram/complete',json={'id':a['id']},headers=headers['alice']).status_code==200
    b,code=start(clients['boris'],headers['boris'])
    assert 'error' in confirm(cfg,code,222)
    assert clients['boris'].post('/api/v1/auth/telegram/complete',json={'id':b['id']},headers=headers['boris']).status_code==409
    assert clients['boris'].post('/api/v1/auth/telegram/start',json={'mode':'link'}).status_code==403
    assert clients['alice'].post('/api/v1/auth/telegram/start',json={'mode':'login'},headers={'Origin':'https://evil.example'}).status_code==403


def test_bot_link_needs_explicit_signed_in_consent_and_cannot_replay(team):
    cfg,clients,headers,ids=team
    with connect(cfg) as db:link=make_bot_link(db,cfg,{'id':333,'first_name':'Бот имя'})
    token=link['url'].split('#telegram=')[1]
    with TestClient(create_app(cfg)) as anon:
        assert anon.get('/api/v1/auth/telegram/bot-link',params={'token':token}).json()['display_name']=='Бот имя'
        assert anon.post('/api/v1/auth/telegram/bot-link',json={'token':token}).status_code==401
    alice=clients['alice']
    assert alice.post('/api/v1/auth/telegram/bot-link',json={'token':token},headers=headers['alice']).json()=={'linked':True}
    assert alice.post('/api/v1/auth/telegram/bot-link',json={'token':token},headers=headers['alice']).status_code==410
    with connect(cfg) as db:link=make_bot_link(db,cfg,{'id':333,'first_name':'Бот имя'})
    token=link['url'].split('#telegram=')[1]
    assert clients['boris'].post('/api/v1/auth/telegram/bot-link',json={'token':token},headers=headers['boris']).status_code==409


def test_expired_challenge_and_changed_session_cannot_link(team):
    cfg,clients,headers,ids=team
    data,code=start(clients['alice'],headers['alice'])
    with connect(cfg) as db:
        db.execute('UPDATE telegram_auth_challenges SET expires_at=? WHERE id=?',(int(time.time())-1,data['id']))
    assert confirm(cfg,code) is None
    assert clients['alice'].get('/api/v1/auth/telegram/status?id='+data['id']).json()['state']=='expired'
    assert clients['alice'].post('/api/v1/auth/telegram/complete',json={'id':data['id']},headers=headers['alice']).status_code==409
    data,code=start(clients['alice'],headers['alice']);confirm(cfg,code)
    # Another valid session for the same account must not redeem a stale link.
    login=clients['alice'].post('/api/v1/auth/login',json={'username':'alice','password':PASSWORD}).json()
    assert clients['alice'].post('/api/v1/auth/telegram/complete',json={'id':data['id']},headers={'X-CSRF-Token':login['csrf_token']}).status_code==403


def test_new_pages_keep_separate_administrator_teams_isolated(team):
    cfg,clients,headers,ids=team
    root=clients['admin']
    result=root.post('/api/v1/admins',json={'username':'separateadmin','full_name':'Другая команда','password':PASSWORD},headers=headers['admin'])
    assert result.status_code==201,result.text
    other_id=result.json()['id']
    with TestClient(create_app(cfg)) as other:
        session=other.post('/api/v1/auth/login',json={'username':'separateadmin','password':PASSWORD}).json()
        auth={'X-CSRF-Token':session['csrf_token']}
        created=other.post('/api/v1/recurring-payments',json={'name':'Секрет другой команды','amount':'10.00','currency':'RUB','next_due_date':'2026-10-28'},headers=auth)
        assert created.status_code==201,created.text
        foreign_id=created.json()['id']
        for path in ('/payment-occurrences/page?month=2026-10','/users/page?search=Другая','/activity/page?search=Секрет','/reminders/page'):
            response=root.get('/api/v1'+path)
            assert response.status_code==200,response.text
            assert response.json()['total']==0
        assert root.get('/api/v1/recurring-payments/'+str(foreign_id)).status_code==404
        assert root.get('/api/v1/payments/calendar?month=2026-10').json()==[]
        assert root.get('/api/v1/payments/summary?month=2026-10').json()['total_count']==0
        assert other.get('/api/v1/payments/summary?month=2026-10').json()['total_count']==1
        assert other.get('/api/v1/settings').json()['active_count']==1
        assert root.get('/api/v1/settings').json()['active_count']==0
        assert other.get('/api/v1/admins/page').status_code==403
        admins=root.get('/api/v1/admins/page?limit=1').json()
        assert admins['total']==2 and len(admins['items'])==1 and admins['has_more']


def test_private_bot_reads_current_identity_and_never_returns_group_finances(team):
    from backend.telegram_bot import handle_update
    cfg,clients,headers,ids=team
    for username in ('alice','boris'):
        assert clients[username].post('/api/v1/recurring-payments',json={'name':'Расход '+username,'amount':'12.50','currency':'RUB','next_due_date':'2026-10-28'},headers=headers[username]).status_code==201
    with connect(cfg) as db:
        db.execute('INSERT INTO telegram_identities VALUES(?,?,?,?)',(ids['alice'],'77','Alice',utcnow()))
        command={'message':{'chat':{'id':77,'type':'private'},'from':{'id':77},'text':'/payments','message_id':1}}
        result=handle_update(cfg,db,command)
        assert 'Расход alice' in result.payload['text'] and 'Расход boris' not in result.payload['text']
        group={'message':{'chat':{'id':-10001,'type':'supergroup'},'from':{'id':77},'text':'/payments','message_id':1}}
        assert handle_update(cfg,db,group) is None
        callback={'callback_query':{'id':'cb','from':{'id':77},'data':'cab:payments:2026-10:0','message':{'chat':{'id':77,'type':'private'},'message_id':1}}}
        assert handle_update(cfg,db,callback).method=='editMessageText'
        callback['callback_query']['message']['chat']['id']=88
        assert handle_update(cfg,db,callback) is None
        callback['callback_query']['message']['chat']['id']=77
        db.execute('DELETE FROM telegram_identities WHERE user_id=?',(ids['alice'],))
        result=handle_update(cfg,db,callback)
        assert 'Расход alice' not in result.payload['text']


def test_actual_bot_auth_button_requires_private_explicit_confirmation(team):
    from backend.telegram_bot import handle_update
    cfg,clients,headers,ids=team
    data,code=start(clients['alice'],headers['alice'])
    message={'chat':{'id':77,'type':'private'},'from':{'id':77},'text':'/start auth_'+code,'message_id':1}
    with connect(cfg) as db:
        result=handle_update(cfg,db,{'message':message})
        assert 'Подтвердите' in result.payload['text']
        assert clients['alice'].get('/api/v1/auth/telegram/status?id='+data['id']).json()['state']=='pending'
        group={**message,'chat':{'id':-10001,'type':'supergroup'}}
        handle_update(cfg,db,{'message':group})
        assert clients['alice'].get('/api/v1/auth/telegram/status?id='+data['id']).json()['state']=='pending'
        callback={'callback_query':{'id':'confirm','from':{'id':77},'data':'auth:'+code,'message':message}}
        assert 'Подтверждено' in handle_update(cfg,db,callback).payload['text']
        assert clients['alice'].get('/api/v1/auth/telegram/status?id='+data['id']).json()['state']=='confirmed'
