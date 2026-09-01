#!/usr/bin/env python3
"""One-shot import: tres extractos de María Ruisánchez, agosto 2026.

  1. Extracto de cuenta (11).pdf          -> Trade Republic (cta corriente María)
  2. File (2).pdf                          -> Santander María (IBAN 0049)
  3. transactions_1788195777.494518.pdf    -> Openbank "CTA NOMINA OPEN" María

Uso:
  python3 import_maria_agosto2026_3cuentas.py           # DRY-RUN (no escribe nada)
  python3 import_maria_agosto2026_3cuentas.py --write    # escribe en Google Sheets

Reglas de negocio (ya implementadas en los parsers, aquí solo se verifican):
  - Santander María: income -> 'income'; outgoing -> 'internal' (nunca 'expense').
  - Trade Republic: Interés / Bonificación / Rentabilidad / Operar -> 'investment'.
  - Transferencias entre cuentas propias de María -> 'internal'.
  - is_renta_trabajo (sheets.py) marca rentas de trabajo de María para excluirlas
    del cálculo de ingresos compensatorios; no se toca aquí.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import (
    TradeRepublicParser, SantanderPDFParser, OpenbankCuentasPDFParser,
    disambiguate_duplicates, _detect_bank_from_pdf,
)
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient, is_renta_trabajo

TITULAR = 'María Ruisánchez'

FILES = [
    ('/Users/pablocavallergrau/Downloads/Extracto de cuenta (11).pdf', TradeRepublicParser, 'trade_republic'),
    ('/Users/pablocavallergrau/Downloads/File (2).pdf', SantanderPDFParser, 'santander'),
    ('/Users/pablocavallergrau/Downloads/transactions_1788195777.494518.pdf', OpenbankCuentasPDFParser, 'openbank_cuentas'),
]


def _signed(tx) -> float:
    return -tx.amount if tx.tx_type == 'expense' else tx.amount


def main() -> None:
    write = '--write' in sys.argv

    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    load_custom_rules(client)

    # ── Verificación de detección de banco ────────────────────────────────────
    print("== Detección de banco (por contenido) ==")
    for path, _parser, expected in FILES:
        detected = _detect_bank_from_pdf(path)
        flag = 'OK' if detected == expected else '!!! MISMATCH'
        print(f"  {flag:4}  {os.path.basename(path):42}  detect={detected}  (esperado {expected})")
    print()

    # ── Parseo + clasificación ───────────────────────────────────────────────
    all_txs = []
    for path, ParserClass, _expected in FILES:
        txs = ParserClass().parse(path)
        apply_type_overrides(txs)
        # fuera de agosto 2026 -> descartar (los extractos deberían venir limpios,
        # pero el TR (11) puede traer alguna cola de julio)
        keep = [t for t in txs if t.date.strftime('%Y-%m') == '2026-08']
        dropped = len(txs) - len(keep)
        all_txs.extend(keep)
        note = f"  ({dropped} fuera de agosto 2026 descartadas)" if dropped else ""
        print(f"parseadas {os.path.basename(path):42} -> {len(keep)} filas de agosto{note}")

    cats = classify_batch(all_txs)
    for tx, cat in zip(all_txs, cats):
        tx.category = cat

    disambiguate_duplicates(all_txs)

    # ── Dedupe simulado contra la Sheet en vivo ──────────────────────────────
    existing = client._existing_keys()
    to_write, skipped = [], []
    for tx in all_txs:
        key = (tx.fmt_date(), tx.description, client._amount_key(_signed(tx)), tx.bank)
        (skipped if key in existing else to_write).append(tx)

    # ── Informe dry-run ─────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print(f"{'DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA'}"
          f"  |  a escribir: {len(to_write)}  |  ya en la Sheet (skip dedupe): {len(skipped)}")
    print("=" * 90)

    if skipped:
        print("\n-- Ya presentes en la Sheet (no se reescriben) --")
        for tx in sorted(skipped, key=lambda t: t.date):
            print(f"  {tx.fmt_date()}  {_signed(tx):>10.2f}  [{tx.tx_type:10}] {tx.bank:14} {tx.description}")

    by_bank = defaultdict(list)
    for tx in to_write:
        by_bank[tx.bank].append(tx)

    for bank, txs in by_bank.items():
        print(f"\n{'─' * 90}\n{bank}  ({len(txs)} filas)\n{'─' * 90}")
        for tx in sorted(txs, key=lambda t: t.date):
            rt = ' [renta_trabajo]' if (tx.tx_type == 'income' and is_renta_trabajo(tx.description)) else ''
            print(f"  {tx.fmt_date()}  {_signed(tx):>10.2f}  [{tx.tx_type:10}] {tx.category:24} {tx.description}{rt}")

        tipo_counts = defaultdict(int)
        tipo_sum = defaultdict(float)
        for tx in txs:
            tipo_counts[tx.tx_type] += 1
            tipo_sum[tx.tx_type] += _signed(tx)
        print(f"  {'.' * 40}")
        for tp in sorted(tipo_counts):
            print(f"  {tp:12} n={tipo_counts[tp]:3}  suma={tipo_sum[tp]:>12.2f}")

    # ── Agregados de cash flow (solo lo que se escribiría + lo ya presente) ──
    universe = to_write + skipped
    print(f"\n{'=' * 90}\nAGREGADOS agosto 2026 María (todas las filas del import, incl. ya presentes)\n{'=' * 90}")

    income_rows = [t for t in universe if t.tx_type == 'income']
    expense_rows = [t for t in universe if t.tx_type == 'expense']
    total_income = sum(_signed(t) for t in income_rows)
    total_expense = sum(t.amount for t in expense_rows)
    print(f"\nINGRESOS (Tipo=income): {total_income:.2f}  ({len(income_rows)} filas)")
    cat_in = defaultdict(float)
    for t in income_rows:
        cat_in[t.category] += _signed(t)
    for c, v in sorted(cat_in.items(), key=lambda kv: -kv[1]):
        print(f"    {c:24} {v:>10.2f}")
    print(f"    -- renta de trabajo (excluida de ingresos compensatorios):")
    for t in income_rows:
        tag = 'renta_trabajo' if is_renta_trabajo(t.description) else 'NO -> compensatorio'
        print(f"       {t.fmt_date()} {_signed(t):>9.2f} [{tag:20}] {t.bank:12} {t.description}")

    print(f"\nGASTOS (Tipo=expense, ABS): {total_expense:.2f}  ({len(expense_rows)} filas)")
    cat_ex = defaultdict(float)
    for t in expense_rows:
        cat_ex[t.category] += t.amount
    for c, v in sorted(cat_ex.items(), key=lambda kv: -kv[1]):
        print(f"    {c:24} {v:>10.2f}")

    internal_rows = [t for t in universe if t.tx_type == 'internal']
    invest_rows = [t for t in universe if t.tx_type == 'investment']
    print(f"\ninternal: {len(internal_rows)} filas, suma {sum(_signed(t) for t in internal_rows):.2f}")
    print(f"investment: {len(invest_rows)} filas, suma {sum(_signed(t) for t in invest_rows):.2f}")

    # ── Chequeos de sanidad ─────────────────────────────────────────────────
    print(f"\n{'=' * 90}\nCHEQUEOS DE SANIDAD\n{'=' * 90}")
    empty_cat = [t for t in universe if not t.category]
    print(f"1. Filas con Categoría vacía: {len(empty_cat)}")
    for t in empty_cat:
        print(f"     {t.fmt_date()} {t.bank} {t.description}")

    KNOWN_TIPOS = {'expense', 'income', 'internal', 'investment', 'patrimonio', 'cash_withdrawal'}
    bad_tipo = [t for t in universe if t.tx_type not in KNOWN_TIPOS]
    print(f"4. Filas con Tipo desconocido: {len(bad_tipo)}  {[t.tx_type for t in bad_tipo]}")

    # conteo mensual histórico de María
    recs = client._get_all_records()
    monthly = defaultdict(int)
    for r in recs:
        if r.get('Titular') == TITULAR:
            monthly[r.get('Mes', '')] += 1
    prev = [monthly[m] for m in sorted(monthly) if m < '2026-08'][-6:]
    avg = sum(prev) / len(prev) if prev else 0
    proj_aug = monthly.get('2026-08', 0) + len(to_write)
    print(f"3. Filas María: media 6 meses previos = {avg:.1f}  ({prev})")
    print(f"   agosto 2026 tras import = {proj_aug}  "
          f"(ya en Sheet: {monthly.get('2026-08', 0)}, nuevas: {len(to_write)})")
    if avg:
        dev = (proj_aug - avg) / avg * 100
        print(f"   desviación vs media: {dev:+.0f}%  {'<-- REVISAR (>40%)' if abs(dev) > 40 else 'OK'}")

    print("\n2. Recurrentes esperados de María en agosto (buscar en el listado de arriba):")
    checks = {
        'TGSS autónomos -88,56 (Santander 31 ago)': any('TGSS' in t.description.upper() and abs(t.amount - 88.56) < 0.01 for t in universe),
        'Netflix vía transferencia -2,34 (Openbank)': any('NETFLIX' in t.description.upper() and abs(t.amount - 2.34) < 0.01 for t in universe),
        'Apple.com/bill -9,99 (Openbank)': any('APPLE.COM' in t.description.upper() and abs(t.amount - 9.99) < 0.01 for t in universe),
        'Ingresos de sesiones de psicología': any(t.tx_type == 'income' and ('SESION' in t.description.upper() or 'CHEVERE' in t.description.upper()) for t in universe),
    }
    for label, ok in checks.items():
        print(f"   [{'OK' if ok else 'FALTA'}] {label}")

    # ── Escritura ───────────────────────────────────────────────────────────
    if write:
        client.add_titular(TITULAR)
        # write_transactions deduplica internamente; pasamos el batch completo
        saved = client.write_transactions(all_txs, titular=TITULAR)
        print(f"\n>>> ESCRITAS {len(saved)} filas en la Sheet (descartadas dup: {len(all_txs) - len(saved)})")
        by_bank_saved = defaultdict(lambda: defaultdict(int))
        for tx in saved:
            by_bank_saved[tx.bank][tx.tx_type] += 1
        for bank, d in by_bank_saved.items():
            print(f"    {bank:14} " + "  ".join(f"{k}={v}" for k, v in sorted(d.items())))
    else:
        print("\n(dry-run: nada escrito. Ejecuta con --write para confirmar.)")


if __name__ == '__main__':
    main()
