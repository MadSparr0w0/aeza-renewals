"""Group linking and recipient boundaries. No real Telegram requests."""
from dataclasses import replace
from datetime import date
import json
import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.config import Settings
from backend.db import SCHEMA, connect, initialize, transaction
from backend.domain import add_recurring, schedule_reminders
from backend.security import hash_password
from backend.telegram_connections import (active_connection, code_hash, confirm_link,
    connection_status, disconnect, group_id, record_candidate, start_link, token_scope)
from backend.worker import SendResult, claim, deliver, finish, tick

PASSWORD = "Group setup test password!"
ENCODED = hash_password(PASSWORD)
CHAT = {"id": -100123456, "title": "Бухгалтерия <команды>", "type": "supergroup"}


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026,10,25)


@pytest.fixture
def cfg(tmp_path):
    result = FrozenSettings(tmp_path / "groups.db", "admin", ENCODED, "synthetic-secret-"*5,
        telegram_bot_token="123456:FAKE_GROUP_TOKEN", telegram_chat_id="777")
    initialize(result)
    return result


def administrator(db):
    return db.execute("SELECT * FROM users WHERE env_managed=1").fetchone()


def link(db,cfg,now=1000,chat=CHAT):
    with transaction(db):
        invitation=start_link(db,cfg,administrator(db),now)
    assert record_candidate(db,cfg,invitation['code'],chat,42,now+1)
    with transaction(db):
        confirm_link(db,cfg,administrator(db),invitation['request_id'],now+2)
    return invitation


def due(db):
    with transaction(db):
        return add_recurring(db,name="Аренда",amount="1500",currency="RUB",next_due_date="2026-10-27")


@pytest.mark.parametrize('value',[777,'777',0,True,'-01','@team','-9223372036854775808',None,'-1.0'])
def test_recipient_must_be_canonical_negative_id(value):
    assert group_id(value) is None


def test_environment_chat_is_not_connected_and_no_financial_send(cfg):
    with connect(cfg) as db:
        due(db)
        schedule_reminders(db,cfg.today)
        assert not connection_status(db,cfg)['connected']
        assert db.execute('SELECT COUNT(*) FROM reminder_outbox').fetchone()[0]==0
    assert not tick(cfg,sender=lambda *_:pytest.fail('legacy personal destination used'))


def test_code_is_hashed_expiring_once_and_candidate_needs_web_confirmation(cfg):
    with connect(cfg) as db:
        due(db)
        with transaction(db): pending=start_link(db,cfg,administrator(db),1000)
        stored=dict(db.execute('SELECT * FROM telegram_link_requests').fetchone())
        assert stored['code_hash']==code_hash(cfg,pending['code'])
        assert pending['code'] not in json.dumps(stored)
        assert record_candidate(db,cfg,pending['code'],CHAT,42,1001)
        assert not record_candidate(db,cfg,pending['code'],CHAT,42,1002)
        schedule_reminders(db,cfg.today)
        assert not active_connection(db,cfg)
        assert db.execute('SELECT COUNT(*) FROM reminder_outbox').fetchone()[0]==0
        with transaction(db): state=confirm_link(db,cfg,administrator(db),pending['request_id'],1003)
        assert state['chat']=={'id':str(CHAT['id']),'title':CHAT['title'],'type':'supergroup'}
        assert pending['code'] not in json.dumps(state)
        assert db.execute('SELECT recipient_chat_id FROM reminder_outbox').fetchone()[0]==str(CHAT['id'])
        with pytest.raises(HTTPException):
            with transaction(db):confirm_link(db,cfg,administrator(db),pending['request_id'],1004)


def test_expiry_revoke_and_wrong_chat_types(cfg):
    with connect(cfg) as db:
        with transaction(db): first=start_link(db,cfg,administrator(db),1000)
        for chat in ({'id':123,'type':'private'},{**CHAT,'type':'channel'},{**CHAT,'id':'@team'}):
            assert not record_candidate(db,cfg,first['code'],chat,42,1001)
        assert not record_candidate(db,cfg,first['code'],CHAT,42,1600)
        with transaction(db): second=start_link(db,cfg,administrator(db),1601)
        assert not record_candidate(db,cfg,first['code'],CHAT,42,1602)
        assert record_candidate(db,cfg,second['code'],CHAT,42,1602)
        with pytest.raises(HTTPException):
            with transaction(db):confirm_link(db,cfg,administrator(db),second['request_id'],2201)


def test_new_group_does_not_redirect_queued_recipient(cfg):
    with connect(cfg) as db:
        due(db);link(db,cfg)
        old=dict(db.execute('SELECT * FROM reminder_outbox').fetchone())
        link(db,cfg,2000,{**CHAT,'id':-10098765,'title':'Другая команда'})
        rows=[dict(r) for r in db.execute('SELECT * FROM reminder_outbox ORDER BY id')]
        assert rows[0]['id']==old['id'] and rows[0]['state']=='cancelled'
        assert rows[0]['recipient_chat_id']==str(CHAT['id'])
        assert rows[1]['state']=='pending' and rows[1]['recipient_chat_id']=='-10098765'
        outgoing=claim(db,'worker',3000)
        assert outgoing['recipient_chat_id']=='-10098765'


@pytest.mark.parametrize('state',['sent','unknown'])
def test_terminal_delivery_survives_disconnect_and_token_rotation(cfg,state):
    with connect(cfg) as db:
        due(db);link(db,cfg)
        job=claim(db,'worker',1003)
        finish(db,job,'worker',SendResult(state),1004)
        before=dict(db.execute('SELECT * FROM reminder_outbox').fetchone())
        with transaction(db):disconnect(db)
        rotated=replace(cfg,telegram_bot_token='123456:ROTATED_FAKE_TOKEN')
        link(db,rotated,2000)
        after=dict(db.execute('SELECT * FROM reminder_outbox').fetchone())
        assert after==before
        assert db.execute('SELECT COUNT(*) FROM reminder_outbox').fetchone()[0]==1
        assert claim(db,'other',3000) is None


@pytest.mark.parametrize('recipient,scope',[('777',None),('-100123456','wrong')])
def test_deliver_refuses_private_or_wrong_token_before_network(cfg,recipient,scope):
    row={'recipient_chat_id':recipient,'delivery_scope':scope}
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('invalid destination sent'))) as client:
        result=deliver(cfg,row,client)
    assert result.state=='cancelled'


def test_connection_api_admin_csrf_and_no_repeated_code(cfg):
    with TestClient(create_app(cfg)) as client:
        assert client.get('/api/v1/telegram/connection').status_code==401
        session=client.post('/api/v1/auth/login',json={'username':'admin','password':PASSWORD}).json()
        headers={'Origin':cfg.origin,'X-CSRF-Token':session['csrf_token']}
        assert client.post('/api/v1/telegram/connection/request').status_code==403
        issued=client.post('/api/v1/telegram/connection/request',headers=headers)
        assert issued.status_code==200
        body=issued.json()
        assert body['code'] not in client.get('/api/v1/telegram/connection').text
        assert client.post('/api/v1/telegram/connection/confirm',json={'request_id':body['request_id']},headers=headers).status_code==409
        with connect(cfg) as db:
            assert record_candidate(db,cfg,body['code'],CHAT,42)
        confirmed=client.post('/api/v1/telegram/connection/confirm',json={'request_id':body['request_id']},headers=headers)
        assert confirmed.status_code==200 and confirmed.json()['connected']
        assert client.get('/api/v1/settings').json()['telegram_configured']
        assert client.delete('/api/v1/telegram/connection',headers=headers).status_code==204
        assert not client.get('/api/v1/settings').json()['telegram_configured']
        client.post('/api/v1/users',json={'username':'member','full_name':'Участник','password':PASSWORD},headers=headers)
        client.post('/api/v1/auth/logout',headers=headers)
        session=client.post('/api/v1/auth/login',json={'username':'member','password':PASSWORD}).json()
        headers['X-CSRF-Token']=session['csrf_token']
        assert client.get('/api/v1/telegram/connection').status_code==403
        assert client.post('/api/v1/telegram/connection/request',headers=headers).status_code==403
        assert client.delete('/api/v1/telegram/connection',headers=headers).status_code==403
