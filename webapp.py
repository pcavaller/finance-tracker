#!/usr/bin/env python3
"""Finance Tracker Web App"""

import hashlib
import hmac
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl

from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, Depends, FastAPI, Header, UploadFile, HTTPException, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from parsers import (
    detect_bank, TradeRepublicParser, OpenbankParser,
    OpenbankPDFParser, RevolutPDFParser, Transaction, BBVAParser,
)
from classifier import classify_batch, load_custom_rules, apply_type_overrides, apply_exclusions
from sheets import SheetsClient, is_renta_trabajo, sum_ingresos_no_laborales

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
ALLOWED_CHAT_IDS: set[int] = {int(x) for x in os.getenv('ALLOWED_CHAT_IDS', '').split(',') if x}

app = FastAPI(title="Finance Tracker")


def _check_telegram_init_data(init_data: str, max_age_seconds: int = 86400) -> dict | None:
    """Validate a Telegram WebApp initData string per Telegram's HMAC scheme.
    Returns the decoded `user` dict if valid and fresh, None otherwise."""
    if not init_data:
        return None
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop('hash', None)
    if not received_hash:
        return None
    data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    if time.time() - int(parsed.get('auth_date', 0)) > max_age_seconds:
        return None
    try:
        return json.loads(parsed.get('user', '{}'))
    except json.JSONDecodeError:
        return None


async def require_telegram_user(x_telegram_init_data: str = Header(default='')) -> dict:
    user = _check_telegram_init_data(x_telegram_init_data)
    if not user or user.get('id') not in ALLOWED_CHAT_IDS:
        raise HTTPException(403, "No autorizado.")
    return user


api = APIRouter(prefix="/api", dependencies=[Depends(require_telegram_user)])


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Sin esto, el WebView nativo de Telegram móvil (iOS/Android) cachea agresivamente
    index.html/JS/CSS al no llevar Cache-Control explícito (Telegram Desktop no mostraba
    el problema porque revalida solo) — quedaba con una versión vieja de la Mini App
    hasta cerrar y reabrir la app de Telegram entera."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        return response

app.add_middleware(NoCacheMiddleware)

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


@api.get("/people")
async def get_people():
    return {"people": sheets.get_titulares()}


@api.post("/upload")
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

    if filename.lower().endswith('.xlsx'):
        suffix = '.xlsx'
    elif bank_key in ('trade_republic', 'openbank_pdf', 'revolut') or filename.lower().endswith('.pdf'):
        suffix = '.pdf'
    else:
        suffix = '.xls'

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
        elif bank_key == 'bbva':
            all_txs = BBVAParser().parse(tmp_path)
            bank_name = 'BBVA'
        else:
            all_txs = OpenbankParser().parse(tmp_path)
            bank_name = 'Openbank'

        apply_type_overrides(all_txs)
        all_txs = apply_exclusions(all_txs)
        expenses = [tx for tx in all_txs
                    if tx.tx_type == 'expense'
                    or (tx.tx_type == 'income' and (
                        '[Devolución]' in tx.description
                        or 'Bizum de' in tx.description
                        or 'BIZUM DE' in tx.description.upper()
                    ))]
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


@api.post("/save/{session_id}")
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

    saved_txs = sheets.write_transactions(to_save, titular=body.titular)
    sheets.add_titular(body.titular)
    del sessions[session_id]

    return {'saved': len(saved_txs), 'total': sum(tx.amount for tx in saved_txs)}


@api.get("/summary")
async def get_summary(year: int = None, month: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    summary = sheets.get_monthly_summary(year, month, titular=titular or None)
    total = summary.pop('__total__', 0.0)
    income = summary.pop('__income__', 0.0)
    total_expenses = round(sum(summary.values()), 2)

    month_str = f"{year:04d}-{month:02d}"
    income_items = []
    for r in sheets._get_all_records():
        if r.get('Mes') != month_str or r.get('Tipo') != 'income':
            continue
        if r.get('Banco', '') == 'Santander':
            continue
        desc = r.get('Descripción', '')
        if is_renta_trabajo(desc):
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
        income_items.append({'description': desc, 'amount': amt, 'date': r.get('Fecha', '')})
    income_items.sort(key=lambda x: x['amount'], reverse=True)

    period_rows = [
        r for r in sheets._get_all_records()
        if r.get('Mes') == month_str and (not titular or r.get('Titular', '') == titular)
    ]
    ingresos_no_laborales = round(sum_ingresos_no_laborales(period_rows), 2)
    gasto_neto = round(total_expenses - ingresos_no_laborales, 2)

    return {'summary': summary, 'total': total, 'total_expenses': total_expenses, 'income': income, 'income_items': income_items, 'ingresos_no_laborales': ingresos_no_laborales, 'gasto_neto': gasto_neto, 'year': year, 'month': month}


@api.get("/transactions")
async def get_transactions(year: int = None, month: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    txs = sheets.get_monthly_transactions(year, month, titular=titular or None)
    return {'transactions': txs}


@api.get("/months")
async def get_months(titular: str = None):
    return {'months': sheets.get_months_with_data(titular=titular or None)}


@api.get("/annual")
async def get_annual(year: int = None, titular: str = None):
    now = datetime.now()
    year = year or now.year
    rows = sheets._get_all_records()
    cat_totals: dict[str, float] = {}
    month_expenses: dict[str, float] = {}
    month_inl: dict[str, float] = {}  # ingresos no laborales (income ∧ ¬renta_trabajo), por mes
    for r in rows:
        mes = r.get('Mes', '')
        if not mes.startswith(str(year)):
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        tipo = r.get('Tipo', '')
        try:
            amt = abs(float(str(r.get('Importe', 0)).replace(',', '.')))
        except ValueError:
            continue
        if tipo == 'expense':
            cat = r.get('Categoría') or 'Otros'
            cat_totals[cat] = cat_totals.get(cat, 0.0) + amt
            month_expenses[mes] = month_expenses.get(mes, 0.0) + amt
        elif tipo == 'income' and not is_renta_trabajo(r.get('Descripción', '')):
            month_inl[mes] = month_inl.get(mes, 0.0) + amt
    sorted_months = []
    for m, v in sorted(month_expenses.items()):
        if v <= 0:
            continue
        inl = round(month_inl.get(m, 0.0), 2)
        sorted_months.append({
            'month': m,
            'total': round(v, 2),
            'ingresos_no_laborales': inl,
            'gasto_neto': round(v - inl, 2),
        })
    sorted_cats = sorted([{'name': k, 'amount': round(v, 2)} for k, v in cat_totals.items()], key=lambda x: -x['amount'])
    gross_total = round(sum(m['total'] for m in sorted_months), 2)
    total_inl = round(sum(month_inl.values()), 2)
    return {
        'year': year,
        'categories': sorted_cats,
        'months': sorted_months,
        'total': gross_total,
        'ingresos_no_laborales': total_inl,
        'gasto_neto': round(gross_total - total_inl, 2),
        'months_with_data': len(sorted_months),
    }


@api.get("/monthly_totals")
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


class UpdateCategoryRequest(BaseModel):
    fecha: str
    descripcion: str
    amount: float
    banco: str
    category: str


@api.patch("/transaction/category")
async def update_transaction_category(body: UpdateCategoryRequest):
    ok = sheets.update_transaction_category(
        body.fecha, body.descripcion, body.amount, body.banco, body.category
    )
    if not ok:
        raise HTTPException(404, "Transacción no encontrada.")
    return {"ok": True}


# total_financiado NUNCA se calcula sumando transacciones — es un hecho fijo que vive en
# la hoja "Prestamos" (sheets.get_total_financiado). Las dos heurísticas por keyword usadas
# antes aquí para inferir "financiado" vs "pagado" a partir del signo/palabras del concepto
# se han equivocado dos veces; en su lugar, total_pagado se calcula sumando TODAS las filas
# marcadas Vivienda, excluyendo por descripción EXACTA (no keyword) las que son solo el
# recibo del préstamo (ya contado en total_financiado, gastado más tarde en otra fila).
# El seguro de vida vinculado (Pablo + María) SÍ cuenta como financiado (viene incluido en
# el desembolso de 425.317,96€ del banco, confirmado por el propio Pablo) y por tanto SÍ es
# un gasto real pagado — se mantiene como bucket propio 'seguro' solo para colorearlo aparte
# en la lista, no para excluirlo de total_pagado.
_VIVIENDA_EXCLUDE_FROM_PAGADO = {
    'Abono por disposicion de prestamo/credito',
    'Transferencia recibida - Préstamo padre de María (Ruisánchez González-Barros)',
}
_VIVIENDA_SEGURO_VINCULADO = {'Adeudo de seguros'}


def _build_calendario_padre(prestamos: list[dict]) -> Optional[dict]:
    """Calendario de devolución del préstamo del padre de María, calculado dinámicamente
    respecto a la fecha actual del servidor a partir de los tramos en la hoja Prestamos
    (Entidad == 'Padre de María'). No hardcodea meses/años restantes."""
    tramos = []
    for p in prestamos:
        if p.get('Entidad') != 'Padre de María':
            continue
        fecha_inicio = str(p.get('Fecha_inicio_cuota', '')).strip()
        if not fecha_inicio or '/' not in fecha_inicio:
            continue
        mes_str, anio_str = fecha_inicio.split('/')
        try:
            tramos.append({
                'concepto': p.get('Concepto', ''),
                'importe': float(str(p.get('Importe', 0) or 0).replace(',', '.')),
                'cuota_mensual': float(str(p.get('Cuota_mensual', 0) or 0).replace(',', '.')),
                'anio': int(anio_str),
                'mes': int(mes_str),
            })
        except ValueError:
            continue
    if not tramos:
        return None
    tramos.sort(key=lambda t: (t['anio'], t['mes']))

    def _months_between(y1: int, m1: int, y2: int, m2: int) -> int:
        return (y2 - y1) * 12 + (m2 - m1)

    now = datetime.now()
    hito1, hito2 = tramos[0], (tramos[1] if len(tramos) > 1 else None)
    meses_hasta_hito1 = _months_between(now.year, now.month, hito1['anio'], hito1['mes'])

    if meses_hasta_hito1 > 0:
        estado = 'en_carencia'
    elif hito2 and _months_between(now.year, now.month, hito2['anio'], hito2['mes']) > 0:
        estado = 'tramo1_activo'
    elif hito2:
        estado = 'tramo2_activo'
    else:
        estado = 'tramo1_activo'

    total_a_devolver = round(sum(t['importe'] for t in tramos), 2)
    return {
        'estado': estado,
        'meses_restantes_carencia': max(meses_hasta_hito1, 0),
        'anios_restantes_carencia': round(max(meses_hasta_hito1, 0) / 12, 1),
        'hito1': {
            'concepto': hito1['concepto'],
            'fecha': f"{hito1['mes']:02d}/{hito1['anio']}",
            'cuota_mensual': round(hito1['cuota_mensual'], 2),
        },
        'hito2': ({
            'concepto': hito2['concepto'],
            'fecha': f"{hito2['mes']:02d}/{hito2['anio']}",
            'cuota_total': round(hito1['cuota_mensual'] + hito2['cuota_mensual'], 2),
        } if hito2 else None),
        'total_a_devolver': total_a_devolver,
    }


def _parse_sheet_date(value) -> Optional[datetime]:
    """Parsea una fecha DD/MM/YYYY, o el número de serie de fecha de Google Sheets si la
    celda se autoconvirtió a fecha al escribirla (epoch de Sheets: 30/12/1899 = día 0) —
    con UNFORMATTED_VALUE, una celda con pinta de fecha vuelve como número, no como texto."""
    s = str(value).strip()
    if not s:
        return None
    if '/' in s:
        try:
            dia, mes, anio = s.split('/')
            return datetime(int(anio), int(mes), int(dia))
        except ValueError:
            return None
    try:
        serial = float(s)
    except ValueError:
        return None
    return datetime(1899, 12, 30) + timedelta(days=serial)


def _build_amortizacion_hipoteca(prestamos: list[dict], vivienda_txs: list[dict]) -> Optional[dict]:
    """Tabla de amortización francesa de la hipoteca, calculada dinámicamente respecto a
    la fecha actual del servidor a partir de los datos fijos en la hoja Prestamos (Entidad
    empieza por 'Banco'): Importe (capital, incluye el seguro de vida vinculado — confirmado
    por Pablo que el banco lo financió dentro del desembolso), TIN, Cuota_mensual,
    Fecha_inicio_cuota (DD/MM/YYYY, primera cuota regular) y Numero_cuotas. La proyección de
    359 cuotas en sí no depende de las transacciones reales — es calculada con los términos
    del contrato, igual que la tabla que Pablo ya tiene en su Excel. Pero antes de la primera
    cuota regular hay un cargo real de interés prorrateado (p.ej. los 28,55€ del 31/07/2026,
    interés de los días entre el desembolso y el inicio de la cuota mensual) que si no se
    suma aparte, el "total de intereses a lo largo del préstamo" se queda corto."""
    fila = next((p for p in prestamos if str(p.get('Entidad', '')).startswith('Banco')), None)
    if not fila:
        return None
    try:
        principal = float(str(fila.get('Importe', 0) or 0).replace(',', '.'))
        tin = float(str(fila.get('TIN', 0) or 0).replace(',', '.'))
        cuota = float(str(fila.get('Cuota_mensual', 0) or 0).replace(',', '.'))
        n_cuotas = int(float(str(fila.get('Numero_cuotas', 0) or 0).replace(',', '.')))
        primera_cuota = _parse_sheet_date(fila.get('Fecha_inicio_cuota', ''))
        if primera_cuota is None:
            return None
    except (ValueError, AttributeError):
        return None
    if not (principal and tin and cuota and n_cuotas):
        return None

    r = tin / 100 / 12
    now = datetime.now()

    balance = principal
    capital_pendiente_hoy = principal
    interes_acumulado = 0.0
    capital_amortizado = 0.0
    interes_total_vida = 0.0
    cuotas_pagadas = 0
    fecha_cuota = primera_cuota
    for i in range(1, n_cuotas + 1):
        interes_mes = round(balance * r, 2)
        capital_mes = round(cuota - interes_mes, 2)
        interes_total_vida += interes_mes
        balance = round(balance - capital_mes, 2)
        if fecha_cuota <= now:
            interes_acumulado += interes_mes
            capital_amortizado += capital_mes
            cuotas_pagadas = i
            capital_pendiente_hoy = balance
        # avanza un mes
        mes_n = fecha_cuota.month + 1
        anio_n = fecha_cuota.year + (1 if mes_n > 12 else 0)
        mes_n = mes_n - 12 if mes_n > 12 else mes_n
        dia_n = min(fecha_cuota.day, 28)
        fecha_cuota = datetime(anio_n, mes_n, dia_n)

    # Cargos reales de Categoría=Hipoteca anteriores a la primera cuota regular (p.ej. el
    # interés prorrateado del día del desembolso) — no forman parte del calendario de 359
    # cuotas, pero sí son intereses reales ya pagados que hay que sumar al total de la vida
    # del préstamo para que no quede corto.
    interes_previo_a_cuotas = 0.0
    for tx in vivienda_txs:
        if tx.get('category') != 'Hipoteca':
            continue
        fecha_tx = _parse_sheet_date(tx.get('date', ''))
        if fecha_tx and fecha_tx < primera_cuota:
            interes_previo_a_cuotas += abs(tx.get('amount', 0))

    return {
        'capital_inicial': round(principal, 2),
        'tin': tin,
        'cuota_mensual': round(cuota, 2),
        'numero_cuotas': n_cuotas,
        'cuotas_pagadas': cuotas_pagadas,
        'capital_pendiente': round(capital_pendiente_hoy, 2),
        'capital_amortizado_acumulado': round(capital_amortizado, 2),
        'interes_pagado_acumulado': round(interes_acumulado + interes_previo_a_cuotas, 2),
        'interes_previo_a_cuotas': round(interes_previo_a_cuotas, 2),
        'interes_total_vida_prestamo': round(interes_total_vida + interes_previo_a_cuotas, 2),
        'fecha_primera_cuota': primera_cuota.strftime('%d/%m/%Y'),
    }


@api.get("/vivienda")
async def get_vivienda():
    txs = sheets.get_vivienda_transactions()
    total_pagado = 0.0
    seguro_vinculado = 0.0
    tx_out = []
    for r in txs:
        desc = r['description']
        if desc in _VIVIENDA_EXCLUDE_FROM_PAGADO:
            bucket = 'financiacion_recibida'
        elif r['category'] == 'Hipoteca':
            # Cuota + interés prorrateado de la hipoteca: servicio de deuda recurrente,
            # ya cuenta como gasto mensual en el cash flow (get_monthly_summary). No es
            # aportación a la compra, así que NO va en total_pagado ni infla "de bolsillo".
            # Check por categoría (no por descripción) porque habrá una fila Hipoteca al mes.
            bucket = 'hipoteca'
        else:
            total_pagado += abs(r['amount'])
            bucket = 'seguro' if desc in _VIVIENDA_SEGURO_VINCULADO else 'pagado'
            if desc in _VIVIENDA_SEGURO_VINCULADO:
                seguro_vinculado += abs(r['amount'])
        tx_out.append({
            'date': r['date'],
            'description': desc,
            'category': r['category'],
            'amount': r['amount'],
            'bucket': bucket,
            'bank': r['bank'],
            'titular': r['titular'],
        })

    total_financiado = sheets.get_total_financiado()
    prestamos = sheets.get_prestamos()
    alquiler = sheets.get_alquiler_vivienda(months=12)
    return {
        'transactions': tx_out,
        'summary': {
            'total_financiado': round(total_financiado, 2),
            'total_pagado': round(total_pagado, 2),
            'diferencia': round(total_financiado - total_pagado, 2),
            'seguro_vinculado': round(seguro_vinculado, 2),
        },
        'ingresos_alquiler_12m': alquiler['total'],
        'ingresos_alquiler_mensual': alquiler['mensual'],
        'prestamos': prestamos,
        'calendario_padre': _build_calendario_padre(prestamos),
        'amortizacion_hipoteca': _build_amortizacion_hipoteca(prestamos, tx_out),
    }


def _panorama_months(period: str) -> list[tuple[int, int]]:
    """Meses (año, mes) del período pedido, en orden cronológico ascendente.
    Un año natural (`period` en '2024'..'2026') da sus 12 meses; '12m' da la
    ventana móvil actual (mes en curso incluido, igual que antes)."""
    now = datetime.now()
    if period.isdigit() and len(period) == 4:
        year = int(period)
        return [(year, m) for m in range(1, 13)]
    months = []
    y, m = now.year, now.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()
    return months


def _completitud_meses(months: list[tuple[int, int]], titular: str = None) -> list[dict]:
    """Nº de filas expense/income por mes de `months` vs la media de los demás
    meses del propio set (regla de PROJECT.md: desviación > 40% → incompleto).
    Mes futuro o con 0 filas → 'sin_datos' (no es lo mismo que 'incompleto': un
    mes futuro no es un import fallido, simplemente no ha pasado)."""
    now = datetime.now()
    rows = sheets._get_all_records()
    counts: dict[str, int] = {f"{y:04d}-{m:02d}": 0 for y, m in months}
    for r in rows:
        mes = r.get('Mes', '')
        if mes not in counts:
            continue
        if r.get('Tipo') not in ('expense', 'income'):
            continue
        if titular and r.get('Titular', '') != titular:
            continue
        counts[mes] += 1

    out = []
    for y, m in months:
        mes = f"{y:04d}-{m:02d}"
        n_filas = counts[mes]
        otros = [c for k, c in counts.items() if k != mes]
        media_filas = sum(otros) / len(otros) if otros else 0.0
        es_futuro = (y, m) > (now.year, now.month)
        if es_futuro or n_filas == 0:
            estado = 'sin_datos'
            completo = False
        elif media_filas > 0 and n_filas < 0.6 * media_filas:
            estado = 'incompleto'
            completo = False
        else:
            estado = 'completo'
            completo = True
        desviacion_pct = round((n_filas - media_filas) / media_filas, 4) if media_filas else 0.0
        out.append({
            'mes': mes,
            'n_filas': n_filas,
            'media_filas': round(media_filas, 2),
            'completo': completo,
            'estado': estado,
            'desviacion_pct': desviacion_pct,
        })
    return out


@api.get("/panorama_12m")
async def get_panorama_12m(titular: str = None, period: str = '12m'):
    """Ingresos/Gastos/Ahorro agregados del período pedido. Reutiliza
    sheets.get_monthly_summary (misma función que /api/summary) mes a mes y suma —
    no duplica lógica de cálculo, solo cambia la ventana de meses.
    `period`: '12m' (default, ventana móvil actual, comportamiento de siempre) o
    un año natural ('2024', '2025', '2026'...), en cuyo caso itera sus 12 meses.
    real_income=True: aquí Ingresos = nómina + rentas del trabajo (ingreso real),
    a diferencia de /api/summary que muestra el ingreso 'compensatorio' (sin nómina/Santander)."""
    months = _panorama_months(period)
    total_income = 0.0
    total_expenses = 0.0
    for y, m in months:
        s = sheets.get_monthly_summary(y, m, titular=titular or None, real_income=True)
        income = s.get('__income__', 0.0)
        net = s.get('__total__', 0.0)  # expenses - income, per get_monthly_summary
        total_income += income
        total_expenses += net + income
    ahorro = total_income - total_expenses

    month_keys = {f"{y:04d}-{m:02d}" for y, m in months}
    period_rows = [
        r for r in sheets._get_all_records()
        if r.get('Mes') in month_keys and (not titular or r.get('Titular', '') == titular)
    ]
    ingresos_no_laborales = round(sum_ingresos_no_laborales(period_rows), 2)
    gasto_neto = round(round(total_expenses, 2) - ingresos_no_laborales, 2)

    return {
        'ingresos': round(total_income, 2),
        'gastos': round(total_expenses, 2),
        'ahorro': round(ahorro, 2),
        'tasa_ahorro': round(ahorro / total_income, 4) if total_income else 0.0,
        'ingresos_no_laborales': ingresos_no_laborales,
        'gasto_neto': gasto_neto,
        'periodo': period,
        'meses': _completitud_meses(months, titular or None),
    }


@api.get("/search")
async def search_transactions(q: str = '', titular: str = None):
    if len(q) < 2:
        return {'transactions': []}
    q_up = q.upper()
    rows = sheets._get_all_records()
    results = []
    for r in rows:
        if r.get('Tipo') not in ('expense', 'income', 'alquiler'):
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
            'category': r.get('Categoría') or 'Otros',
            'bank': r.get('Banco', ''),
            'titular': r.get('Titular', ''),
            'mes': r.get('Mes', ''),
        })
    results.sort(key=lambda x: _parse_sheet_date(x.get('date', '')) or datetime.min, reverse=True)
    return {'transactions': results[:150]}


app.include_router(api)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
