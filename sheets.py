from __future__ import annotations

import base64
import json
import os
import time
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

PRESTAMOS_HEADERS = [
    'Concepto', 'Importe', 'Fecha', 'Entidad', 'Años', 'Carencia_años',
    'Cuota_mensual', 'Fecha_inicio_cuota', 'Nota',
]

_PRESTAMOS_SEED = [
    [
        'Préstamo hipotecario (neto)', 416000, '30/07/2026', 'Banco (BBVA)', '', '', '', '',
        'Desembolso bruto 425.317,96€ menos 9.317,96€ de seguro de vida vinculado',
    ],
    [
        'Préstamo padre — tramo 1 (arras)', 50000, '08/06/2026', 'Padre de María', 20, 5, 270, '06/2031',
        'Sin intereses',
    ],
    [
        'Préstamo padre — tramo 2 (cierre)', 40000, '16/07/2026', 'Padre de María', 20, 5, 222, '07/2031',
        'Sin intereses, se suma a la cuota del tramo 1',
    ],
]


def is_nomina(descripcion: str) -> bool:
    d = descripcion.upper()
    return 'NOMINA' in d or 'NÓMINA' in d


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
        self.ws_reglas = self._get_or_create_sheet('Reglas', ['Keyword', 'Categoría', 'Tipo'])
        self._ensure_header(self.ws_reglas, 'Tipo')
        self._ensure_header(self.ws, 'Vivienda')
        self.ws_prestamos = self._get_or_create_sheet('Prestamos', PRESTAMOS_HEADERS)
        self._seed_prestamos()
        self._cache_data: list[dict] = []
        self._cache_ts: float = 0.0

    def _seed_prestamos(self) -> None:
        """Puebla la hoja Prestamos con los 3 préstamos conocidos de la compra de
        vivienda si aún está vacía (solo cabecera). No-op si ya tiene datos."""
        values = self.ws_prestamos.get_all_values()
        if len(values) <= 1:
            self.ws_prestamos.append_rows(_PRESTAMOS_SEED)

    _CACHE_TTL: float = 60.0

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

    @staticmethod
    def _ensure_header(ws, header_name: str) -> None:
        """Añade una columna de cabecera a una hoja ya existente si no la tiene
        (migración in-place para hojas creadas antes de introducir esa columna)."""
        headers = ws.row_values(1)
        if header_name not in headers:
            ws.update_cell(1, len(headers) + 1, header_name)

    def _get_all_records(self) -> list[dict]:
        """Fetch all records with unformatted numeric values. Cached for 60s."""
        now = time.time()
        if self._cache_data and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache_data
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return []
        headers = values[0]
        self._cache_data = [dict(zip(headers, row)) for row in values[1:]]
        self._cache_ts = now
        return self._cache_data

    def _invalidate_cache(self) -> None:
        self._cache_ts = 0.0

    @staticmethod
    def _amount_key(v) -> str:
        """Normalize an amount to a consistent string key regardless of int/float representation."""
        try:
            return str(round(float(str(v).replace(',', '.')), 2))
        except (ValueError, TypeError):
            return str(v)

    def _existing_keys(self) -> set[tuple]:
        """Returns set of (fecha, descripcion, importe_normalized, banco) for all stored rows."""
        rows = self._get_all_records()
        return {
            (r.get('Fecha', ''), r.get('Descripción', ''), self._amount_key(r.get('Importe', '')), r.get('Banco', ''))
            for r in rows
        }

    def write_transactions(self, transactions: list[Transaction], titular: str = '') -> list[Transaction]:
        """Write transactions, skipping duplicates. Returns the transactions actually written."""
        existing = self._existing_keys()
        rows = []
        saved: list[Transaction] = []
        for tx in transactions:
            importe = -tx.amount if tx.tx_type == 'expense' else tx.amount
            key = (tx.fmt_date(), tx.description, self._amount_key(importe), tx.bank)
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
            existing.add(key)
            saved.append(tx)
        if rows:
            self.ws.append_rows(rows)
            self._invalidate_cache()
        return saved

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
            elif tipo == 'income' and row.get('Banco', '') != 'Santander':
                if not is_nomina(row.get('Descripción', '')):
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
                'category': row.get('Categoría') or 'Otros',
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

    def get_learned_classifications(self) -> dict[str, str]:
        """Returns dict of {description_upper: category} from historical expenses."""
        rows = self._get_all_records()
        learned = {}
        for r in rows:
            desc = r.get('Descripción', '').strip().upper()
            cat = r.get('Categoría', '').strip()
            if desc and cat and cat != 'Otros':
                learned[desc] = cat
        return learned

    def get_rules(self) -> list[dict]:
        """Returns every row of the Reglas sheet as {'keyword', 'categoria', 'tipo'}.
        Tipo vacío = regla de categoría (usa 'categoria'); Tipo='exclude' = excluir
        la transacción del tracking; cualquier otro Tipo = forzar ese tx_type."""
        rows = self.ws_reglas.get_all_records()
        return [
            {
                'keyword': r['Keyword'].upper(),
                'categoria': r.get('Categoría', '').strip(),
                'tipo': r.get('Tipo', '').strip().lower(),
            }
            for r in rows if r.get('Keyword')
        ]

    def add_custom_rule(self, keyword: str, category: str):
        """Añade una regla de categoría (Tipo vacío) desde el comando de texto del bot."""
        existing = {r['keyword'] for r in self.get_rules()}
        if keyword.upper() not in existing:
            self.ws_reglas.append_row([keyword.upper(), category, ''])

    def fix_santander_expense_types(self) -> int:
        """Set Tipo=internal for all Santander rows currently marked as expense. Returns rows updated."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return 0
        headers = values[0]
        try:
            banco_col = headers.index('Banco')
            tipo_col = headers.index('Tipo')
        except ValueError:
            return 0
        tipo_col_1 = tipo_col + 1  # gspread update_cell is 1-indexed
        updated = 0
        for i, row in enumerate(values[1:], start=2):
            if len(row) <= max(banco_col, tipo_col):
                continue
            if row[banco_col] == 'Santander' and row[tipo_col] == 'expense':
                self.ws.update_cell(i, tipo_col_1, 'internal')
                updated += 1
        if updated:
            self._invalidate_cache()
        return updated

    def update_transaction_tipo(self, descripcion_contains: str, banco: str, new_tipo: str) -> int:
        """Update Tipo for all rows matching banco and description substring. Returns count updated."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return 0
        headers = values[0]
        try:
            desc_col = headers.index('Descripción')
            banco_col = headers.index('Banco')
            tipo_col = headers.index('Tipo')
        except ValueError:
            return 0
        tipo_col_1 = tipo_col + 1
        needle = descripcion_contains.upper()
        updated = 0
        for i, row in enumerate(values[1:], start=2):
            if len(row) <= max(desc_col, banco_col, tipo_col):
                continue
            if row[banco_col] == banco and needle in row[desc_col].upper():
                self.ws.update_cell(i, tipo_col_1, new_tipo)
                updated += 1
        if updated:
            self._invalidate_cache()
        return updated

    def update_transaction_category(self, fecha: str, descripcion: str, amount: float, banco: str, new_category: str) -> bool:
        """Find a transaction by (fecha, descripcion, abs(amount), banco) and update its category."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return False
        headers = values[0]
        try:
            fecha_col = headers.index('Fecha')
            desc_col = headers.index('Descripción')
            importe_col = headers.index('Importe')
            banco_col = headers.index('Banco')
            cat_col = headers.index('Categoría') + 1  # 1-indexed for update_cell
        except ValueError:
            return False
        target_amount = round(abs(amount), 2)
        for i, row in enumerate(values[1:], start=2):
            if len(row) <= max(fecha_col, desc_col, importe_col, banco_col):
                continue
            try:
                row_amount = round(abs(float(str(row[importe_col]).replace(',', '.'))), 2)
            except ValueError:
                continue
            if (row[fecha_col] == fecha and
                    row[desc_col] == descripcion and
                    row_amount == target_amount and
                    row[banco_col] == banco):
                self.ws.update_cell(i, cat_col, new_category)
                self._invalidate_cache()
                return True
        return False

    def mark_vivienda(self, fecha: str, descripcion: str, amount: float, banco: str) -> bool:
        """Marca Vivienda='Sí' en una fila localizada por (fecha, descripcion, abs(importe), banco),
        sin tocar Tipo ni Categoría. Mismo patrón de matching que update_transaction_category —
        pensado para filas históricas que deben conservar su Tipo/Categoría actual."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return False
        headers = values[0]
        try:
            fecha_col = headers.index('Fecha')
            desc_col = headers.index('Descripción')
            importe_col = headers.index('Importe')
            banco_col = headers.index('Banco')
            vivienda_col = headers.index('Vivienda') + 1  # 1-indexed for update_cell
        except ValueError:
            return False
        target_amount = round(abs(amount), 2)
        for i, row in enumerate(values[1:], start=2):
            if len(row) <= max(fecha_col, desc_col, importe_col, banco_col):
                continue
            try:
                row_amount = round(abs(float(str(row[importe_col]).replace(',', '.'))), 2)
            except ValueError:
                continue
            if (row[fecha_col] == fecha and
                    row[desc_col] == descripcion and
                    row_amount == target_amount and
                    row[banco_col] == banco):
                self.ws.update_cell(i, vivienda_col, 'Sí')
                self._invalidate_cache()
                return True
        return False

    def mark_vivienda_by_category(self, categories: list[str]) -> list[dict]:
        """Marca Vivienda='Sí' en todas las filas cuya Categoría esté en `categories`
        y que aún no tengan la marca. Devuelve las filas marcadas (fecha, descripcion,
        importe, banco) para poder verificarlas."""
        values = self.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return []
        headers = values[0]
        try:
            fecha_col = headers.index('Fecha')
            desc_col = headers.index('Descripción')
            importe_col = headers.index('Importe')
            banco_col = headers.index('Banco')
            cat_col = headers.index('Categoría')
            vivienda_col = headers.index('Vivienda') + 1  # 1-indexed for update_cell
        except ValueError:
            return []
        marked = []
        for i, row in enumerate(values[1:], start=2):
            if len(row) <= cat_col or row[cat_col] not in categories:
                continue
            current = row[vivienda_col - 1] if len(row) > vivienda_col - 1 else ''
            if current == 'Sí':
                continue
            self.ws.update_cell(i, vivienda_col, 'Sí')
            marked.append({
                'fecha': row[fecha_col] if len(row) > fecha_col else '',
                'descripcion': row[desc_col] if len(row) > desc_col else '',
                'importe': row[importe_col] if len(row) > importe_col else '',
                'banco': row[banco_col] if len(row) > banco_col else '',
            })
        if marked:
            self._invalidate_cache()
        return marked

    def get_prestamos(self) -> list[dict]:
        """Todas las filas de la hoja Prestamos (financiación de la compra de vivienda:
        hipoteca + préstamos familiares). Fuente única de `total_financiado` — nunca se
        calcula sumando transacciones."""
        values = self.ws_prestamos.get_all_values(value_render_option='UNFORMATTED_VALUE')
        if not values:
            return []
        headers = values[0]
        return [dict(zip(headers, row)) for row in values[1:] if row and row[0]]

    def get_total_financiado(self) -> float:
        """Suma del campo Importe de todas las filas de la hoja Prestamos."""
        total = 0.0
        for r in self.get_prestamos():
            try:
                total += float(str(r.get('Importe', 0) or 0).replace(',', '.'))
            except (ValueError, TypeError):
                continue
        return total

    def get_vivienda_transactions(self) -> list[dict]:
        """Todas las filas de la vista Vivienda: Categoría en {'Compra vivienda', 'Hipoteca'}
        o Vivienda='Sí' (columna independiente de Tipo/Categoría, no afecta cash flow).
        Ordenado cronológicamente ascendente."""
        rows = self._get_all_records()
        result = []
        for r in rows:
            cat = r.get('Categoría', '')
            if cat not in ('Compra vivienda', 'Hipoteca') and r.get('Vivienda', '') != 'Sí':
                continue
            raw = str(r.get('Importe', '0')).replace(',', '.')
            try:
                amount = float(raw)
            except ValueError:
                amount = 0.0
            result.append({
                'date': r.get('Fecha', ''),
                'description': r.get('Descripción', ''),
                'category': cat or 'Otros',
                'amount': amount,
                'bank': r.get('Banco', ''),
                'titular': r.get('Titular', ''),
                'tipo': r.get('Tipo', ''),
            })

        def _sort_key(item: dict) -> tuple[int, int, int]:
            try:
                d, m, y = item['date'].split('/')
                return (int(y), int(m), int(d))
            except (ValueError, AttributeError):
                return (0, 0, 0)

        result.sort(key=_sort_key)
        return result
