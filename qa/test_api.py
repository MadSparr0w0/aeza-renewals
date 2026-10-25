"""Acceptance and adversarial API checks using isolated temporary databases."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import connect, utcnow
from backend.security import hash_password
from backend.telegram_connections import token_scope

ADMIN_PASSWORD = 'A temporary acceptance passphrase!'
USER_PASSWORD = 'An independent user passphrase!'


class FrozenSettings(Settings):
    @property
    def today(self):
        return date(2026, 10, 23)


@pytest.fixture
def cfg(tmp_path):
    return FrozenSettings(database_path=tmp_path / 'acceptance.db', admin_username='administrator',
                          admin_password_hash=hash_password(ADMIN_PASSWORD), app_secret='a' * 64,
                          telegram_bot_token='synthetic-acceptance-token')


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg)) as test_client:
        yield test_client


def login(client, username='administrator', password=ADMIN_PASSWORD):
    response = client.post('/api/v1/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': response.json()['csrf_token']}


def payment(client, headers, **overrides):
    payload = {'name': 'Office rent', 'amount': '125000.10', 'currency': 'RUB',
               'next_due_date': '2026-10-21', 'frequency': 'monthly'} | overrides
    response = client.post('/api/v1/recurring-payments', headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_anonymous_cannot_read_data_or_specification(client):
    for path in ['auth/me', 'recurring-payments', 'payment-occurrences', 'payment-occurrences/1',
                 'payments/summary', 'users', 'settings', 'activity', 'docs', 'openapi.json']:
        response = client.get('/api/v1/' + path)
        assert response.status_code == 401, (path, response.status_code)


def test_cookie_csrf_origin_and_logout(client):
    headers = login(client)
    assert client.get('/api/v1/auth/me').json()['user']['role'] == 'admin'
    session_cookie = next(c for c in client.cookies.jar if c.name == 'ik_session')
    assert session_cookie.has_nonstandard_attr('HttpOnly')
    assert session_cookie.get_nonstandard_attr('SameSite') == 'strict'
    body = {'name': 'Server', 'amount': '12.50', 'currency': 'USD', 'next_due_date': '2026-10-25'}
    assert client.post('/api/v1/recurring-payments', json=body).status_code == 403
    assert client.post('/api/v1/recurring-payments', headers=headers | {'Origin': 'https://evil.example'}, json=body).status_code == 403
    assert client.post('/api/v1/recurring-payments', headers=headers | {'Sec-Fetch-Site': 'cross-site'}, json=body).status_code == 403
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/v1/payment-occurrences').status_code == 401


def test_user_cannot_administer_even_with_direct_api_calls(client, cfg):
    admin_headers = login(client)
    recurring = payment(client, admin_headers)
    user = client.post('/api/v1/users', headers=admin_headers,
                       json={'username': 'operator', 'full_name': 'Payment operator', 'password': USER_PASSWORD})
    assert user.status_code == 201
    user_id = user.json()['id']
    with TestClient(create_app(cfg)) as ordinary:
        headers = login(ordinary, 'operator', USER_PASSWORD)
        assert ordinary.get('/api/v1/payment-occurrences').status_code == 200
        assert ordinary.get('/api/v1/users').status_code == 403
        assert ordinary.post('/api/v1/users', headers=headers, json={'username': 'attacker', 'full_name': 'No', 'password': USER_PASSWORD}).status_code == 403
        assert ordinary.patch(f'/api/v1/users/{user_id}', headers=headers, json={'full_name': 'Changed'}).status_code == 403
        assert ordinary.delete(f'/api/v1/users/{user_id}', headers=headers).status_code == 403
        assert ordinary.delete(f'/api/v1/recurring-payments/{recurring["id"]}', headers=headers).status_code == 404
        assert ordinary.get('/api/v1/payment-occurrences?month=2026-10').json() == []
        assert ordinary.post('/api/v1/recurring-payments', headers=headers, json={'name': 'Own payment', 'amount': '1', 'currency': 'EUR', 'next_due_date': '2026-10-25'}).status_code == 201
        occurrence = ordinary.get('/api/v1/payment-occurrences?month=2026-10').json()[0]
        response = ordinary.patch(f'/api/v1/payment-occurrences/{occurrence["id"]}', headers=headers,
                                  json={'is_paid': True, 'paid_at': '2026-10-21'})
        assert response.status_code == 200
        assert response.json()['overdue_days'] == 0


def test_admin_is_immutable_and_role_injection_rejected(client):
    headers = login(client)
    admin_id = client.get('/api/v1/auth/me').json()['user']['id']
    assert client.patch(f'/api/v1/users/{admin_id}', headers=headers, json={'full_name': 'Changed'}).status_code == 403
    assert client.delete(f'/api/v1/users/{admin_id}', headers=headers).status_code == 403
    response = client.post('/api/v1/users', headers=headers,
                           json={'username': 'newadmin', 'full_name': 'No', 'password': USER_PASSWORD, 'role': 'admin'})
    assert response.status_code == 422
    assert USER_PASSWORD not in response.text


def test_anchor_day_independent_history_and_recalculation(client):
    headers = login(client)
    recurring = payment(client, headers, next_due_date='2026-01-31')
    rows = client.get('/api/v1/payment-occurrences').json()
    by_date = {row['due_date']: row for row in rows if row['recurring_payment_id'] == recurring['id']}
    assert {'2026-01-31', '2026-02-28', '2026-03-31', '2026-10-31', '2026-11-30'} <= by_date.keys()
    january = by_date['2026-01-31']
    url = f'/api/v1/payment-occurrences/{january["id"]}'
    paid = client.patch(url, headers=headers, json={'is_paid': True, 'paid_at': '2026-02-02'}).json()
    assert paid['status'] == 'paid' and paid['overdue_days'] == 2
    corrected = client.patch(url, headers=headers, json={'paid_at': '2026-01-30'}).json()
    assert corrected['overdue_days'] == 0 and corrected['due_date'] == '2026-01-31'
    assert not client.get(f'/api/v1/payment-occurrences/{by_date["2026-02-28"]["id"]}').json()['is_paid']
    undone = client.patch(url, headers=headers, json={'is_paid': False}).json()
    assert undone['paid_at'] is None and undone['status'] == 'overdue'


def test_currency_summaries_are_exact_and_never_converted(client):
    headers = login(client)
    for currency, amount in [('RUB', '0.10'), ('RUB', '0.20'), ('USD', '12.99'), ('EUR', '9.95')]:
        payment(client, headers, currency=currency, amount=amount, next_due_date='2026-10-23')
    summary = client.get('/api/v1/payments/summary?month=2026-10').json()['currencies']
    assert summary['RUB']['planned'] == '0.30'
    assert summary['USD']['planned'] == '12.99'
    assert summary['EUR']['planned'] == '9.95'
    assert all(item['overdue'] == '0.00' for item in summary.values())


def test_archive_preserves_history_and_cancels_unsent_reminders(client, cfg):
    headers = login(client)
    with connect(cfg) as db:
        admin_id = db.execute('SELECT id FROM users WHERE env_managed=1').fetchone()[0]
        db.execute('''INSERT INTO telegram_connection
          (id,chat_id,chat_title,chat_type,bot_scope,connected_at,connected_on,connected_by)
          VALUES(1,'-1001234567890','Synthetic team','supergroup',?,?,?,?)''',
                   (token_scope(cfg.telegram_bot_token), utcnow(), cfg.today.isoformat(), admin_id))
    recurring = payment(client, headers)
    for _ in range(3):
        client.get('/api/v1/payment-occurrences')
    rows = client.get('/api/v1/payment-occurrences?month=2026-10').json()
    with connect(cfg) as db:
        assert db.execute('SELECT COUNT(*) FROM reminder_outbox WHERE occurrence_id=?', (rows[0]['id'],)).fetchone()[0] == 1
    assert client.delete(f'/api/v1/recurring-payments/{recurring["id"]}', headers=headers).status_code == 204
    assert client.get('/api/v1/recurring-payments').json() == []
    assert client.get('/api/v1/payment-occurrences?month=2026-10').json()[0]['id'] == rows[0]['id']
    with connect(cfg) as db:
        assert db.execute("SELECT COUNT(*) FROM reminder_outbox WHERE state IN ('pending','retry','sending')").fetchone()[0] == 0


def test_user_change_revokes_sessions(client, cfg):
    headers = login(client)
    user = client.post('/api/v1/users', headers=headers, json={'username': 'operator', 'full_name': 'Operator', 'password': USER_PASSWORD}).json()
    with TestClient(create_app(cfg)) as ordinary:
        login(ordinary, 'operator', USER_PASSWORD)
        assert client.patch(f'/api/v1/users/{user["id"]}', headers=headers, json={'full_name': 'Renamed'}).status_code == 200
        assert ordinary.get('/api/v1/auth/me').status_code == 401
        login(ordinary, 'operator', USER_PASSWORD)
        assert client.delete(f'/api/v1/users/{user["id"]}', headers=headers).status_code == 204
        assert ordinary.get('/api/v1/auth/me').status_code == 401


def test_restart_preserves_state(cfg):
    with TestClient(create_app(cfg)) as first:
        headers = login(first)
        payment(first, headers)
        row = first.get('/api/v1/payment-occurrences?month=2026-10').json()[0]
        assert first.patch(f'/api/v1/payment-occurrences/{row["id"]}', headers=headers,
                           json={'is_paid': True, 'paid_at': '2026-10-21'}).status_code == 200
    with TestClient(create_app(cfg)) as second:
        login(second)
        persisted = second.get(f'/api/v1/payment-occurrences/{row["id"]}').json()
        assert persisted['is_paid'] and persisted['paid_at'] == '2026-10-21'


def test_invalid_money_future_paid_date_and_mass_assignment(client):
    headers = login(client)
    for bad_amount in ['-1', '0', '0.001', 'NaN', 'Infinity', '1000000000']:
        response = client.post('/api/v1/recurring-payments', headers=headers,
                               json={'name': 'Bad', 'amount': bad_amount, 'currency': 'USD', 'next_due_date': '2026-10-23'})
        assert response.status_code == 422, bad_amount
    payment(client, headers)
    row = client.get('/api/v1/payment-occurrences?month=2026-10').json()[0]
    url = f'/api/v1/payment-occurrences/{row["id"]}'
    assert client.patch(url, headers=headers, json={'is_paid': True, 'paid_at': '2026-10-24'}).status_code == 422
    assert client.patch(url, headers=headers, json={'amount': '0.01', 'is_paid': True}).status_code == 422
    assert client.get('/api/v1/payment-occurrences?month=9999-12').status_code == 422


def test_rate_limit_and_oversized_request(client):
    for _ in range(8):
        assert client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong'}).status_code == 429
    assert client.post('/api/v1/auth/login', content='x' * 70000, headers={'Content-Type': 'application/json'}).status_code == 413


def test_untrusted_spa_paths_are_not_probed_as_files(client, monkeypatch):
    original_is_file = Path.is_file
    probed = []

    def watch(path, *args, **kwargs):
        probed.append(str(path))
        return original_is_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'is_file', watch)
    for path in ['/.env', '/data/local-access.json', '/%5c%5cexample.invalid%5cshare%5csecret', '/C:%5cWindows%5cwin.ini']:
        response = client.get(path)
        assert response.status_code in {200, 400, 404, 503}
        assert 'ADMIN_PASSWORD_HASH' not in response.text
    assert not any('.env' in path or 'example.invalid' in path or 'win.ini' in path or 'local-access' in path for path in probed)
