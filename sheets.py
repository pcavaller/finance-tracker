from __future__ import annotations

import base64
import json
import os
import re
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


_RENTA_TRABAJO_KEYWORDS = ('STRIPE', 'BUENCOCO', 'HOSPITAL SANT JOAN', 'SAMARANCH GALLART')

# Pagadores recurrentes de sesiones de la consulta de psicología de María cuyo
# concepto de transferencia NO siempre dice "sesion" (la sociedad del paciente o
# el propio paciente ponen su nombre). Lista explícita y ampliable: añadir aquí
# cualquier pagador nuevo que se detecte sin la palabra "sesion" en el concepto.
_MARIA_CONSULTA_PAGADORES = (
    'CHEVERE J.R. SL', 'CHEVERE JR', 'CASTILLERO YUSTE', 'DOMINGUEZ NAVARRO',
    'MONTEAGUDO MARTINEZ', 'VALERIA DUARTE',
)

# Pagadoras de la consulta cuyo concepto habitual es "terapia" o "cita", no
# "sesion". Esas dos palabras son demasiado comunes para contar por sí solas (un
# "cita previa" o "terapia de pareja" en un ingreso de Pablo no es facturación),
# así que solo cuentan si la descripción trae además una de estas pagadoras.
# Incluye las de arriba más Rocío Novella y "VALERIA D R" (cadena muy corta: nunca
# se acepta como match suelto, solo combinada con terapia/cita).
_MARIA_TERAPIA_CITA_PAGADORES = _MARIA_CONSULTA_PAGADORES + ('NOVELLA CEPERUELO', 'VALERIA D R')

# Concepto de sesión fechada de un paciente: "Sesion 13 Julio", "Sesion 26 Agosto",
# "Sesi-n 3 Octubre". La ó de "sesión" llega a veces mal codificada como guion o
# guion bajo. Distingue la consulta de María de un "Sesion de padel" o "Sesion de
# coaching" que pudiera aparecer en un ingreso de Pablo.
_SESION_FECHADA_RE = re.compile(r'SESI[OÓ\-_]N\s+\d')

# "Terapia" / "cita" como palabra completa (evita casar 'solicita', 'citado', etc.).
_TERAPIA_CITA_RE = re.compile(r'\b(?:TERAPIA|CITA)\b')


def _is_sesion_psicologia_maria(desc_upper: str) -> bool:
    """Pago de un paciente por una sesión de la consulta de psicología de María
    (transferencia directa o Bizum). Es facturación de negocio propio, igual que
    Stripe/Buencoco o el datáfono de Samaranch Gallart, así que cuenta como renta
    de trabajo y queda fuera de los ingresos compensatorios. Aprobado por Pablo
    2026-09-01.

    Se reconoce por: (1) pagador conocido (_MARIA_CONSULTA_PAGADORES, para los que
    no ponen concepto claro); (2) concepto "terapia" o "cita" como palabra completa
    junto a una pagadora de _MARIA_TERAPIA_CITA_PAGADORES (esas palabras solas son
    demasiado comunes y la función no recibe el titular, así que se exigen ambas);
    (3) sesión fechada tipo 'Sesion 26 Agosto' / 'Sesi-n 3 Octubre'; (4) 'sesion' +
    'psicolog'. No basta la palabra 'sesion' suelta, para no arrastrar un
    'Sesion de padel'/'Sesion de coaching' de un ingreso de Pablo. Verificado el
    2026-09-01 contra todo el histórico de ingresos de María: cubre los pagadores
    vistos (Chevere, Castillero Yuste, Dominguez Navarro, Blanch Moliner,
    Monteagudo Martinez, Novella Ceperuelo, Valeria Duarte) sin falsos positivos."""
    if any(p in desc_upper for p in _MARIA_CONSULTA_PAGADORES):
        return True
    if _TERAPIA_CITA_RE.search(desc_upper) and any(p in desc_upper for p in _MARIA_TERAPIA_CITA_PAGADORES):
        return True
    if _SESION_FECHADA_RE.search(desc_upper):
        return True
    return ('SESION' in desc_upper or 'SESIÓN' in desc_upper) and 'PSICOLOG' in desc_upper


def is_renta_trabajo(descripcion: str) -> bool:
    """Ingreso real de trabajo (nómina o negocio propio). Usado tanto por el
    Dashboard general (ver real_income en get_monthly_summary) como para excluir
    salario de 'ingresos compensatorios' en /api/summary y /api/annual — confirmado
    con Pablo 2026-08-07 que toda esta lista es sueldo/negocio recurrente, no
    ingreso puntual, así que debe quedar fuera de ambos cálculos por igual.
    Confirmado con Pablo 2026-08-03: Nómina DiverInvest (Pablo) + Stripe/Buencoco
    (consulta de María) + Hospital Sant Joan de Déu (nómina hospital de María) +
    Datafono Samaranch Gallart (ingreso de María). Ampliado 2026-09-01: sesiones de
    psicología que los pacientes de María pagan directamente por transferencia
    (ver _is_sesion_psicologia_maria). Todo lo demás que aparece como Tipo=income
    (Bizums, dinero de familia, devoluciones/cashback, transferencias entre cuentas
    propias con nombre distinto) queda fuera — no es renta de trabajo."""
    if is_nomina(descripcion):
        return True
    d = descripcion.upper()
    if any(kw in d for kw in _RENTA_TRABAJO_KEYWORDS):
        return True
    return _is_sesion_psicologia_maria(d)


def sum_ingresos_no_laborales(rows: list[dict]) -> float:
    """Σ ABS(Importe) de las filas con Tipo == 'income' que NO son renta de
    trabajo (is_renta_trabajo(desc) == False). `rows` viene ya filtrado por
    periodo/titular. Es el conjunto que netea contra el gasto para el número
    secundario "gasto neto tras ingresos no laborales" en Inicio, Anual,
    Personas y Dashboard general.

    A diferencia del modo compensatorio de get_monthly_summary, aquí NO se aplica
    ningún carve-out de Santander: el set es simplemente income ∧ ¬renta_trabajo.
    'alquiler', 'fianza', 'patrimonio', 'internal' e 'investment' quedan fuera por
    no tener Tipo == 'income'."""
    total = 0.0
    for r in rows:
        if r.get('Tipo') != 'income':
            continue
        if is_renta_trabajo(r.get('Descripción', '')):
            continue
        try:
            total += abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
    return total


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

    def get_monthly_summary(self, year: int, month: int, titular: str = None, real_income: bool = False) -> dict:
        """Por defecto calcula 'ingresos compensatorios' (excluye renta de trabajo —
        ver is_renta_trabajo — y Banco=Santander; usado por /api/summary y /api/annual
        para mostrar solo ingresos puntuales no-salariales). Con real_income=True
        cuenta solo renta de trabajo real — usado por /api/panorama_12m, que necesita
        nómina + negocio de María, no el compensatorio ni el resto de ruido (Bizums,
        familia, devoluciones...)."""
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
                desc = row.get('Descripción', '')
                is_compensatorio = row.get('Banco', '') != 'Santander' and not is_renta_trabajo(desc)
                counts = is_renta_trabajo(desc) if real_income else is_compensatorio
                if counts:
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

    def get_alquiler_vivienda(self, months: int = 12) -> dict:
        """Renta de habitaciones (Tipo 'alquiler'): total y desglose mensual de los
        últimos `months` meses. Tipo 'alquiler' es paralelo a 'fianza'/'patrimonio':
        queda fuera de income/expense en get_monthly_summary y en toda agregación de
        cash flow, así que NO aparece en Inicio, en el anual ni en el panorama de
        ingresos. Esto es lo único que lo lee, para mostrarlo aparte en la sección
        Vivienda del Dashboard general."""
        from datetime import datetime
        now = datetime.now()
        meses_validos = set()
        y, m = now.year, now.month
        for _ in range(months):
            meses_validos.add(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        mensual: dict[str, float] = {}
        for r in self._get_all_records():
            if r.get('Tipo') != 'alquiler' or r.get('Mes') not in meses_validos:
                continue
            try:
                amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
            except ValueError:
                continue
            mensual[r['Mes']] = mensual.get(r['Mes'], 0.0) + amt
        return {
            'total': round(sum(mensual.values()), 2),
            'mensual': {k: round(v, 2) for k, v in sorted(mensual.items())},
        }

    def get_vivienda_transactions(self) -> list[dict]:
        """Todas las filas de la vista Vivienda: Categoría en {'Compra vivienda', 'Hipoteca'}
        o Vivienda='Sí' (columna independiente de Tipo/Categoría, no afecta cash flow).
        Ordenado cronológicamente descendente (más reciente primero)."""
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

        result.sort(key=_sort_key, reverse=True)
        return result
