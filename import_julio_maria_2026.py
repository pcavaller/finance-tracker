#!/usr/bin/env python3
"""One-shot import: extractos de julio de María (Openbank + Santander)."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import OpenbankCuentasPDFParser, SantanderPDFParser, disambiguate_duplicates
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient

TITULAR = 'María Ruisánchez'

PDFS = [
    ('/Users/pablocavallergrau/Downloads/transactions_1785743094.307661.pdf', OpenbankCuentasPDFParser),
    ('/Users/pablocavallergrau/Downloads/File.pdf', SantanderPDFParser),
]

# Tramo 2 del préstamo del padre (40.000€, ya registrado como patrimonio en BBVA
# el 16/07/2026): estas 5 entradas + la salida a BBVA Conjunta son el mismo dinero
# de paso por la cuenta Openbank de María. Se marcan internal para no duplicar
# income/expense ni el evento patrimonial, ya contado una vez en BBVA.
_PADRE_TRAMO2_DESC = 'Préstamo padre (tramo 2) — vía Openbank María'


def _fix_padre_tramo(txs: list) -> None:
    for tx in txs:
        if tx.bank != 'Openbank':
            continue
        d = tx.description.upper()
        if 'IGNACIO RUISANCHEZ' in d or 'RUISANCHEZ CAPELASTEGUI' in d:
            tx.tx_type = 'internal'
            tx.category = 'Otros'
            tx.description = _PADRE_TRAMO2_DESC
        elif tx.description == 'TRANSFERENCIA A FAVOR DE BBVA Conjunta' and tx.amount == 40000.0:
            tx.tx_type = 'internal'
            tx.category = 'Otros'
            tx.description = _PADRE_TRAMO2_DESC
        elif tx.description.startswith('MARIA TRA TRANSFERENCIA A FAVOR DE Pablo Jardon'):
            # PDF line-wrap artifact: stray tail ("MARIA TRA") of the preceding
            # padre-loan line's concept bled into this transaction's own line.
            tx.description = tx.description.removeprefix('MARIA TRA ').strip()


def main() -> None:
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    load_custom_rules(client)

    all_txs = []
    for path, ParserClass in PDFS:
        parser = ParserClass()
        txs = parser.parse(path)
        apply_type_overrides(txs)
        all_txs.extend(txs)

    categories = classify_batch(all_txs)
    for tx, cat in zip(all_txs, categories):
        tx.category = cat

    _fix_padre_tramo(all_txs)
    disambiguate_duplicates(all_txs)

    saved = client.write_transactions(all_txs, titular=TITULAR)
    print(f"Parseadas: {len(all_txs)} | Guardadas: {len(saved)} | Descartadas (dup): {len(all_txs) - len(saved)}")
    for tx in sorted(saved, key=lambda t: t.date):
        sign = '-' if tx.tx_type == 'expense' else '+'
        print(f"  {tx.fmt_date()}  {sign}{tx.fmt_amount():>10}  [{tx.tx_type:10}]  {tx.category:20}  {tx.description}")


if __name__ == '__main__':
    main()
