"""Bounded SQL reads shared by the website and private Telegram cabinet."""
from datetime import date, timedelta
import re

from fastapi import HTTPException

from .domain import CURRENCIES, money, month_end, occurrence_json

FROM = ''' FROM payment_occurrences o JOIN recurring_payments r ON r.id=o.recurring_payment_id
 JOIN users owner ON owner.id=r.owner_id'''
FIELDS = 'o.*,r.active AS recurring_active,r.owner_id,owner.full_name AS owner_name'


def where(cfg,user,month=None,currency=None,status=None,search=None,day=None):
    clauses,args=[],[]
    if user['role']=='admin':
        clauses.append('owner.team_id=?');args.append(user['team_id'])
    else:
        clauses.append('r.owner_id=?'); args.append(user['id'])
    if month:
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):
            raise HTTPException(422,'Месяц должен иметь формат YYYY-MM.')
        first=date.fromisoformat(month+'-01')
        if not 2000<=first.year<=cfg.today.year+5:
            raise HTTPException(422,'Месяц вне допустимого диапазона.')
        clauses.extend(['o.due_date>=?','o.due_date<=?']); args.extend([first.isoformat(),month_end(first).isoformat()])
    if day:
        try: date.fromisoformat(day)
        except ValueError: raise HTTPException(422,'Некорректная дата.') from None
        clauses.append('o.due_date=?');args.append(day)
    if currency and currency!='all':
        if currency not in CURRENCIES: raise HTTPException(422,'Неизвестная валюта.')
        clauses.append('o.currency=?');args.append(currency)
    if status and status!='all':
        if status not in {'paid','unpaid','overdue','pending','upcoming'}: raise HTTPException(422,'Неизвестный статус.')
        clauses.append('o.is_paid=?');args.append(1 if status=='paid' else 0)
        if status in {'overdue','pending','upcoming'}:
            clauses.append('o.due_date'+('<?' if status=='overdue' else '>=?'));args.append(cfg.today.isoformat())
        if status=='upcoming': clauses.append('r.active=1')
    if search and search.strip():
        clauses.append('(instr(CASEFOLD(o.name),CASEFOLD(?))>0'+(' OR instr(CASEFOLD(owner.full_name),CASEFOLD(?))>0)' if user['role']=='admin' else ')'))
        args.append(search.strip())
        if user['role']=='admin':args.append(search.strip())
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),args


def page(db,cfg,user,*,limit=30,offset=0,sort='due_date',descending=False,**filters):
    limit=max(1,min(int(limit),100));offset=max(0,int(offset))
    order={'due_date':'o.due_date','name':'CASEFOLD(o.name)','amount':'o.currency,o.amount_minor'}.get(sort)
    if order is None:raise HTTPException(422,'Неизвестная сортировка.')
    direction=' DESC' if descending else ' ASC'
    # Currency grouping stays ascending while amounts reverse inside a currency.
    order=order+direction+',o.id'+direction
    clause,args=where(cfg,user,**filters)
    total=db.execute('SELECT COUNT(*)'+FROM+clause,args).fetchone()[0]
    rows=db.execute('SELECT '+FIELDS+FROM+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
    return {'items':[occurrence_json(r,cfg.today,user) for r in rows], 'total':total,
            'limit':limit,'offset':offset,'has_more':offset+len(rows)<total}


def summary(db,cfg,user,month=None):
    clause,args=where(cfg,user,month)
    rows=db.execute('''SELECT o.currency,COUNT(*) AS total_count,SUM(o.is_paid) AS paid_count,
      SUM(CASE WHEN o.is_paid=0 AND o.due_date<? THEN 1 ELSE 0 END) AS overdue_count,
      SUM(o.amount_minor) AS planned,SUM(CASE WHEN o.is_paid=1 THEN o.amount_minor ELSE 0 END) AS paid,
      SUM(CASE WHEN o.is_paid=0 THEN o.amount_minor ELSE 0 END) AS unpaid,
      SUM(CASE WHEN o.is_paid=0 AND o.due_date<? THEN o.amount_minor ELSE 0 END) AS overdue'''
      +FROM+clause+' GROUP BY o.currency',(cfg.today.isoformat(),cfg.today.isoformat(),*args)).fetchall()
    amounts={c:{k:'0.00' for k in ('planned','paid','unpaid','overdue')} for c in CURRENCIES}
    by_currency={c:{k:0 for k in ('total_count','paid_count','unpaid_count','overdue_count')} for c in CURRENCIES}
    for r in rows:
        amounts[r['currency']]={k:money(r[k] or 0) for k in amounts[r['currency']]}
        by_currency[r['currency']]={'total_count':r['total_count'],'paid_count':r['paid_count'] or 0,
          'unpaid_count':r['total_count']-(r['paid_count'] or 0),'overdue_count':r['overdue_count'] or 0}
    counts={k:sum(v[k] for v in by_currency.values()) for k in by_currency['RUB']}
    return {'currencies':amounts,'counts_by_currency':by_currency,**counts}


def calendar(db,cfg,user,month):
    clause,args=where(cfg,user,month)
    rows=db.execute('''SELECT o.due_date,o.currency,COUNT(*) AS count,SUM(o.amount_minor) AS amount,
      SUM(o.is_paid) AS paid_count'''+FROM+clause+' GROUP BY o.due_date,o.currency ORDER BY o.due_date',args).fetchall()
    days={}
    for r in rows:
        day=days.setdefault(r['due_date'],{'date':r['due_date'],'count':0,'paid_count':0,'currencies':{}})
        day['count']+=r['count'];day['paid_count']+=r['paid_count']
        day['currencies'][r['currency']]=money(r['amount'])
    return list(days.values())


def insights(db,cfg,user,month):
    """Return bounded actionable findings, never a synthetic AI recommendation."""
    clause,args=where(cfg,user,month)
    duplicate=db.execute('''SELECT MIN(o.id) AS id,o.name,owner.full_name AS owner_name,o.currency,
      o.amount_minor,o.due_date,COUNT(*) AS count'''+FROM+clause+'''
      GROUP BY r.owner_id,CASEFOLD(trim(o.name)),o.currency,o.amount_minor,o.due_date
      HAVING COUNT(*)>1 ORDER BY count DESC,MIN(o.id) LIMIT 5''',args).fetchall()
    today=cfg.today.isoformat();until=(cfg.today+timedelta(days=7)).isoformat()
    own,values=where(cfg,user,status='unpaid')
    upcoming=db.execute('''SELECT o.currency,SUM(o.amount_minor) AS amount,COUNT(*) AS count'''+FROM+own+
      ' AND r.active=1 AND o.due_date>=? AND o.due_date<=? GROUP BY o.currency',(*values,today,until)).fetchall()
    # Adjacent periods provide a reproducible price comparison in the same currency.
    changes=db.execute('''SELECT o.id,o.name,o.amount_minor,o.currency,o.due_date,owner.full_name AS owner_name,
      prior.amount_minor AS previous_amount'''+FROM+''' JOIN payment_occurrences prior ON prior.id=(
        SELECT p.id FROM payment_occurrences p WHERE p.recurring_payment_id=o.recurring_payment_id
        AND p.due_date<o.due_date ORDER BY p.due_date DESC LIMIT 1)'''+clause+
      ' AND prior.currency=o.currency AND o.amount_minor>prior.amount_minor ORDER BY o.due_date,o.id LIMIT 5',args).fetchall()
    return {'upcoming':[{'currency':r['currency'],'amount':money(r['amount']),'count':r['count']} for r in upcoming],
      'duplicates':[{'id':r['id'],'name':r['name'],'owner_name':r['owner_name'],'currency':r['currency'],
        'amount':money(r['amount_minor']),'date':r['due_date'],'count':r['count']} for r in duplicate],
      'price_changes':[{'id':r['id'],'name':r['name'],'owner_name':r['owner_name'],'currency':r['currency'],
        'amount':money(r['amount_minor']),'previous_amount':money(r['previous_amount']),'date':r['due_date']} for r in changes]}
