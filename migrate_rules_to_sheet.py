#!/usr/bin/env python3
"""Migra a la hoja "Reglas" de Google Sheets las reglas de exclusión y de
tx_type override que antes vivían hardcodeadas en classifier.py
(TYPE_OVERRIDE_RULES) y parsers.py (exclusión de Psicoterapia Ayuso).

Ejecutar una sola vez tras desplegar el refactor de reglas centralizadas.
Es idempotente: si una keyword ya existe en la hoja, no la duplica.

Uso: python3 migrate_rules_to_sheet.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

from sheets import SheetsClient

OVERRIDE_RULES: list[tuple[str, str]] = [
    ('JOSE MARIA SAMARANCH GALLART', 'internal'),
    ('JOSÉ MARÍA SAMARANCH GALLART', 'internal'),
    ('CASH REWARD ALLOCATION', 'internal'),
    ('INTEREST PAYMENT', 'internal'),
    ('BARROS MARIA ROSALIA CONCEPTO', 'internal'),
    ('RUISANCHEZ GONZALEZ-BARROS MARIA ROSALIA', 'internal'),
    ('RUISANCHEZ GONZALEZ BARROS MARIA ROSALIA', 'internal'),
    ('OUTGOING TRANSFER FOR MARIA ROSALIA RUIS', 'internal'),
    ('MIRIAM B M', 'internal'),
    ('BLANCA D P', 'internal'),
    ('ANA D R', 'internal'),
]

EXCLUDE_RULES: list[str] = [
    'PSICOTERAPIA AYUSO',
    'PSICOLOGIA Y PSICOTERAPIA',
]


def main() -> None:
    sheets = SheetsClient()
    existing = {r['keyword'] for r in sheets.get_rules()}

    added = 0
    for keyword, tx_type in OVERRIDE_RULES:
        if keyword in existing:
            continue
        sheets.ws_reglas.append_row([keyword, '', tx_type])
        added += 1

    for keyword in EXCLUDE_RULES:
        if keyword in existing:
            continue
        sheets.ws_reglas.append_row([keyword, '', 'exclude'])
        added += 1

    print(f"{added} reglas migradas a la hoja Reglas.")


if __name__ == '__main__':
    main()
