from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING

import gspread
from google.oauth2.service_account import Credentials

if TYPE_CHECKING:
    from parsers import Transaction

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

HEADERS = ['Fecha', 'Descripción', 'Importe', 'Categoría', 'Banco', 'Titular', 'Mes', 'Tipo']


class SheetsClient:

    def __init__(self, credentials_path: str = None, sheet_id: str = None):
        credentials_json_b64 = os.getenv('GOOGLE_CREDENTIALS_JSON')
        if credentials_json_b64:
            creds_dict = json.loads(base64.b64decode(credentials_json_b64))
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(credentials_path or os.getenv('GOOGLE_CREDENTIALS_PATH'), scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sheet_id = sheet_id or os.getenv('GOOGLE_SHEET_ID')
        self._spreadsheet = self.gc.open_by_key(self.sheet_id)
        self.ws = self._get_or_create_sheet('Transacciones', HEADERS)
        self.ws_personas = self._get_or_create_sheet('Personas', ['Titular', 'Creado'])

    def _get_or_create_sheet(self, name: str, headers: list[str]):
        try:
            ws = self._spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(name, rows=2000, cols=len(headers) + 2)
            ws.append_row(headers)
            ws.format(f'A1:{chr(64 + len(headers))}1', {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.18, 'green': 0.18, 'blue': 0.52},
            })
        return ws

    def write_transactions(self, transactions: list[Transaction], titular: str = ''):
        rows = []
        for tx in transactions:
            importe = -tx.amount if tx.tx_type == 'expense' else tx.amount
            rows.append([
                tx.fmt_date(),
                tx.description,
                importe,
                tx.category,
                tx.bank,
                titular or '',
                tx.date.strftime('%Y-%m'),
                tx.tx_type,
            ])
        if rows:
            self.ws.append_rows(rows)

    def get_monthly_summary(self, year: int, month: int, titular: str = None) -> dict:
        month_str = f"{year:04d}-{month:02d}"
        all_rows = self.ws.get_all_records()
        summary: dict[str, float] = {}
        total = 0.0
        for row in all_rows:
            if row.get('Mes') != month_str or row.get('Tipo') != 'expense':
                continue
            if titular and row.get('Titular', '') != titular:
                continue
            cat = row.get('Categoría') or 'Otros'
            raw = str(row.get('Importe', '0')).replace(',', '.')
            try:
                amount = abs(float(raw))
            except ValueError:
                continue
            summary[cat] = summary.get(cat, 0.0) + amount
            total += amount
        summary['__total__'] = total
        return summary

    def get_monthly_transactions(self, year: int, month: int, titular: str = None) -> list[dict]:
        month_str = f"{year:04d}-{month:02d}"
        all_rows = self.ws.get_all_records()
        result = []
        for row in all_rows:
            if row.get('Mes') != month_str or row.get('Tipo') != 'expense':
                continue
            if titular and row.get('Titular', '') != titular:
                continue
            raw = str(row.get('Importe', '0')).replace(',', '.')
            try:
                amount = abs(float(raw))
            except ValueError:
                amount = 0.0
            result.append({
                'date': row.get('Fecha', ''),
                'description': row.get('Descripción', ''),
                'amount': amount,
                'category': row.get('Categoría', 'Otros'),
                'bank': row.get('Banco', ''),
                'titular': row.get('Titular', ''),
            })
        return result

    def get_months_with_data(self, titular: str = None) -> list[str]:
        all_rows = self.ws.get_all_records()
        months = set()
        for r in all_rows:
            if r.get('Mes') and r.get('Tipo') == 'expense':
                if not titular or r.get('Titular', '') == titular:
                    months.add(r['Mes'])
        return sorted(months)

    def get_titulares(self) -> list[str]:
        rows = self.ws_personas.get_all_records()
        return [r['Titular'] for r in rows if r.get('Titular')]

    def add_titular(self, name: str):
        from datetime import datetime
        existing = self.get_titulares()
        if name not in existing:
            self.ws_personas.append_row([name, datetime.now().strftime('%Y-%m-%d')])
