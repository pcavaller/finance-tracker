#!/usr/bin/env python3
"""One-shot cleanup: 26 filas de Buy/Sell trade en Trade Republic con Importe=0.

Bug distinto al del pie de página (ya arreglado): en el import histórico
original (antes de la reescritura de columnas TR por posición, commit
b0ef754) el importe de la operación se perdía y su texto quedaba suelto
dentro de la descripción en vez de llenar la columna Importe. El parser
actual ya lo parsea bien — 23 de las 26 filas caen dentro del rango del PDF
fuente conservado en Extractos/ (01 dic 2024 - 11 mar 2026) y se recuperan
re-parseándolo. Las 3 restantes (16/03, 18/03, 31/03/2026) quedan fuera de
ese rango: no hay PDF fuente disponible, se dejan sin tocar y se listan al
final para que Pablo decida si merece la pena re-descargar ese extracto.
"""
from __future__ import annotations

import os
import re
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import TradeRepublicParser
from sheets import SheetsClient

FIXTURE_PDF = os.path.join(os.path.dirname(__file__), 'Extractos', 'Extracto de cuenta (2).pdf')

_ISIN_RE = re.compile(r'\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b')
_QTY_RE = re.compile(r'quantity:\s*([\d.]+)')
_VERB_RE = re.compile(r'^(Buy trade|Sell trade)\b')

ROWS = [10, 11, 15, 16, 17, 20, 38, 42, 43, 116, 124, 125, 130, 145, 146, 151,
        1131, 1134, 1135, 1139, 1140, 1145, 1146, 1154, 1156, 1172]


def _extract(desc: str) -> tuple[str, str, str] | None:
    verb = _VERB_RE.match(desc)
    isin = _ISIN_RE.search(desc)
    qty = _QTY_RE.search(desc)
    if not (verb and isin and qty):
        return None
    return verb.group(1), isin.group(1), qty.group(1)


def main() -> None:
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    ws = client._spreadsheet.worksheet('Transacciones')

    fixture_txs = TradeRepublicParser().parse(FIXTURE_PDF)
    fixture_max_date = max(t.date for t in fixture_txs)

    rows = ws.get_all_values()
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    COL_DESC = idx['Descripción'] + 1
    COL_IMPORTE = idx['Importe'] + 1

    updates = []
    unresolved = []

    for row_num in ROWS:
        old_desc = rows[row_num - 1][idx['Descripción']]
        fecha = rows[row_num - 1][idx['Fecha']]
        key = _extract(old_desc)
        if key is None:
            unresolved.append((row_num, fecha, old_desc, 'no se pudo extraer ISIN/verbo/quantity'))
            continue
        verb, isin, qty = key

        candidates = [
            t for t in fixture_txs
            if t.fmt_date() == fecha and t.description.startswith(verb)
            and isin in t.description and f'quantity: {qty}' in t.description
        ]
        if len(candidates) != 1:
            unresolved.append((row_num, fecha, old_desc, f'{len(candidates)} matches en el PDF fuente'))
            continue

        tx = candidates[0]
        print(f"fila {row_num}: {old_desc[:70]!r} [0] -> {tx.description!r} [{tx.amount}]")
        updates.append((row_num, COL_DESC, tx.description))
        updates.append((row_num, COL_IMPORTE, tx.amount))

    for row_num, col, value in updates:
        ws.update_cell(row_num, col, value)

    print(f"\n{len(updates)} celdas actualizadas en {len(updates)//2} filas.")

    if unresolved:
        print(f"\nSin resolver ({len(unresolved)} filas) — fuera del rango del PDF fuente "
              f"(hasta {fixture_max_date.strftime('%d/%m/%Y')}) o sin match único:")
        for row_num, fecha, desc, reason in unresolved:
            print(f"  fila {row_num} ({fecha}): {reason} — {desc[:80]!r}")


if __name__ == '__main__':
    main()
