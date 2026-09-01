"""Regresión del selector de período en /api/panorama_12m (2026-09-01):

1. `?period=2025` itera los 12 meses de ese año natural con
   sheets.get_monthly_summary (real_income=True) y suma igual que la ventana
   móvil de 12m — cero lógica de cálculo nueva.
2. `_completitud_meses` marca cada mes como 'completo' / 'incompleto'
   (caída > 40% de filas frente a la media de los demás meses del set) /
   'sin_datos' (mes futuro o con 0 filas).
"""

import asyncio
from datetime import datetime

import webapp


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


class _FakeSheetsPanorama:
    """Cada mes real_income deja income=1000, expenses=800 (net=-200)."""

    def get_monthly_summary(self, year, month, titular=None, real_income=False):
        return {'__income__': 1000.0, '__total__': -200.0}

    def _get_all_records(self):
        return []


def test_panorama_periodo_2025_itera_los_12_meses_del_anio(monkeypatch):
    monkeypatch.setattr(webapp, 'sheets', _FakeSheetsPanorama())
    data = asyncio.run(webapp.get_panorama_12m(titular=None, period='2025'))

    assert data['periodo'] == '2025'
    assert data['ingresos'] == 12000.0
    assert data['gastos'] == 9600.0
    assert data['ahorro'] == 2400.0
    assert data['tasa_ahorro'] == round(2400.0 / 12000.0, 4)
    assert len(data['meses']) == 12
    assert all(m['mes'].startswith('2025') for m in data['meses'])


def test_panorama_periodo_default_es_12m(monkeypatch):
    monkeypatch.setattr(webapp, 'sheets', _FakeSheetsPanorama())
    data = asyncio.run(webapp.get_panorama_12m(titular=None))
    assert data['periodo'] == '12m'
    assert len(data['meses']) == 12


def test_completitud_meses_marca_incompleto_y_sin_datos(monkeypatch):
    now = datetime.now()
    # 10 meses "normales" (10 filas cada uno, meses -1..-10), 1 mes con caida
    # fuerte de filas (-11, 2 filas) y 1 mes futuro (+1, 0 filas).
    normales = [_shift(now.year, now.month, -d) for d in range(1, 11)]
    mes_incompleto = _shift(now.year, now.month, -11)
    mes_futuro = _shift(now.year, now.month, 1)
    months = normales + [mes_incompleto, mes_futuro]

    records = []
    for (yy, mm) in normales:
        mes = f"{yy:04d}-{mm:02d}"
        records += [{'Mes': mes, 'Tipo': 'expense', 'Titular': ''} for _ in range(10)]
    mes_inc_key = f"{mes_incompleto[0]:04d}-{mes_incompleto[1]:02d}"
    records += [{'Mes': mes_inc_key, 'Tipo': 'income', 'Titular': ''} for _ in range(2)]
    # el mes futuro no tiene filas en absoluto.

    class _Fake:
        def _get_all_records(self):
            return records

    monkeypatch.setattr(webapp, 'sheets', _Fake())
    out = webapp._completitud_meses(months, titular=None)
    by_mes = {o['mes']: o for o in out}

    futuro_key = f"{mes_futuro[0]:04d}-{mes_futuro[1]:02d}"

    assert by_mes[mes_inc_key]['n_filas'] == 2
    assert by_mes[mes_inc_key]['estado'] == 'incompleto'
    assert by_mes[mes_inc_key]['completo'] is False

    assert by_mes[futuro_key]['n_filas'] == 0
    assert by_mes[futuro_key]['estado'] == 'sin_datos'
    assert by_mes[futuro_key]['completo'] is False

    # Un mes normal (10 filas, media similar entre el resto) debe salir completo.
    normal_key = f"{normales[0][0]:04d}-{normales[0][1]:02d}"
    assert by_mes[normal_key]['n_filas'] == 10
    assert by_mes[normal_key]['estado'] == 'completo'
    assert by_mes[normal_key]['completo'] is True
