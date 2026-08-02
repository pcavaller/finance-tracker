#!/usr/bin/env python3
"""One-shot import: BBVA Conjunta + BBVA Pablo personal (xlsx, compra de
vivienda julio 2026) + Openbank + Trade Republic (PDFs de julio 2026).

Uso:
  python3 import_bbva_agosto2026.py            # importa de verdad a Google Sheets
  python3 import_bbva_agosto2026.py --dry-run  # solo parsea y clasifica, no escribe

Ver PROJECT.md — sección "Reglas operativas" para las reglas de negocio
(transferencias internas, Hipoteca, Compra vivienda, Tipo patrimonio).
"""
from __future__ import annotations

import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import (
    BBVAParser, OpenbankPDFParser, TradeRepublicParser, disambiguate_duplicates,
)
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient

FILES = [
    ('/Users/pablocavallergrau/Downloads/2026Y-08M-01D-13_04_37-Últimos movimientos.xlsx', BBVAParser, 'Conjunta'),
    ('/Users/pablocavallergrau/Downloads/2026Y-08M-01D-13_04_54-Últimos movimientos.xlsx', BBVAParser, 'Pablo Cavaller'),
    ('/Users/pablocavallergrau/Downloads/Movimientos de Cuenta julio.pdf', OpenbankPDFParser, 'Pablo Cavaller'),
    ('/Users/pablocavallergrau/Downloads/Certificado de saldo y movimientos (1).pdf', TradeRepublicParser, 'Pablo Cavaller'),
]


def main() -> None:
    dry_run = '--dry-run' in sys.argv

    client = SheetsClient(
        credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'),
    )
    load_custom_rules(client)

    if not dry_run:
        client.add_titular('Conjunta')
        client.add_titular('Pablo Cavaller')

    grand_total_saved = 0
    grand_total_parsed = 0
    for path, ParserClass, titular in FILES:
        transactions = ParserClass().parse(path)
        apply_type_overrides(transactions)
        disambiguate_duplicates(transactions)

        to_classify = [tx for tx in transactions if not tx.category]
        cats = classify_batch(to_classify)
        for tx, cat in zip(to_classify, cats):
            tx.category = cat

        if dry_run:
            saved = transactions
            print(f"\n[DRY-RUN] {os.path.basename(path)} [{titular}]: {len(transactions)} parseadas")
        else:
            saved = client.write_transactions(transactions, titular=titular)
            dup = len(transactions) - len(saved)
            print(f"\n{os.path.basename(path)} [{titular}]: {len(transactions)} parseadas, "
                  f"{len(saved)} guardadas, {dup} duplicadas")

        for tx in saved:
            sign = '-' if tx.tx_type == 'expense' else '+'
            print(f"  {tx.fmt_date()}  {sign}{tx.fmt_amount():>14}  [{tx.tx_type:10}]  "
                  f"{tx.category:18}  {tx.description}")

        grand_total_saved += len(saved)
        grand_total_parsed += len(transactions)

    label = "Total parseadas (dry-run)" if dry_run else "Total guardadas"
    print(f"\n{label}: {grand_total_saved} de {grand_total_parsed}")


if __name__ == '__main__':
    main()
