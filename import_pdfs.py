#!/usr/bin/env python3
"""One-shot import script: parse PDFs and write to Google Sheets."""
from __future__ import annotations

import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import OpenbankPDFParser, TradeRepublicParser
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient

TITULAR = 'Pablo Cavaller'

PDFS = [
    ('/Users/pablocavallergrau/Downloads/Movimientos de Cuenta (2).pdf', OpenbankPDFParser),
    ('/Users/pablocavallergrau/Downloads/Certificado de saldo y movimientos.pdf', TradeRepublicParser),
]


def main() -> None:
    client = SheetsClient(
        credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'),
    )
    load_custom_rules(client)

    all_saved = 0
    for path, ParserClass in PDFS:
        parser = ParserClass()
        transactions = parser.parse(path)
        apply_type_overrides(transactions)
        categories = classify_batch(transactions)
        for tx, cat in zip(transactions, categories):
            tx.category = cat

        saved = client.write_transactions(transactions, titular=TITULAR)
        print(f"{os.path.basename(path)}: {len(transactions)} parseadas, {len(saved)} guardadas")
        for tx in saved:
            sign = '-' if tx.tx_type == 'expense' else '+'
            print(f"  {tx.fmt_date()}  {sign}{tx.fmt_amount():>10}  [{tx.tx_type:10}]  {tx.category:20}  {tx.description}")
        all_saved += len(saved)

    print(f"\nTotal guardadas: {all_saved}")


if __name__ == '__main__':
    main()
