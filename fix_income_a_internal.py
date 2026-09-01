#!/usr/bin/env python3
"""One-shot: reclasifica de `income` a `internal` las transferencias de María
Ruisánchez que son traspaso entre cuentas propias (no ingreso real), y que por
error entraron como `income`. Esas filas neteaban contra el gasto en el número
"gasto neto tras ingresos no laborales" de Inicio / Anual / Personas / Dashboard
general (`sum_ingresos_no_laborales` en sheets.py no aplica carve-out de banco),
deprimiendo el gasto neto de forma artificial.

Filas objetivo (todas Openbank, Titular María Ruisánchez, Tipo actual `income`):

  1. 13/01/2025 · 3.422,57 € · "TRANSFERENCIA DE Maria Ruisanchez, Enviada desde
     Revolut"  → envío desde su propia cuenta Revolut.
  2. 29/11/2024 ·    69,33 € · "TRANSFERENCIA DE Maria Ruisanchez, Enviada desde
     Revolut"  → mismo patrón, su propia Revolut.
  3. 22/09/2025 ·   100,00 € · "TRANSFERENCIA INMEDIATA DE Mar a Rosalia Ruis
     nchez Gonzalez Barros"  → su nombre completo ("María Rosalía Ruisánchez
     González Barros", con í/á perdidas en la codificación del extracto).
  4. 24/09/2025 · 2.000,00 € · misma contraparte.
  5. 25/09/2025 ·    50,00 € · misma contraparte.

Contraparte propia verificada con la lógica de identidades de `parsers.py`:
  - filas 1-2: `_is_bbva_known_counterparty` casa por "MARIA RUISANCHEZ".
  - filas 3-5: `_is_internal` casa por "MAR A ROSALIA" (ya en OWN_ACCOUNT_KEYWORDS).

NO se tocan (dudosas, anotadas para revisión de Pablo):
  - 29/10/2024 · 2.000 € · "TRANSFERENCIA DE Maria Ruisanchez, Prestamo 400 y
    sistemica": su nombre como emisor, pero el concepto "Prestamo" introduce duda.
  - Transferencias Santander 2026 con su propio nombre (1.000 €, 1.897,06 €,
    1.200 €, 100 €, 300 €): también son traspasos propios, pero PROJECT.md fija
    que el income de Santander se deja como `income` (regla previa); tocarlas
    excede el encargo. `sum_ingresos_no_laborales` sí las cuenta, así que
    deprimen el gasto neto de 2026. Decidir aparte con Pablo.

Match exacto por (Fecha, Descripción, abs(Importe), Banco, Titular). Cada objetivo
debe casar exactamente 1 fila con Tipo `income`; si no, aborta sin escribir nada.

Uso:
  python3 fix_income_a_internal.py            # DRY-RUN
  python3 fix_income_a_internal.py --write    # escribe en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

TITULAR = 'María Ruisánchez'
BANCO = 'Openbank'
TIPO_ANTES = 'income'
TIPO_DESPUES = 'internal'

# (Fecha, Descripción, importe_abs)
TARGETS = [
    ('13/01/2025', 'TRANSFERENCIA DE Maria Ruisanchez, Enviada desde Revolut', 3422.57),
    ('29/11/2024', 'TRANSFERENCIA DE Maria Ruisanchez, Enviada desde Revolut', 69.33),
    ('22/09/2025', 'TRANSFERENCIA INMEDIATA DE Mar a Rosalia Ruis nchez Gonzalez Barros', 100.0),
    ('24/09/2025', 'TRANSFERENCIA INMEDIATA DE Mar a Rosalia Ruis nchez Gonzalez Barros', 2000.0),
    ('25/09/2025', 'TRANSFERENCIA INMEDIATA DE Mar a Rosalia Ruis nchez Gonzalez Barros', 50.0),
]


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

    print('=' * 88)
    print('DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA')
    print('=' * 88)

    to_write: list[tuple[int, str]] = []
    ok = True
    for fecha, desc, imp in TARGETS:
        matches = [
            (i, row) for i, row in enumerate(values[1:], start=2)
            if len(row) > max(c_fecha, c_desc, c_imp, c_banco, c_tit, c_tipo)
            and row[c_fecha] == fecha
            and row[c_desc] == desc
            and abs(_amt(row[c_imp]) - imp) < 0.005
            and row[c_banco] == BANCO
            and row[c_tit] == TITULAR
        ]
        print(f"\n· {fecha}  {imp:>9.2f} €  {desc!r}")
        if len(matches) != 1:
            print(f"  !!! se esperaba 1 fila, hay {len(matches)}, NO se tocará")
            ok = False
            continue
        row_idx, row = matches[0]
        if row[c_tipo] != TIPO_ANTES:
            print(f"  !!! fila {row_idx}: Tipo actual {row[c_tipo]!r}, se esperaba {TIPO_ANTES!r}, NO se tocará")
            ok = False
            continue
        print(f"  fila {row_idx}: Tipo {row[c_tipo]!r} -> {TIPO_DESPUES!r}")
        to_write.append((row_idx, TIPO_DESPUES))

    if not ok:
        print("\n!!! ABORTADO: algún objetivo no casó de forma unívoca. No se ha escrito nada.")
        sys.exit(1)

    if not write:
        print(f"\n(dry-run: {len(to_write)} filas listas. Ejecuta con --write para confirmar.)")
        return

    for row_idx, tipo in to_write:
        client.ws.update_cell(row_idx, c_tipo + 1, tipo)
    client._invalidate_cache()
    print(f"\n>>> ESCRITO: {len(to_write)} filas, columna Tipo -> {TIPO_DESPUES!r}")


if __name__ == '__main__':
    main()
