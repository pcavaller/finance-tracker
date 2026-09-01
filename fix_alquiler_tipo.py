#!/usr/bin/env python3
"""One-shot: cambia el Tipo de la renta de habitación de `income` a `alquiler`.

Fila objetivo (única):
  01/09/2026 · "Alquiler habitación 2" · 650 · Efectivo · Conjunta · Tipo income

`alquiler` es un Tipo propio paralelo a `fianza`/`patrimonio`/`internal`: la fila se
registra pero queda FUERA de `SUM income` y `SUM expense` en get_monthly_summary y en
toda agregación de cash flow. Así deja de contar como ingreso del hogar en Inicio, en
el anual y en el panorama de ingresos; solo se ve aparte en la sección Vivienda vía
SheetsClient.get_alquiler_vivienda / GET /api/vivienda.

Match exacto por (Descripción, Banco) + verificación de que hay exactamente 1 fila y
de que su Tipo actual es `income`. Solo se toca esa celda de la columna Tipo.

Uso:
  python3 fix_alquiler_tipo.py            # DRY-RUN
  python3 fix_alquiler_tipo.py --write    # escribe en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

DESC = 'Alquiler habitación 2'
BANCO = 'Efectivo'
TIPO_ANTES = 'income'
TIPO_DESPUES = 'alquiler'


def main() -> None:
    write = '--write' in sys.argv
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))

    values = client.ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
    headers = values[0]
    desc_col = headers.index('Descripción')
    banco_col = headers.index('Banco')
    tipo_col = headers.index('Tipo')

    matches = [
        (i, row) for i, row in enumerate(values[1:], start=2)
        if len(row) > max(desc_col, banco_col, tipo_col)
        and row[desc_col] == DESC and row[banco_col] == BANCO
    ]

    print('=' * 88)
    print(f"{'DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA'}")
    print('=' * 88)
    print(f"Filas que casan (Descripción={DESC!r}, Banco={BANCO!r}): {len(matches)}")
    for i, row in matches:
        print(f"  fila {i}: Fecha={row[headers.index('Fecha')]!r}  Importe={row[headers.index('Importe')]!r}  "
              f"Categoría={row[headers.index('Categoría')]!r}  Titular={row[headers.index('Titular')]!r}  "
              f"Tipo={row[tipo_col]!r}  ->  Tipo={TIPO_DESPUES!r}")

    if len(matches) != 1:
        print(f"\n!!! ABORTADO: se esperaba exactamente 1 fila, hay {len(matches)}.")
        sys.exit(1)
    row_idx, row = matches[0]
    if row[tipo_col] != TIPO_ANTES:
        print(f"\n!!! ABORTADO: Tipo actual es {row[tipo_col]!r}, se esperaba {TIPO_ANTES!r}.")
        sys.exit(1)

    if not write:
        print("\n(dry-run: nada escrito. Ejecuta con --write para confirmar.)")
        return

    client.ws.update_cell(row_idx, tipo_col + 1, TIPO_DESPUES)
    client._invalidate_cache()
    print(f"\n>>> ESCRITO: fila {row_idx}, columna Tipo -> {TIPO_DESPUES!r}")


if __name__ == '__main__':
    main()
