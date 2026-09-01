"""Regresión (2026-09-01): número secundario "gasto neto tras ingresos no
laborales" junto al gasto bruto en /api/summary y /api/annual.

Conjunto que netea = filas Tipo == 'income' que NO son renta de trabajo
(is_renta_trabajo == False). Sin carve-out de Santander. 'alquiler'/'fianza'/
'patrimonio'/'internal'/'investment' quedan fuera por no ser Tipo == 'income'.

- `gasto_neto` = `total_expenses` (bruto) − `ingresos_no_laborales`.
- El bruto (`total_expenses` en summary, `total` en annual) NUNCA cambia y nunca
  se va a negativo; `gasto_neto`, como campo secundario, SÍ puede ser negativo
  cuando los ingresos no laborales superan al gasto bruto.
"""

import asyncio

import webapp


class _FakeSummarySheets:
    def __init__(self, records, summary):
        self._records = records
        self._summary = summary

    def get_monthly_summary(self, year, month, titular=None, real_income=False):
        return dict(self._summary)

    def _get_all_records(self):
        return self._records


def test_api_summary_expone_ingresos_no_laborales_y_gasto_neto_negativo(monkeypatch):
    mes = '2099-01'
    records = [
        {'Mes': mes, 'Tipo': 'expense', 'Descripción': 'Compra', 'Importe': -100,
         'Categoría': 'Alimentación', 'Banco': 'BBVA', 'Titular': ''},
        {'Mes': mes, 'Tipo': 'expense', 'Descripción': 'Cena', 'Importe': -50,
         'Categoría': 'Restaurantes', 'Banco': 'BBVA', 'Titular': ''},
        {'Mes': mes, 'Tipo': 'income', 'Descripción': 'Bizum de Fulano', 'Importe': 30,
         'Banco': 'BBVA', 'Titular': ''},
        {'Mes': mes, 'Tipo': 'income', 'Descripción': 'Transferencia de la abuela',
         'Importe': 200, 'Banco': 'BBVA', 'Titular': ''},
        # renta de trabajo -> NO cuenta como ingreso no laboral
        {'Mes': mes, 'Tipo': 'income', 'Descripción': 'TRANSFERENCIA NOMINA ENERO',
         'Importe': 2000, 'Banco': 'BBVA', 'Titular': ''},
        # Santander SÍ cuenta aquí (sin carve-out)
        {'Mes': mes, 'Tipo': 'income', 'Descripción': 'Bizum via Santander', 'Importe': 40,
         'Banco': 'Santander', 'Titular': ''},
        # alquiler -> fuera (no es Tipo income)
        {'Mes': mes, 'Tipo': 'alquiler', 'Descripción': 'Alquiler habitación 2',
         'Importe': 650, 'Banco': 'Efectivo', 'Titular': ''},
    ]
    summary = {'Alimentación': 100.0, 'Restaurantes': 50.0, '__income__': 230.0, '__total__': -80.0}
    monkeypatch.setattr(webapp, 'sheets', _FakeSummarySheets(records, summary))

    data = asyncio.run(webapp.get_summary(year=2099, month=1, titular=None))

    assert data['total_expenses'] == 150.0          # bruto intacto
    assert data['total_expenses'] > 0               # el bruto nunca se va a negativo
    assert data['ingresos_no_laborales'] == 270.0   # 30 + 200 + 40 (nómina y alquiler fuera)
    assert data['gasto_neto'] == -120.0             # 150 - 270, negativo aceptable para el secundario

    # La lista que acompaña a "Ingresos recibidos (no laborales)" usa EXACTAMENTE
    # el mismo conjunto que la cifra: income ∧ ¬renta_trabajo, SIN carve-out de
    # Santander -> Σ(lista) == ingresos_no_laborales, y el Bizum vía Santander entra.
    items = data['ingresos_no_laborales_items']
    assert [i['amount'] for i in items] == [200.0, 40.0, 30.0]   # ordenada desc
    assert any('Santander' in i['description'] for i in items)
    assert round(sum(i['amount'] for i in items), 2) == data['ingresos_no_laborales']


def test_api_summary_gasto_neto_positivo_normal(monkeypatch):
    mes = '2099-02'
    records = [
        {'Mes': mes, 'Tipo': 'expense', 'Descripción': 'x', 'Importe': -500,
         'Categoría': 'Otros', 'Banco': 'BBVA', 'Titular': ''},
        {'Mes': mes, 'Tipo': 'income', 'Descripción': 'Bizum de Fulano', 'Importe': 120,
         'Banco': 'BBVA', 'Titular': ''},
    ]
    summary = {'Otros': 500.0, '__income__': 120.0, '__total__': 380.0}
    monkeypatch.setattr(webapp, 'sheets', _FakeSummarySheets(records, summary))

    data = asyncio.run(webapp.get_summary(year=2099, month=2, titular=None))
    assert data['total_expenses'] == 500.0
    assert data['ingresos_no_laborales'] == 120.0
    assert data['gasto_neto'] == 380.0


class _FakeAnnualSheets:
    def __init__(self, records):
        self._records = records

    def _get_all_records(self):
        return self._records


def test_api_annual_ingresos_no_laborales_por_mes_y_anio(monkeypatch):
    records = [
        # 2099-01: gasto 300, ing. no laboral 100  -> neto 200
        {'Mes': '2099-01', 'Tipo': 'expense', 'Descripción': 'a', 'Importe': -300,
         'Categoría': 'Otros', 'Titular': ''},
        {'Mes': '2099-01', 'Tipo': 'income', 'Descripción': 'Bizum de Fulano', 'Importe': 100,
         'Titular': ''},
        # 2099-02: gasto 200, ing. no laboral 500  -> neto -300 (ingresos > gasto bruto)
        {'Mes': '2099-02', 'Tipo': 'expense', 'Descripción': 'b', 'Importe': -200,
         'Categoría': 'Otros', 'Titular': ''},
        {'Mes': '2099-02', 'Tipo': 'income', 'Descripción': 'Transferencia familia', 'Importe': 500,
         'Titular': ''},
        # renta de trabajo -> excluida del set
        {'Mes': '2099-02', 'Tipo': 'income', 'Descripción': 'Transferencia De Stripe, Buencoco',
         'Importe': 999, 'Titular': ''},
        # alquiler -> fuera
        {'Mes': '2099-02', 'Tipo': 'alquiler', 'Descripción': 'Alquiler habitación 1', 'Importe': 650,
         'Titular': ''},
        # 2099-03: gasto 400, sin ingresos
        {'Mes': '2099-03', 'Tipo': 'expense', 'Descripción': 'c', 'Importe': -400,
         'Categoría': 'Otros', 'Titular': ''},
    ]
    monkeypatch.setattr(webapp, 'sheets', _FakeAnnualSheets(records))

    data = asyncio.run(webapp.get_annual(year=2099, titular=None))

    assert data['total'] == 900.0            # bruto del año, intacto
    assert data['total'] > 0
    assert data['ingresos_no_laborales'] == 600.0
    assert data['gasto_neto'] == 300.0       # 900 - 600

    by_month = {m['month']: m for m in data['months']}
    assert by_month['2099-01']['total'] == 300.0
    assert by_month['2099-01']['ingresos_no_laborales'] == 100.0
    assert by_month['2099-01']['gasto_neto'] == 200.0

    assert by_month['2099-02']['total'] == 200.0
    assert by_month['2099-02']['ingresos_no_laborales'] == 500.0
    assert by_month['2099-02']['gasto_neto'] == -300.0   # negativo aceptable en el secundario
    assert by_month['2099-02']['total'] > 0              # el bruto del mes nunca negativo

    assert by_month['2099-03']['ingresos_no_laborales'] == 0.0
    assert by_month['2099-03']['gasto_neto'] == 400.0
