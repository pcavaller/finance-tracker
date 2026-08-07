#!/usr/bin/env python3
"""One-shot import: extracto Trade Republic de María, 01 jul - 06 ago 2026."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import TradeRepublicParser, disambiguate_duplicates
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient

TITULAR = 'María Ruisánchez'

PDF_PATH = '/Users/pablocavallergrau/Downloads/Extracto de cuenta (10).pdf'


def main() -> None:
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    load_custom_rules(client)

    parser = TradeRepublicParser()
    txs = parser.parse(PDF_PATH)
    apply_type_overrides(txs)

    categories = classify_batch(txs)
    for tx, cat in zip(txs, categories):
        tx.category = cat

    disambiguate_duplicates(txs)

    saved = client.write_transactions(txs, titular=TITULAR)
    print(f"Parseadas: {len(txs)} | Guardadas: {len(saved)} | Descartadas (dup): {len(txs) - len(saved)}")
    for tx in sorted(saved, key=lambda t: t.date):
        sign = '-' if tx.tx_type == 'expense' else '+'
        print(f"  {tx.fmt_date()}  {sign}{tx.fmt_amount():>10}  [{tx.tx_type:10}]  {tx.category:20}  {tx.description}")


if __name__ == '__main__':
    main()
