#!/usr/bin/env python3
"""One-shot: borra la fila DUPLICADA del bonus de DiverInvest de diciembre 2025.

El 24/12/2025 hay DOS filas de 3.749,62 € (Openbank, Pablo Cavaller, Tipo income):

  A) "Nómina DiverInvest Bonus"
       -> renta de trabajo (is_nomina == True), cuenta como ingreso real.
  B) "DiverInvest: TRANSFERENCIA INMEDIATA DE DIVERINVEST ASESORAMIENTO EAF
      S.L.U. Bonus Bon"
       -> is_renta_trabajo == False, así que `sum_ingresos_no_laborales` la
          cuenta como "ingreso recibido no laboral" y la netea contra el gasto,
          deprimiendo el gasto neto ~3.750 € en 2025 y en la ventana 12m.

Mismo día, mismo importe exacto, mismo pagador (DiverInvest), ambas dicen "Bonus":
es el MISMO pago, importado dos veces (una con la etiqueta limpia de nómina, otra
con el texto crudo de la transferencia). El dedupe de `write_transactions` no lo
pilló porque la descripción difiere. La fila A es la buena (ya es renta de
trabajo); se borra la B. No se reclasifica: si se dejara como `internal` seguiría
sin ser cierto (no es un traspaso), y como `income`+renta_trabajo duplicaría el
ingreso real del mes.

Salvaguarda: antes de borrar B se verifica que existe A (misma fecha e importe).
Si no aparece A, o si B no casa exactamente 1 fila, aborta sin tocar nada.

Uso:
  python3 fix_borrar_bonus_duplicado.py            # DRY-RUN
  python3 fix_borrar_bonus_duplicado.py --write    # borra en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

FECHA = '24/12/2025'
IMPORTE = 3749.62
BANCO = 'Openbank'
TITULAR = 'Pablo Cavaller'
DESC_BORRAR = ('DiverInvest: TRANSFERENCIA INMEDIATA DE DIVERINVEST ASESORAMIENTO '
               'EAF S.L.U. Bonus Bon')
DESC_CONSERVAR = 'Nómina DiverInvest Bonus'


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

    print('=' * 88)
    print('DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA')
    print('=' * 88)
    print(f"Fila a CONSERVAR ({DESC_CONSERVAR!r}): {len(conservar)} encontrada(s)")
    for i, row in conservar:
        print(f"  fila {i}: {row[c_fecha]}  {row[c_imp]}  Tipo={row[c_tipo]!r}")
    print(f"Fila a BORRAR ({DESC_BORRAR!r}): {len(borrar)} encontrada(s)")
    for i, row in borrar:
        print(f"  fila {i}: {row[c_fecha]}  {row[c_imp]}  Tipo={row[c_tipo]!r}")

    if len(conservar) < 1:
        print("\n!!! ABORTADO: no aparece la fila 'Nómina DiverInvest Bonus'. "
              "Sin ella no está claro que la otra sea un duplicado. No se toca nada.")
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
