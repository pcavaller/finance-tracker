#!/usr/bin/env python3
"""One-shot: reclasifica de `income` a `internal` las transferencias ENTRANTES del
Santander de María Ruisánchez cuya contraparte es una variante de su propio nombre
(traspaso entre cuentas propias, no ingreso real).

Refinamiento de la regla previa "Santander income → income": decidido por Pablo
2026-09-01, toda transferencia de María a sí misma es `internal` SIEMPRE, en
cualquier banco, Santander incluido. Estas filas las contaba
`sum_ingresos_no_laborales` (sin carve-out de banco), deprimiendo el gasto neto de
forma artificial (~4.500 € en 2026).

Descubrimiento en vivo: se recorren todas las filas Banco=='Santander',
Titular=='María Ruisánchez', Tipo=='income' y se filtran con
`parsers._is_maria_own_identity` (mismo helper que usa ahora `SantanderPDFParser`
para los imports futuros). Cualquier candidata cuyo concepto sugiera algo distinto
de un traspaso limpio (Prestamo / matrícula / hipoteca…) se deja fuera y se anota.

Candidatas esperadas (análisis previo, se verifican en el scan):
  · 17/04/2026 · 1.000,00 € · "Transferencia Inmediata De Mar A Rosalia Ruis Nchez Gonzalez Barros,"
  · 26/01/2026 · 1.897,06 € · idem
  · 15/07/2026 · 1.200,00 € · "Transferencia De Marãa Rosalia Ruisã¡nchez Gonzalez Barros, ."
  · 15/07/2026 ·   100,00 € · idem
  · 14/07/2026 ·   300,00 € · "Transferencia De Ruisanchez Gonzalez-barros Maria Rosalia, ."

NO se toca (dudosa, en Openbank no Santander, anotada en PROJECT.md):
  · 29/10/2024 · 2.000 € · "TRANSFERENCIA DE Maria Ruisanchez, Prestamo 400 y
    sistemica" (posible matrícula universidad).

Uso:
  python3 fix_maria_santander_autotraspaso.py            # DRY-RUN
  python3 fix_maria_santander_autotraspaso.py --write     # escribe en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import _is_maria_own_identity
from sheets import SheetsClient

TITULAR = 'María Ruisánchez'
BANCO = 'Santander'
TIPO_ANTES = 'income'
TIPO_DESPUES = 'internal'

# Conceptos que descartan que sea un traspaso propio limpio.
DANGER_WORDS = ('PRESTAMO', 'PRÉSTAMO', 'MATRICULA', 'MATRÍCULA', 'HIPOTECA')

# Sanity: importes que el análisis previo ya identificó (se avisa si falta alguno).
EXPECTED_AMOUNTS = {1000.0, 1897.06, 1200.0, 100.0, 300.0}


def _amt(v) -> float:
    try:
        return abs(float(str(v).replace(',', '.')))
    except ValueError:
        return float('nan')


def main() -> None:
    write = '--write' in sys.argv
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))

    values = client.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
    headers = values[0]
    c_fecha = headers.index('Fecha')
    c_desc = headers.index('Descripción')
    c_imp = headers.index('Importe')
    c_banco = headers.index('Banco')
    c_tit = headers.index('Titular')
    c_tipo = headers.index('Tipo')

    print('=' * 92)
    print('DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA')
    print('=' * 92)

    to_write: list[tuple[int, str, float]] = []
    skipped: list[tuple[str, float, str, str]] = []
    caught_amounts: set[float] = set()

    for i, row in enumerate(values[1:], start=2):
        if len(row) <= max(c_fecha, c_desc, c_imp, c_banco, c_tit, c_tipo):
            continue
        if row[c_banco] != BANCO or row[c_tit] != TITULAR or row[c_tipo] != TIPO_ANTES:
            continue
        desc = row[c_desc]
        if not _is_maria_own_identity(desc):
            continue
        imp = _amt(row[c_imp])
        danger = next((w for w in DANGER_WORDS if w in desc.upper()), None)
        if danger:
            skipped.append((row[c_fecha], imp, desc, f'concepto contiene {danger!r}'))
            continue
        caught_amounts.add(round(imp, 2))
        to_write.append((i, row[c_fecha], imp, desc))

    print(f'\nFILAS QUE SE RECLASIFICAN income -> internal ({len(to_write)}):')
    for i, fecha, imp, desc in to_write:
        print(f'  fila {i:>4} · {fecha} · {imp:>9.2f} € · {desc!r}')

    if skipped:
        print(f'\nDESCARTADAS (no es traspaso limpio, se dejan como income) ({len(skipped)}):')
        for fecha, imp, desc, why in skipped:
            print(f'  {fecha} · {imp:>9.2f} € · {desc!r}  ->  {why}')
    else:
        print('\nDESCARTADAS: ninguna')

    missing = EXPECTED_AMOUNTS - caught_amounts
    extra = caught_amounts - EXPECTED_AMOUNTS
    if missing:
        print(f'\n!!! AVISO: no se han cazado importes esperados: {sorted(missing)}')
    if extra:
        print(f'\n(nota: se cazaron importes NO previstos en el análisis inicial: {sorted(extra)} '
              '— pasan el filtro de traspaso propio limpio, se reclasifican igual)')

    if not to_write:
        print('\nNada que hacer.')
        return

    if not write:
        print(f'\n(dry-run: {len(to_write)} filas listas. Ejecuta con --write para confirmar.)')
        return

    for i, _fecha, _imp, _desc in to_write:
        client.ws.update_cell(i, c_tipo + 1, TIPO_DESPUES)
    client._invalidate_cache()
    print(f'\n>>> ESCRITO: {len(to_write)} filas, columna Tipo -> {TIPO_DESPUES!r}')


if __name__ == '__main__':
    main()
