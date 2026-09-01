"""Regresión BUG-9 (2026-09-01): /api/search debe ordenar por fecha real
(parseando DD/MM/YYYY o el serial de Sheets), no por concatenación de
strings 'mes+fecha' — eso rompía el orden cronológico dentro de un mismo mes
y con fechas que Sheets había autoconvertido a número de serie."""

import asyncio

import webapp


class _FakeSheets:
    def __init__(self, records):
        self._records = records

    def _get_all_records(self):
        return self._records


def test_search_ordena_cronologicamente_descendente(monkeypatch):
    records = [
        {'Tipo': 'expense', 'Descripción': 'Mercadona 5', 'Importe': -10, 'Categoría': 'Alimentación', 'Banco': 'BBVA', 'Titular': '', 'Mes': '2026-03', 'Fecha': '05/03/2026'},
        {'Tipo': 'expense', 'Descripción': 'Mercadona 27', 'Importe': -20, 'Categoría': 'Alimentación', 'Banco': 'BBVA', 'Titular': '', 'Mes': '2026-03', 'Fecha': '27/03/2026'},
        {'Tipo': 'expense', 'Descripción': 'Mercadona serial', 'Importe': -30, 'Categoría': 'Alimentación', 'Banco': 'BBVA', 'Titular': '', 'Mes': '2026-02', 'Fecha': 46080},  # 27/02/2026 como serial
    ]
    monkeypatch.setattr(webapp, 'sheets', _FakeSheets(records))
    data = asyncio.run(webapp.search_transactions(q='mercadona', titular=None))
    dates = [t['description'] for t in data['transactions']]
    assert dates == ['Mercadona 27', 'Mercadona 5', 'Mercadona serial']
