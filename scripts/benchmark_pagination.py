"""Reproducible, offline synthetic page benchmark; never uses the live DB."""
import argparse
from datetime import date
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
from backend.db import connect,initialize,transaction,utcnow
from backend.security import hash_password


class BenchmarkSettings(Settings):
    @property
    def today(self):return date(2026,10,25)


def main():
    parser=argparse.ArgumentParser(description='Проверка страниц на синтетических данных без сетевых запросов')
    parser.add_argument('--rows',type=int,default=50000)
    parser.add_argument('--runs',type=int,default=10)
    args=parser.parse_args()
    if not 100<=args.rows<=500000 or not 1<=args.runs<=100:parser.error('rows: 100..500000; runs:1..100')
    with tempfile.TemporaryDirectory(prefix='invoice-page-benchmark-') as folder:
        password='Synthetic local benchmark password!'
        cfg=BenchmarkSettings(Path(folder)/'benchmark.sqlite','benchmark',hash_password(password),'offline-benchmark-secret-'*4)
        initialize(cfg)
        with connect(cfg) as db,transaction(db):
            uid=db.execute('SELECT id FROM users').fetchone()[0]
            stamp=utcnow()
            db.executemany('''INSERT INTO recurring_payments
              (id,name,amount_minor,owner_id,currency,initial_due_date,next_materialize_date,anchor_day,created_at)
              VALUES(?, ?,10000,?,'RUB','2026-10-28','2026-12-28',28,?)''',
              ((i,'Синтетический счёт '+str(i),uid,stamp) for i in range(1,args.rows+1)))
            db.executemany('''INSERT INTO payment_occurrences
              (recurring_payment_id,name,amount_minor,currency,due_date,updated_at)
              VALUES(?,?,10000,'RUB','2026-10-28',?)''',
              ((i,'Синтетический счёт '+str(i),stamp) for i in range(1,args.rows+1)))
        timings={}
        with TestClient(create_app(cfg)) as client:
            assert client.post('/api/v1/auth/login',json={'username':'benchmark','password':password}).status_code==200
            for label,offset in (('first_page',0),('deep_page',args.rows-30)):
                samples=[]
                for _ in range(args.runs):
                    started=time.perf_counter()
                    response=client.get('/api/v1/payment-occurrences/page',params={'month':'2026-10','limit':30,'offset':offset})
                    samples.append((time.perf_counter()-started)*1000)
                    assert response.status_code==200 and response.json()['total']==args.rows
                    assert len(response.json()['items'])==30
                timings[label]={'median_ms':round(statistics.median(samples),2),'max_ms':round(max(samples),2),'response_bytes':len(response.content)}
            totals=client.get('/api/v1/payments/summary?month=2026-10').json()
            assert totals['total_count']==args.rows and totals['currencies']['RUB']['planned']==str(args.rows*100)+'.00'
            calendar=client.get('/api/v1/payments/calendar?month=2026-10').json()
            assert len(calendar)==1 and calendar[0]['count']==args.rows
        print(json.dumps({'synthetic_rows':args.rows,'runs_per_page':args.runs,'page_size':30,
          'timings':timings,'aggregate_count':totals['total_count'],'calendar_days':len(calendar),
          'scope':'local ASGI+SQLite, sequential reads, not a concurrency or live-server SLA'},ensure_ascii=False))


if __name__=='__main__':main()
