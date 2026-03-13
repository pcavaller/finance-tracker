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
    return {'summary': summary, 'total': total, 'year': year, 'month': month}


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


app.mount("/", StaticFiles(directory="static", html=True), name="static")
