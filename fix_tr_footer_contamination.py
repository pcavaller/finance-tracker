#!/usr/bin/env python3
"""One-shot cleanup: 20 filas históricas de Trade Republic contaminadas por el
bug de pie de página (filtro top<h-60 dejaba pasar la mitad del bloque de
datos de la empresa, que se colaba como "continuación" en la última
transacción de cada página). Corregido en parsers.py (margen ahora h-90).

Para las 9 filas con fecha <= 12 mar 2026 se recupera descripción E importe
reales re-parseando el PDF fuente (Extractos/Extracto de cuenta (2).pdf) con
el parser ya corregido. Para las 11 restantes el importe ya era correcto en
la Sheet (solo la descripción estaba contaminada) y basta con recortar el
texto a partir del primer marcador de pie de página conocido.
"""
from __future__ import annotations

import os
import re
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import TradeRepublicParser
from sheets import SheetsClient

FIXTURE_PDF = os.path.join(os.path.dirname(__file__), 'Extractos', 'Extracto de cuenta (2).pdf')

# (fila, fecha, importe_correcto) — importe recuperado del PDF fuente, se
# empareja por fecha exacta + fragmento estable de la descripción original
# para evitar coger la transacción equivocada cuando hay varias el mismo día.
FIXTURE_ROWS = {
    8: ('10/03/2025', 'quantity: 1'),
    22: ('12/06/2025', 'Sell trade DE000SX7PG56'),
    41: ('17/10/2025', 'DE000FA0DB99'),
    98: ('10/02/2026', 'CAVALLER GRAU PABLO'),
    128: ('02/03/2026', 'quantity: 10.879025'),
    1119: ('02/02/2026', 'quantity: 1.825242'),
    1127: ('26/02/2026', 'CA44888L1085'),
    1138: ('03/03/2026', 'DE000SJ66RZ0'),
    1149: ('09/03/2026', 'CAVALLER GRAU PABLO'),
}

# Filas donde el importe de la Sheet ya era correcto: solo recorte de texto.
DESC_ONLY_ROWS = [1208, 2285, 2293, 2387, 2404, 2420, 2437, 2530, 2547, 2612, 2629]

_FOOTER_MARKERS = ('Sucursal en España', 'www.traderepublic.es')


def _strip_footer(desc: str) -> str:
    cut = len(desc)
    for marker in _FOOTER_MARKERS:
        i = desc.find(marker)
        if i != -1:
            cut = min(cut, i)
    return desc[:cut].strip()


def main() -> None:
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    ws = client._spreadsheet.worksheet('Transacciones')

    fixture_txs = TradeRepublicParser().parse(FIXTURE_PDF)

    rows = ws.get_all_values()
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    COL_DESC = idx['Descripción'] + 1
    COL_IMPORTE = idx['Importe'] + 1

    updates = []

    for row_num, (fecha, needle) in FIXTURE_ROWS.items():
        matches = [t for t in fixture_txs if t.fmt_date() == fecha and needle in t.description]
        if len(matches) != 1:
            raise RuntimeError(f"fila {row_num}: {len(matches)} matches para fecha={fecha} needle={needle!r}")
        tx = matches[0]
        old_desc = rows[row_num - 1][idx['Descripción']]
        old_importe = rows[row_num - 1][idx['Importe']]
        print(f"fila {row_num}: {old_desc[:60]!r} [{old_importe}] -> {tx.description!r} [{tx.amount}]")
        updates.append((row_num, COL_DESC, tx.description))
        updates.append((row_num, COL_IMPORTE, tx.amount))

    for row_num in DESC_ONLY_ROWS:
        old_desc = rows[row_num - 1][idx['Descripción']]
        new_desc = _strip_footer(old_desc)
        print(f"fila {row_num}: {old_desc[:60]!r} -> {new_desc!r}")
        updates.append((row_num, COL_DESC, new_desc))

    for row_num, col, value in updates:
        ws.update_cell(row_num, col, value)

    print(f"\n{len(updates)} celdas actualizadas en {len(FIXTURE_ROWS) + len(DESC_ONLY_ROWS)} filas.")


if __name__ == '__main__':
    main()
