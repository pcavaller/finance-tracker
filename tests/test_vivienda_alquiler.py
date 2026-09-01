"""Regresión de los cambios 2026-09-01 en la vista Vivienda del Dashboard general:

1. `SheetsClient.get_alquiler_vivienda` — total y desglose mensual de las filas
   Tipo `alquiler` de los últimos N meses (renta de habitaciones, fuera del cash flow).
2. `GET /api/vivienda` — `total_pagado` excluye las filas de Categoría `Hipoteca`
   (servicio de deuda recurrente, ya cuenta en el cash flow, no aportación a la compra)
   y expone `ingresos_alquiler_12m` / `ingresos_alquiler_mensual`.
"""

import asyncio
from datetime import datetime

import webapp
from sheets import SheetsClient


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def test_get_alquiler_vivienda_suma_y_desglose_en_ventana():
    now = datetime.now()
    y_prev, m_prev = _shift(now.year, now.month, -1)     # dentro de la ventana de 12m
    y_old, m_old = _shift(now.year, now.month, -20)       # fuera de la ventana

    records = [
        {'Tipo': 'alquiler', 'Mes': _ym(now.year, now.month), 'Importe': 650},
        {'Tipo': 'alquiler', 'Mes': _ym(y_prev, m_prev), 'Importe': '650'},
        {'Tipo': 'alquiler', 'Mes': _ym(y_old, m_old), 'Importe': 650},   # demasiado antiguo
        {'Tipo': 'income', 'Mes': _ym(now.year, now.month), 'Importe': 9999},
        {'Tipo': 'expense', 'Mes': _ym(now.year, now.month), 'Importe': -9999},
    ]
    client = SheetsClient.__new__(SheetsClient)
    client._get_all_records = lambda: records

    out = client.get_alquiler_vivienda(months=12)
    assert out['total'] == 1300.0
    assert out['mensual'] == {
        _ym(y_prev, m_prev): 650.0,
        _ym(now.year, now.month): 650.0,
    }


class _FakeSheets:
    def get_vivienda_transactions(self):
        return [
            {'date': '31/08/2026', 'description': 'Cargo por amortizacion de prestamo/credito',
             'category': 'Hipoteca', 'amount': -1672.64, 'bank': 'BBVA', 'titular': 'Conjunta'},
            {'date': '31/07/2026', 'description': 'Cargo por intereses de prestamo',
             'category': 'Hipoteca', 'amount': -28.55, 'bank': 'BBVA', 'titular': 'Conjunta'},
            {'date': '30/07/2026', 'description': 'Adeudo de seguros',
             'category': 'Compra vivienda', 'amount': 4921.66, 'bank': 'BBVA', 'titular': 'Conjunta'},
            {'date': '13/07/2026', 'description': 'Adeudo tecnicos en tasacion, s.a.',
             'category': 'Compra vivienda', 'amount': 273.46, 'bank': 'BBVA', 'titular': 'Conjunta'},
            {'date': '30/07/2026', 'description': 'Abono por disposicion de prestamo/credito',
             'category': 'Compra vivienda', 'amount': 425317.96, 'bank': 'BBVA', 'titular': 'Conjunta'},
        ]

    def get_total_financiado(self):
        return 515317.96

    def get_prestamos(self):
        return []

    def get_alquiler_vivienda(self, months=12):
        return {'total': 650.0, 'mensual': {'2026-09': 650.0}}


def test_api_vivienda_excluye_hipoteca_y_expone_alquiler(monkeypatch):
    monkeypatch.setattr(webapp, 'sheets', _FakeSheets())
    data = asyncio.run(webapp.get_vivienda())

    s = data['summary']
    # Solo cuentan tasación (273,46) y seguro de vida (4.921,66). Hipoteca y el abono
    # del préstamo quedan fuera.
    assert s['total_pagado'] == 5195.12
    assert s['seguro_vinculado'] == 4921.66
    assert s['diferencia'] == round(515317.96 - 5195.12, 2)

    buckets = {t['description']: t['bucket'] for t in data['transactions']}
    assert buckets['Cargo por amortizacion de prestamo/credito'] == 'hipoteca'
    assert buckets['Cargo por intereses de prestamo'] == 'hipoteca'
    assert buckets['Abono por disposicion de prestamo/credito'] == 'financiacion_recibida'
    assert buckets['Adeudo de seguros'] == 'seguro'
    assert buckets['Adeudo tecnicos en tasacion, s.a.'] == 'pagado'

    assert data['ingresos_alquiler_12m'] == 650.0
    assert data['ingresos_alquiler_mensual'] == {'2026-09': 650.0}
