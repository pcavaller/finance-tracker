#!/usr/bin/env python3
"""One-shot: borra la fila DUPLICADA de la nómina de abril 2025 de DiverInvest.

El 28/04/2025 hay DOS filas de 1.803,28 € (Openbank, Pablo Cavaller, Tipo income):

  A) "Nómina DiverInvest"
       -> renta de trabajo (is_nomina == True), ingreso real del mes.
  B) "DiverInvest: TRANSFERENCIA DE DIVERINVEST ANGLI, 58, REFERENCIA: 0073 0100
      696 0ATGL38"
       -> is_renta_trabajo == False, así que `sum_ingresos_no_laborales` la cuenta
          como "ingreso recibido no laboral" y la netea contra el gasto,
          deprimiendo el gasto neto de 2025 ~1.803 €.

1.803,28 € era el neto mensual de la nómina de Pablo de enero a julio de 2025.
Mismo día e importe exacto que la "Nómina DiverInvest" de abril, y sin concepto de
reembolso (el resto de "DiverInvest: TRANSFERENCIA" siempre llevan taxi, ticket o
billete). Pablo confirma (2026-09-01) que es un duplicado de su nómina de abril.
Se borra la fila B y se conserva la A.

Salvaguarda: antes de borrar B se verifica que existe A (misma fecha/importe/banco/
titular). Si no aparece A, o si B no casa exactamente 1 fila, aborta sin tocar nada.

Uso:
  python3 fix_borrar_diverinvest_angli.py            # DRY-RUN
  python3 fix_borrar_diverinvest_angli.py --write     # borra en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

FECHA = '28/04/2025'
IMPORTE = 1803.28
BANCO = 'Openbank'
TITULAR = 'Pablo Cavaller'
DESC_BORRAR = ('DiverInvest: TRANSFERENCIA DE DIVERINVEST ANGLI, 58, REFERENCIA: '
               '0073 0100 696 0ATGL38')
DESC_CONSERVAR = 'Nómina DiverInvest'


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

    def rows_with_desc(desc: str):
        return [
            (i, row) for i, row in enumerate(values[1:], start=2)
            if len(row) > max(c_fecha, c_desc, c_imp, c_banco, c_tit, c_tipo)
            and row[c_fecha] == FECHA
            and row[c_desc] == desc
            and abs(_amt(row[c_imp]) - IMPORTE) < 0.005
            and row[c_banco] == BANCO
            and row[c_tit] == TITULAR
        ]

    borrar = rows_with_desc(DESC_BORRAR)
    conservar = rows_with_desc(DESC_CONSERVAR)

    print('=' * 92)
    print('DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA')
    print('=' * 92)
    print(f"Fila a CONSERVAR ({DESC_CONSERVAR!r}): {len(conservar)} encontrada(s)")
    for i, row in conservar:
        print(f"  fila {i}: {row[c_fecha]}  {row[c_imp]}  Tipo={row[c_tipo]!r}")
    print(f"Fila a BORRAR ({DESC_BORRAR!r}): {len(borrar)} encontrada(s)")
    for i, row in borrar:
        print(f"  fila {i}: {row[c_fecha]}  {row[c_imp]}  Tipo={row[c_tipo]!r}")

    if len(conservar) < 1:
        print("\n!!! ABORTADO: no aparece la 'Nómina DiverInvest' hermana. Sin ella "
              "no está claro que la otra sea un duplicado. No se toca nada.")
        sys.exit(1)
    if len(borrar) != 1:
        print(f"\n!!! ABORTADO: se esperaba exactamente 1 fila a borrar, hay {len(borrar)}.")
        sys.exit(1)

    row_idx = borrar[0][0]
    if not write:
        print(f"\n(dry-run: se borraría la fila {row_idx}. Ejecuta con --write para confirmar.)")
        return

    client.ws.delete_rows(row_idx)
    client._invalidate_cache()
    print(f"\n>>> BORRADA la fila {row_idx}.")


if __name__ == '__main__':
    main()
