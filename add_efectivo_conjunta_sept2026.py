#!/usr/bin/env python3
"""One-shot: 2 movimientos en EFECTIVO de la cuenta Conjunta (Galileu 342).

  1. 13/08/2026  Fianza inquilino habitación (efectivo)   300,00   Tipo 'fianza'
  2. 01/09/2026  Alquiler habitación 2                     650,00   Tipo 'income'

Ambos titular 'Conjunta', Banco 'Efectivo' (convención ya existente en la hoja:
7 filas con Banco='Efectivo', p.ej. 'Gafas Pablo', 'Anillo regalo María').

La fila 1 es una fianza en efectivo: ni income ni expense, no toca cash flow
(webapp.py / sheets.py ya excluyen implícitamente cualquier Tipo != expense/income).
La fila 2 es renta de alquiler de septiembre: cuenta como ingreso NO salarial del
hogar (is_renta_trabajo debe dar False), Mes 2026-09, no afecta agosto.

Se escribe por el mismo camino que import_pablo_agosto2026.py:
SheetsClient.write_transactions, que aplica el dedupe por
(fecha, descripción, importe, banco). Segunda pasada = 0 filas escritas.
Columna Vivienda: no se toca (queda vacía).

Uso:
  python3 add_efectivo_conjunta_sept2026.py            # DRY-RUN
  python3 add_efectivo_conjunta_sept2026.py --write     # escribe en Google Sheets
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import Transaction
from sheets import SheetsClient, is_renta_trabajo, is_nomina

TITULAR = 'Conjunta'
BANCO_EFECTIVO = 'Efectivo'  # convención ya presente en la hoja

# (fecha, descripción, importe_abs, categoría, tipo, mes_esperado)
ROWS = [
    ('13/08/2026', 'Fianza inquilino habitación (efectivo)', 300.00, 'Fianza inquilinos', 'fianza', '2026-08'),
    ('01/09/2026', 'Alquiler habitación 2',                  650.00, 'Alquiler habitaciones', 'income', '2026-09'),
]


def _signed(tx: Transaction) -> float:
    """Importe con el signo con que se ESCRIBE en la hoja (solo 'expense' negativo)."""
    return -tx.amount if tx.tx_type == 'expense' else tx.amount


def _build() -> list[Transaction]:
    out = []
    for fecha, desc, amount, cat, tipo, mes_exp in ROWS:
        d = datetime.strptime(fecha, '%d/%m/%Y')
        assert d.strftime('%Y-%m') == mes_exp, f"Mes esperado {mes_exp} != {d:%Y-%m} para {desc!r}"
        out.append(Transaction(
            date=d,
            description=desc,
            amount=abs(amount),
            tx_type=tipo,
            bank=BANCO_EFECTIVO,
            category=cat,
        ))
    return out


def main() -> None:
    write = '--write' in sys.argv

    # ── Guardarraíl: la renta de alquiler NO es renta de trabajo ──────────────
    desc_alquiler = 'Alquiler habitación 2'
    rt, nm = is_renta_trabajo(desc_alquiler), is_nomina(desc_alquiler)
    print(f"is_renta_trabajo({desc_alquiler!r}) = {rt}")
    print(f"is_nomina({desc_alquiler!r})        = {nm}")
    if rt or nm:
        print("\n!!! ABORTADO: la renta de alquiler no debe clasificar como renta de trabajo.")
        sys.exit(1)

    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))

    # ── Convención de efectivo detectada ────────────────────────────────────
    recs = client._get_all_records()
    efectivo_rows = [r for r in recs if r.get('Banco', '') == BANCO_EFECTIVO]
    print(f"\nConvención de efectivo: Banco={BANCO_EFECTIVO!r} ya existe en la hoja "
          f"({len(efectivo_rows)} filas previas).")

    txs = _build()

    # ── Dedupe simulado contra la hoja en vivo ──────────────────────────────
    existing = client._existing_keys()
    to_write, skipped = [], []
    for tx in txs:
        key = (tx.fmt_date(), tx.description, client._amount_key(_signed(tx)), tx.bank)
        (skipped if key in existing else to_write).append(tx)

    print("\n" + "=" * 92)
    print(f"{'DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA'}"
          f"  |  a escribir: {len(to_write)}  |  ya en la hoja (skip dedupe): {len(skipped)}")
    print("=" * 92)
    hdr = f"  {'Fecha':10}  {'Importe':>9}  {'Categoría':20} {'Banco':9} {'Titular':9} {'Mes':8} {'Tipo':8} Descripción"
    print(hdr)
    for tx in txs:
        dedup = '  <<< ya en hoja (no se reescribe)' if tx not in to_write else ''
        print(f"  {tx.fmt_date():10}  {_signed(tx):>9.2f}  {tx.category:20} {tx.bank:9} "
              f"{TITULAR:9} {tx.date:%Y-%m}  {tx.tx_type:8} {tx.description}{dedup}")

    if not write:
        print("\n(dry-run: nada escrito. Ejecuta con --write para confirmar.)")
        return

    client.add_titular(TITULAR)
    saved = client.write_transactions(txs, titular=TITULAR)
    print(f"\n>>> ESCRITAS {len(saved)} filas (descartadas por dedupe: {len(txs) - len(saved)})")
    for tx in saved:
        print(f"    {tx.fmt_date()}  {_signed(tx):>9.2f}  {tx.bank:9} {tx.tx_type:8} {tx.description}")


if __name__ == '__main__':
    main()
