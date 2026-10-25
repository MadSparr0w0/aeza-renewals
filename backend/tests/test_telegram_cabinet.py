"""Private cabinet isolation and explicit, team-bound group destinations."""
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.app import create_app
from backend.db import connect, initialize, transaction
from backend.domain import add_recurring, schedule_reminders
from backend.security import hash_password
from backend.telegram_cabinet import build_view, handle_chat_action, parse_callback
from backend.telegram_connections import (active_connections, claim_chat_selection,
    disable_destination, enable_chat_selection, record_chat_selection,
    request_chat_selection, token_scope)
from backend.worker import SendResult, tick


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026,10,25)


@pytest.fixture
def cfg(tmp_path):
    settings=FrozenSettings(tmp_path/'private.sqlite','rootadmin',hash_password('Synthetic password 123!'),
        'synthetic-secret-'*5,telegram_bot_token='123456:FAKE_TOKEN')
    initialize(settings)
    with connect(settings) as db,transaction(db):
        db.execute("INSERT INTO users(id,username,full_name,password_hash,role,team_id,created_at) VALUES(2,'alice','Alice',?,'user',1,'2026-10-25')",(settings.admin_password_hash,))
        db.execute("INSERT INTO users(id,username,full_name,password_hash,role,team_id,created_at) VALUES(3,'bob','Bob',?,'user',1,'2026-10-25')",(settings.admin_password_hash,))
        db.execute("INSERT INTO users(id,username,full_name,password_hash,role,team_id,created_at) VALUES(4,'otheradmin','Other',?,'admin',4,'2026-10-25')",(settings.admin_password_hash,))
        for user_id in (1,2,3,4):
            db.execute("INSERT INTO telegram_identities VALUES(?,?,?,'2026-10-25')",(user_id,str(100+user_id),'Synthetic'))
        for user_id,name in ((2,'ALICE <only>'),(3,'BOB ONLY'),(4,'OTHER TEAM')):
            add_recurring(db,name=name,amount='10.25',currency='RUB',next_due_date='2026-10-25',owner_id=user_id,created_by=user_id)
    return settings


def choose(db,cfg,uid,tid,cid,now=1000):
    identifier=request_chat_selection(db,cfg,uid,tid,now)
    claim_chat_selection(db,cfg,uid,tid,identifier,cid,now+1)
    record_chat_selection(db,cfg,uid,tid,identifier,{'id':int(cid),'type':'supergroup','title':'Verified '+cid},now+2)
    enable_chat_selection(db,cfg,uid,tid,identifier,now+3)
    return identifier


def test_private_reads_current_identity_and_team(cfg):
    with connect(cfg) as db:
        alice=build_view(db,cfg,102,'payments').text
        assert 'ALICE &lt;only&gt;' in alice and 'BOB ONLY' not in alice and 'OTHER TEAM' not in alice
        admin=build_view(db,cfg,101,'payments').text
        assert 'ALICE &lt;only&gt;' in admin and 'BOB ONLY' in admin and 'OTHER TEAM' not in admin
        other=build_view(db,cfg,104,'summary').text
        assert '10,25' in other and '20,50' not in other
        db.execute('DELETE FROM telegram_identities WHERE user_id=2')
        with pytest.raises(HTTPException) as error:build_view(db,cfg,102,'payments')
        assert error.value.status_code==403
        with pytest.raises(HTTPException):build_view(db,cfg,999,'summary')


def test_callback_limits(cfg):
    assert parse_callback('cab:payments:2026-10:0',cfg.today)==('payments','2026-10',0)
    assert parse_callback('cab:history:all:999',cfg.today)==('history','all',999)
    for value in ('cab:payments:all:0','cab:payments:2026-10:1000','cab:payments:9999-12:0','cab:payments:2026-00:0','cab:summary:2026-10:-1'):
        assert parse_callback(value,cfg.today) is None


def test_native_request_is_one_use_expiring_and_admin_bound(cfg):
    with connect(cfg) as db:
        request=request_chat_selection(db,cfg,1,101,1000)
        with pytest.raises(HTTPException):claim_chat_selection(db,cfg,4,104,request,'-10001',1001)
        with pytest.raises(HTTPException):claim_chat_selection(db,cfg,2,102,request,'-10001',1001)
        with pytest.raises(HTTPException):claim_chat_selection(db,cfg,1,101,request,'-10001',1600)
        claim_chat_selection(db,cfg,1,101,request,'-10001',1001)
        with pytest.raises(HTTPException):claim_chat_selection(db,cfg,1,101,request,'-10001',1002)
        assert active_connections(db,cfg)==[]


def test_multi_group_fanout_team_isolation_and_remove_only_one(cfg):
    with connect(cfg) as db:
        choose(db,cfg,1,101,'-10001')
        choose(db,cfg,1,101,'-10002',2000)
        choose(db,cfg,4,104,'-10004',3000)
        with transaction(db):schedule_reminders(db,cfg.today)
        assert len(active_connections(db,cfg,1))==2
        rows=db.execute('''SELECT b.id,b.recipient_chat_id,u.team_id FROM reminder_outbox b
          JOIN payment_occurrences o ON o.id=b.occurrence_id JOIN recurring_payments r ON r.id=o.recurring_payment_id
          JOIN users u ON u.id=r.owner_id''').fetchall()
        assert len(rows)==5
        assert all((r['team_id']==1 and r['recipient_chat_id'] in ('-10001','-10002')) or (r['team_id']==4 and r['recipient_chat_id']=='-10004') for r in rows)
        with transaction(db):
            db.execute("UPDATE reminder_outbox SET state='unknown' WHERE id=?",(rows[0]['id'],))
            admin=db.execute('SELECT * FROM users WHERE id=1').fetchone()
            disable_destination(db,'-10001',admin)
        assert [r['chat_id'] for r in active_connections(db,cfg,1)]==['-10002']
        assert len(active_connections(db,cfg,4))==1
        assert db.execute('SELECT state FROM reminder_outbox WHERE id=?',(rows[0]['id'],)).fetchone()[0]=='unknown'
        with pytest.raises(HTTPException):disable_destination(db,'-10004',admin)
    sent=[]
    while tick(cfg,sender=lambda _,row:sent.append(row['recipient_chat_id']) or SendResult('sent'),now=4000):pass
    assert sorted(sent)==['-10002','-10002','-10004']


def test_same_physical_group_cannot_be_shared_between_teams(cfg):
    with connect(cfg) as db:
        choose(db,cfg,1,101,'-10001')
        with pytest.raises(HTTPException) as error:choose(db,cfg,4,104,'-10001',2000)
        assert error.value.status_code==409
        assert [r['id'] for r in active_connections(db,cfg)]==[1]


def test_worker_never_claims_forged_cross_team_recipient(cfg):
    with connect(cfg) as db:
        choose(db,cfg,1,101,'-10001')
        choose(db,cfg,4,104,'-10004',2000)
        with transaction(db):
            occurrence=db.execute('''SELECT o.id FROM payment_occurrences o
              JOIN recurring_payments r ON r.id=o.recurring_payment_id WHERE r.owner_id=2''').fetchone()[0]
            cursor=db.execute('''INSERT INTO reminder_outbox(occurrence_id,state,created_at,recipient_chat_id,delivery_scope)
              VALUES(?,'pending','2026-10-25','-10004',?)''',(occurrence,token_scope(cfg.telegram_bot_token)))
            forged=cursor.lastrowid
    sent=[]
    while tick(cfg,sender=lambda _,row:sent.append(row['id']) or SendResult('sent'),now=4000):pass
    assert forged not in sent
    with connect(cfg) as db:
        assert db.execute('SELECT state FROM reminder_outbox WHERE id=?',(forged,)).fetchone()[0]=='pending'


def test_native_share_uses_verified_title_and_explicit_confirmation(cfg):
    calls=[]
    class Client:
        def call(self,method,payload):
            calls.append(method)
            return {'getChat':{'id':-10001,'type':'supergroup','title':'REAL <Group>'},
                'getChatAdministrators':[{'status':'creator','user':{'id':101}}],
                'getChatMember':{'status':'administrator'}}[method]
    with connect(cfg) as db:
        user=db.execute('SELECT * FROM users WHERE id=1').fetchone()
        request=request_chat_selection(db,cfg,1,101,1000)
        reply=handle_chat_action(db,cfg,user,{'id':101},'',Client(),999,1001,
            shared={'request_id':request,'chat_id':-10001,'title':'FORGED'})
        assert 'REAL &lt;Group&gt;' in reply.text and 'FORGED' not in reply.text
        assert 'обо всех платежах команды' in reply.text
        assert active_connections(db,cfg)==[]
        handle_chat_action(db,cfg,user,{'id':101},'chat:on:'+str(request),Client(),999,1002)
        assert len(active_connections(db,cfg))==1
        assert calls==['getChat','getChatAdministrators','getChatMember']*2


def test_destination_api_is_team_scoped_and_protected(cfg):
    with connect(cfg) as db:
        choose(db,cfg,1,101,'-10001')
        choose(db,cfg,4,104,'-10004',2000)
        db.execute("UPDATE reminder_outbox SET state='sending',lease_until=9999999999 WHERE recipient_chat_id='-10004'")
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/v1/telegram/destinations').status_code==401
        login=client.post('/api/v1/auth/login',json={'username':'rootadmin','password':'Synthetic password 123!'})
        headers={'X-CSRF-Token':login.json()['csrf_token']}
        response=client.get('/api/v1/telegram/destinations')
        assert [row['id'] for row in response.json()['items']]==['-10001']
        assert client.delete('/api/v1/telegram/destinations/-10001').status_code==403
        assert client.delete('/api/v1/telegram/destinations/-10004',headers=headers).status_code==404
        assert client.delete('/api/v1/telegram/destinations/-10001',headers=headers).status_code==204
    with connect(cfg) as db:
        assert [r['chat_id'] for r in active_connections(db,cfg,4)]==['-10004']
