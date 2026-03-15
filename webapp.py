#!/usr/bin/env python3
"""Finance Tracker Web App"""

import os
import tempfile
import uuid
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, HTTPException, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from parsers import (
    detect_bank, TradeRepublicParser, OpenbankParser,
    OpenbankPDFParser, RevolutPDFParser, Transaction,
)
from classifier import classify_batch, load_custom_rules
from sheets import SheetsClient

app = FastAPI(title="Finance Tracker")

sheets = SheetsClient()
load_custom_rules(sheets)
sessions: dict = {}


def _tx_to_dict(tx: Transaction) -> dict:
    return {
        'date': tx.fmt_date(),
        'description': tx.description,
        'amount': tx.amount,
        'category': tx.category,
        'bank': tx.bank,
        'tx_type': tx.tx_type,
        'skipped': False,
    }


@app.get("/api/people")
async def get_people():
    return {"people": sheets.get_titulares()}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename or ''
    mime = file.content_type or ''

    bank_key = detect_bank(filename)
    if bank_key == 'unknown':
        if 'pdf' in mime or filename.lower().endswith('.pdf'):
            bank_key = 'trade_republic'
        elif any(x in mime for x in ('html', 'excel', 'xls')) or filename.lower().endswith(('.xls', '.html')):
            bank_key = 'openbank'
        else:
            raise HTTPException(400, "Archivo no reconocido.")

    suffix = '.pdf' if (bank_key in ('trade_republic', 'openbank_pdf', 'revolut') or filename.lower().endswith('.pdf')) else '.xls'

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if bank_key == 'trade_republic':
            all_txs = TradeRepublicParser().parse(tmp_path)
            bank_name = 'Trade Republic'
        elif bank_key == 'openbank_pdf':
            all_txs = OpenbankPDFParser().parse(tmp_path)
            bank_name = 'Openbank'
        elif bank_key == 'revolut':
            all_txs = RevolutPDFParser().parse(tmp_path)
            bank_name = 'Revolut'
        else:
            all_txs = OpenbankParser().parse(tmp_path)
            bank_name = 'Openbank'

        expenses = [tx for tx in all_txs if tx.tx_type in ('expense', 'income')]
        excluded = len(all_txs) - len(expenses)

        categories = classify_batch(expenses)
        for tx, cat in zip(expenses, categories):
            tx.category = cat

        session_id = str(uuid.uuid4())
        sessions[session_id] = {'transactions': expenses, 'bank': bank_name}

        return {
            'session_id': session_id,
            'bank': bank_name,
            'filename': filename,
            'total': len(expenses),
            'excluded': excluded,
            'transactions': [_tx_to_dict(tx) for tx in expenses],
            'existing_people': sheets.get_titulares(),
        }
    finally:
        os.unlink(tmp_path)


class SaveRequest(BaseModel):
    titular: str
    transactions: list[dict]


@app.post("/api/save/{session_id}")
async def save_session(session_id: str, body: SaveRequest):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Sesión expirada.")

    txs = session['transactions']
    to_save = []
    for i, tx_data in enumerate(body.transactions):
        if tx_data.get('skipped'):
            continue
        if i < len(txs):
            txs[i].category = tx_data.get('category', txs[i].category)
            to_save.append(txs[i])

    sheets.write_transactions(to_save, titular=body.titular)
    sheets.add_titular(body.titular)
    del sessions[session_id]

    return {'saved': len(to_save), 'total': sum(tx.amount for tx in to_save)}


@app.get("/api/summary")
async def get_summary(year: int = None, month: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    summary = sheets.get_monthly_summary(year, month, titular=titular or None)
    total = summary.pop('__total__', 0.0)
    income = summary.pop('__income__', 0.0)

    month_str = f"{year:04d}-{month:02d}"
    income_items = []
    for r in sheets._get_all_records():
        if r.get('Mes') != month_str or r.get('Tipo') != 'income':
            continue
        desc = r.get('Descripción', '')
        if 'NOMINA' in desc.upper() or 'NÓMINA' in desc.upper():
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
        income_items.append({'description': desc, 'amount': amt, 'date': r.get('Fecha', '')})
    income_items.sort(key=lambda x: x['amount'], reverse=True)

    return {'summary': summary, 'total': total, 'income': income, 'income_items': income_items, 'year': year, 'month': month}


@app.get("/api/transactions")
async def get_transactions(year: int = None, month: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    txs = sheets.get_monthly_transactions(year, month, titular=titular or None)
    return {'transactions': txs}


@app.get("/api/months")
async def get_months(titular: str = None):
    return {'months': sheets.get_months_with_data(titular=titular or None)}


@app.get("/api/annual")
async def get_annual(year: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    rows = sheets._get_all_records()
    cat_totals: dict[str, float] = {}
    month_totals: dict[str, float] = {}
    for r in rows:
        if r.get('Tipo') != 'expense':
            continue
        mes = r.get('Mes', '')
        if not mes.startswith(str(year)):
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
        cat = r.get('Categoría', 'Otros')
        cat_totals[cat] = cat_totals.get(cat, 0.0) + amt
        month_totals[mes] = month_totals.get(mes, 0.0) + amt
    sorted_months = [{'month': m, 'total': round(month_totals[m], 2)} for m in sorted(month_totals)]
    sorted_cats = sorted([{'name': k, 'amount': round(v, 2)} for k, v in cat_totals.items()], key=lambda x: -x['amount'])
    return {'year': year, 'categories': sorted_cats, 'months': sorted_months, 'total': round(sum(cat_totals.values()), 2)}


@app.get("/api/monthly_totals")
async def get_monthly_totals(titular: str = None):
    rows = sheets._get_all_records()
    totals: dict[str, float] = {}
    for r in rows:
        mes = r.get('Mes', '')
        if not mes or r.get('Tipo') != 'expense':
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
        totals[mes] = totals.get(mes, 0.0) + amt
    sorted_months = sorted(totals.keys())
    return {'months': sorted_months, 'totals': [round(totals[m], 2) for m in sorted_months]}


@app.get("/api/search")
async def search_transactions(q: str = '', titular: str = None):
    if len(q) < 2:
        return {'transactions': []}
    q_up = q.upper()
    rows = sheets._get_all_records()
    results = []
    for r in rows:
        if r.get('Tipo') not in ('expense', 'income'):
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        desc = r.get('Descripción', '')
        if q_up not in desc.upper() and q_up not in r.get('Categoría', '').upper():
            continue
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            amt = 0.0
        results.append({
            'date': r.get('Fecha', ''),
            'description': desc,
            'amount': amt,
            'category': r.get('Categoría', 'Otros'),
            'bank': r.get('Banco', ''),
            'titular': r.get('Titular', ''),
            'mes': r.get('Mes', ''),
        })
    results.sort(key=lambda x: x.get('mes', '') + x.get('date', ''), reverse=True)
    return {'transactions': results[:150]}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
