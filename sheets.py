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
        self.ws_reglas = self._get_or_create_sheet('Reglas', ['Keyword', 'Categoría'])

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

    def _get_all_records(self) -> list[dict]:
        """Fetch all records with unformatted numeric values (avoids locale issues)."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return []
        headers = values[0]
        return [dict(zip(headers, row)) for row in values[1:]]

    def _existing_keys(self) -> set[tuple]:
        """Returns set of (fecha, descripcion, importe, banco) for all stored rows."""
        rows = self._get_all_records()
        return {
            (r.get('Fecha', ''), r.get('Descripción', ''), str(r.get('Importe', '')), r.get('Banco', ''))
            for r in rows
        }

    def write_transactions(self, transactions: list[Transaction], titular: str = '') -> int:
        """Write transactions, skipping duplicates. Returns number of rows actually written."""
        existing = self._existing_keys()
        rows = []
        for tx in transactions:
            importe = -tx.amount if tx.tx_type == 'expense' else tx.amount
            key = (tx.fmt_date(), tx.description, str(importe), tx.bank)
            if key in existing:
                continue
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
            existing.add(key)  # avoid dupes within the same batch
        if rows:
            self.ws.append_rows(rows)
        return len(rows)

    def get_monthly_summary(self, year: int, month: int, titular: str = None) -> dict:
        month_str = f"{year:04d}-{month:02d}"
        all_rows = self._get_all_records()
        summary: dict[str, float] = {}
        total_expenses = 0.0
        total_income = 0.0
        for row in all_rows:
            if row.get('Mes') != month_str:
                continue
            if titular and row.get('Titular', '') != titular:
                continue
            raw = str(row.get('Importe', '0')).replace(',', '.')
            try:
                amount = abs(float(raw))
            except ValueError:
                continue
            tipo = row.get('Tipo', '')
            cat = row.get('Categoría') or 'Otros'
            if tipo == 'expense':
                summary[cat] = summary.get(cat, 0.0) + amount
                total_expenses += amount
            elif tipo == 'income':
                desc = row.get('Descripción', '').upper()
                # Exclude only salary from offsetting income
                if 'NÓMINA' not in desc and 'NOMINA' not in desc:
                    summary['__income__'] = summary.get('__income__', 0.0) + amount
                    total_income += amount
        summary['__total__'] = total_expenses - total_income
        return summary

    def get_monthly_transactions(self, year: int, month: int, titular: str = None) -> list[dict]:
        month_str = f"{year:04d}-{month:02d}"
        all_rows = self._get_all_records()
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
        all_rows = self._get_all_records()
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

    def get_custom_rules(self) -> list[tuple[str, str]]:
        """Returns list of (keyword_upper, category) from the Reglas sheet."""
        rows = self.ws_reglas.get_all_records()
        return [(r['Keyword'].upper(), r['Categoría']) for r in rows if r.get('Keyword') and r.get('Categoría')]

    def add_custom_rule(self, keyword: str, category: str):
        existing = [kw for kw, _ in self.get_custom_rules()]
        if keyword.upper() not in existing:
            self.ws_reglas.append_row([keyword.upper(), category])
