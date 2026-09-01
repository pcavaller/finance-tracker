#!/usr/bin/env python3
"""One-shot import: 4 extractos de Pablo Cavaller, agosto 2026.

  1. Certificado de saldo y movimientos (2).pdf  -> Trade Republic (Pablo Cavaller)
  2. transactions_1788252736230.pdf              -> Openbank "CTA NOMINA OPEN" (Pablo Cavaller)
  3. 01-09-2026.pdf                              -> BBVA cuenta PERSONAL de Pablo   (5 filas explícitas)
  4. 01-09-2026-1.pdf                            -> BBVA cuenta CONJUNTA / hipoteca (8 filas explícitas)

Uso:
  python3 import_pablo_agosto2026.py            # DRY-RUN (no escribe nada)
  python3 import_pablo_agosto2026.py --write     # escribe en Google Sheets

Ficheros 1 y 2: el parser autodetecta por contenido y aplica sus reglas de negocio
(ver PROJECT.md). Aquí solo se verifican, se clasifican y se filtra a Mes == 2026-08.
  - Trade Republic: Interés / Bonificación / Operar -> 'investment'; transferencias
    con contraparte 'PABLO CAVALLER GRAU' -> 'internal'; MassimoDutti.com 69,95 sube
    el balance -> '[Devolución]' income (verificado, no necesita override).
  - Openbank: BIZUM/COMPRA estándar; 'ABONO EN LA TARJETA' 5,95 -> income; PARKING
    IBERMOTOR -> Categoría 'Coche'; recarga Revolut -> 'internal'.

Ficheros 3 y 4: BBVAParser solo lee xlsx y detect_bank() da 'unknown' para estos
PDF de "Últimos movimientos", así que sus filas van EXPLÍCITAS aquí. Fechas e
importes confirmados leyendo el PDF con pdfplumber; el tratamiento (Tipo/Categoría)
lo fijó Pablo. Convención del repo (igual que BBVAParser): internal / patrimonio /
fianza se guardan con Importe positivo; solo 'expense' lleva signo negativo.

Tipo 'fianza' (nuevo): fianzas de inquilinos, ni income ni expense — webapp.py /
sheets.py ya excluyen implícitamente cualquier Tipo != expense/income.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from parsers import (
    Transaction, TradeRepublicParser, OpenbankCuentasPDFParser,
    disambiguate_duplicates, _detect_bank_from_pdf,
)
from classifier import load_custom_rules, classify_batch, apply_type_overrides
from sheets import SheetsClient, is_renta_trabajo

# ── Ficheros parseables (Pablo Cavaller) ──────────────────────────────────────
PARSED_FILES = [
    ('/Users/pablocavallergrau/Downloads/Certificado de saldo y movimientos (2).pdf',
     TradeRepublicParser, 'trade_republic'),
    ('/Users/pablocavallergrau/Downloads/transactions_1788252736230.pdf',
     OpenbankCuentasPDFParser, 'openbank_cuentas'),
]

# Saldos de cada extracto parseable, para el chequeo de cuadre (dry-run).
#   ('etiqueta', saldo_inicial, saldo_final)
PARSED_BALANCES = {
    'Trade Republic': ('Trade Republic (Pablo)', 2829.65, 2361.77),
    # Openbank: el PDF solo cubre 03-24 ago; saldo antes de la fila más antigua
    # (03/08 -10,00 -> saldo 155,25  =>  pre-saldo 165,25) y saldo tras la más
    # reciente (24/08 -> 19,64).
    'Openbank': ('Openbank CTA NOMINA (Pablo, 03-24 ago)', 165.25, 19.64),
}

# ── Ficheros BBVA: filas explícitas ───────────────────────────────────────────
# (fecha, descripción, importe_abs, categoría, tipo, titular, vivienda, signed_real)
#   signed_real = movimiento con su signo real en el extracto (solo para cuadre).
BBVA_PERSONAL = [
    ('04/08/2026', 'Traspaso a cuenta PABLO CAVALLER GRAU',            50.00,   'Otros',           'internal', 'Pablo Cavaller', False,   -50.00),
    ('04/08/2026', 'Transferencia recibida de pablo cavaller grau',    50.00,   'Otros',           'internal', 'Pablo Cavaller', False,    50.00),
    ('26/08/2026', 'Transferencia recibida - Nomina Diverinvest Agosto', 2126.41, 'Otros',         'income',   'Pablo Cavaller', False,  2126.41),
    ('27/08/2026', 'Traspaso a cuenta PABLO CAVALLER GRAU',          2000.00,   'Otros',           'internal', 'Pablo Cavaller', False, -2000.00),
    ('28/08/2026', 'Transferencia realizada Pablo Cavaller',          126.41,   'Otros',           'internal', 'Pablo Cavaller', False,  -126.41),
]

BBVA_CONJUNTA = [
    ('03/08/2026', 'Adeudo de asesoria, gestoria o consultor - Diagonal Company', 28520.54, 'Compra vivienda',   'patrimonio', 'Conjunta', True,  -28520.54),
    ('04/08/2026', 'Traspaso desde cuenta Pablo cavaller grau',                       50.00, 'Otros',             'internal',   'Conjunta', False,      50.00),
    ('05/08/2026', 'Bbva plan estarseguro',                                           41.20, 'Seguros',           'expense',    'Conjunta', False,     -41.20),
    ('25/08/2026', 'Abono por transferencia recibida (fianza inquilino)',            700.00, 'Fianza inquilinos', 'fianza',     'Conjunta', False,     700.00),
    ('25/08/2026', 'Transferencia recibida de mme fanni guerard',                    20.00, 'Otros',             'income',      'Conjunta', False,      20.00),
    ('27/08/2026', 'Traspaso desde cuenta Pablo cavaller grau',                    2000.00, 'Otros',             'internal',    'Conjunta', False,    2000.00),
    ('31/08/2026', 'Cargo por amortizacion de prestamo/credito',                  1672.64, 'Hipoteca',          'expense',      'Conjunta', True,    -1672.64),
    ('31/08/2026', 'Transferencia recibida de paulo andres leon hereira',          650.00, 'Fianza inquilinos', 'fianza',      'Conjunta', False,     650.00),
]

# Saldos reales de cada extracto BBVA, para el cuadre.
BBVA_BALANCES = {
    'BBVA personal (Pablo)': (0.00, 0.00, BBVA_PERSONAL),
    'BBVA conjunta (hipoteca)': (28537.99, 1723.61, BBVA_CONJUNTA),
}

KNOWN_TIPOS = {'expense', 'income', 'internal', 'investment', 'patrimonio', 'fianza', 'cash_withdrawal'}
KNOWN_TITULARES = {'Pablo Cavaller', 'Conjunta'}


def _signed(tx: Transaction) -> float:
    """Importe con el signo con que se ESCRIBE en la Sheet (solo 'expense' negativo)."""
    return -tx.amount if tx.tx_type == 'expense' else tx.amount


def _tr_recon_signed(tx: Transaction) -> float:
    """Signo real del movimiento en el extracto (para cuadre de saldos)."""
    if tx.tx_type == 'expense':
        return -tx.amount
    if tx.tx_type == 'income':
        return tx.amount
    if tx.tx_type == 'internal':
        return tx.amount if 'Incoming' in tx.description or 'recibida' in tx.description.lower() else -tx.amount
    if tx.tx_type == 'investment':
        inflow = ('Sell trade', 'Cash reward', 'Interest payment', 'Cash Dividend', 'Interés', 'Interest')
        return tx.amount if tx.description.startswith(inflow) else -tx.amount
    return tx.amount


def _ob_recon_signed(tx: Transaction) -> float:
    if tx.tx_type == 'expense':
        return -tx.amount
    if tx.tx_type == 'income':
        return tx.amount
    if tx.tx_type == 'internal':
        # única fila interna del extracto: recarga de Revolut (salida)
        return -tx.amount
    return tx.amount


def _mk_explicit(rows) -> list[tuple[Transaction, str, bool, float]]:
    out = []
    for fecha, desc, amount, cat, tipo, titular, vivienda, signed_real in rows:
        tx = Transaction(
            date=datetime.strptime(fecha, '%d/%m/%Y'),
            description=desc,
            amount=abs(amount),
            tx_type=tipo,
            bank='BBVA',
            category=cat,
        )
        out.append((tx, titular, vivienda, signed_real))
    return out


def main() -> None:
    write = '--write' in sys.argv

    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))
    load_custom_rules(client)

    # ── Detección de banco (informativa) ─────────────────────────────────────
    print("== Detección de banco (por contenido; el parser se aplica explícito) ==")
    for path, _P, expected in PARSED_FILES:
        detected = _detect_bank_from_pdf(path)
        # el nombre "...movimientos.pdf" hace que detect_bank devuelva 'openbank_pdf'
        # para el TR antes de mirar el contenido; no afecta al parseo (parser explícito).
        flag = 'OK' if detected == expected else 'nota (parser explícito)'
        print(f"  {flag:26}  {os.path.basename(path):46}  detect={detected}  (esperado {expected})")
    print()

    # ── Parseo + clasificación de ficheros 1 y 2 ────────────────────────────
    parsed_txs: list[Transaction] = []
    for path, ParserClass, _expected in PARSED_FILES:
        txs = ParserClass().parse(path)
        apply_type_overrides(txs)
        keep = [t for t in txs if t.date.strftime('%Y-%m') == '2026-08']
        dropped = len(txs) - len(keep)
        parsed_txs.extend(keep)
        note = f"  ({dropped} fuera de 2026-08 descartadas)" if dropped else ""
        print(f"parseadas {os.path.basename(path):46} -> {len(keep)} filas de agosto{note}")

    cats = classify_batch(parsed_txs)
    for tx, cat in zip(parsed_txs, cats):
        tx.category = cat

    # ── Filas explícitas BBVA ──────────────────────────────────────────────
    explicit = _mk_explicit(BBVA_PERSONAL) + _mk_explicit(BBVA_CONJUNTA)

    # ── Batch completo + desambiguación de colisiones ──────────────────────
    all_txs = parsed_txs + [t for (t, *_ ) in explicit]
    desc_before = [t.description for t in all_txs]
    disambiguate_duplicates(all_txs)
    renamed = [(b, t.description) for b, t in zip(desc_before, all_txs) if b != t.description]
    if renamed:
        print("\n-- disambiguate_duplicates renombró (colisión real en el batch) --")
        for b, a in renamed:
            print(f"   {b!r}  ->  {a!r}")
    else:
        print("\ndisambiguate_duplicates: sin colisiones en el batch (0 renombradas)")

    # ── Titular por transacción ───────────────────────────────────────────
    titular_of: dict[int, str] = {}
    for tx in parsed_txs:
        titular_of[id(tx)] = 'Pablo Cavaller'
    vivienda_of: dict[int, bool] = {}
    for tx, titular, vivienda, _signed_real in explicit:
        titular_of[id(tx)] = titular
        vivienda_of[id(tx)] = vivienda

    # ── Dedupe simulado contra la Sheet en vivo ───────────────────────────
    existing = client._existing_keys()
    to_write, skipped = [], []
    for tx in all_txs:
        key = (tx.fmt_date(), tx.description, client._amount_key(_signed(tx)), tx.bank)
        (skipped if key in existing else to_write).append(tx)

    # ── Informe dry-run ──────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print(f"{'DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA'}"
          f"  |  a escribir: {len(to_write)}  |  ya en la Sheet (skip dedupe): {len(skipped)}")
    print("=" * 96)

    if skipped:
        print("\n-- Ya presentes en la Sheet (no se reescriben) --")
        for tx in sorted(skipped, key=lambda t: t.date):
            print(f"  {tx.fmt_date()}  {_signed(tx):>11.2f}  [{tx.tx_type:10}] {tx.bank:14} "
                  f"{titular_of[id(tx)]:15} {tx.description}")

    by_bank: dict[str, list[Transaction]] = defaultdict(list)
    for tx in all_txs:
        by_bank[tx.bank].append(tx)

    for bank, txs in by_bank.items():
        print(f"\n{'-' * 96}\n{bank}  ({len(txs)} filas)\n{'-' * 96}")
        for tx in sorted(txs, key=lambda t: t.date):
            rt = ' [renta_trabajo]' if (tx.tx_type == 'income' and is_renta_trabajo(tx.description)) else ''
            viv = ' [Vivienda]' if vivienda_of.get(id(tx)) else ''
            dedup = '' if tx in to_write else '  <<< ya en Sheet'
            print(f"  {tx.fmt_date()}  {_signed(tx):>11.2f}  [{tx.tx_type:10}] "
                  f"{(tx.category or '—'):18} {tx.description[:64]}{rt}{viv}{dedup}")
        tipo_counts = defaultdict(int)
        tipo_sum = defaultdict(float)
        for tx in txs:
            tipo_counts[tx.tx_type] += 1
            tipo_sum[tx.tx_type] += _signed(tx)
        print(f"  {'.' * 48}")
        for tp in sorted(tipo_counts):
            print(f"  {tp:12} n={tipo_counts[tp]:3}  suma(escritura)={tipo_sum[tp]:>13.2f}")

    # ── Cuadre de saldos por extracto ────────────────────────────────────
    print(f"\n{'=' * 96}\nCUADRE DE SALDOS (suma de movimientos con signo real vs. saldo inicial/final)\n{'=' * 96}")

    tr_txs = [t for t in all_txs if t.bank == 'Trade Republic']
    label, ini, fin = PARSED_BALANCES['Trade Republic']
    net = sum(_tr_recon_signed(t) for t in tr_txs)
    ok = abs((ini + net) - fin) < 0.01
    print(f"  {label:44}  ini={ini:>11.2f}  + mov={net:>11.2f}  = {ini + net:>11.2f}  "
          f"(esperado {fin:.2f})  {'OK' if ok else '!!! DESCUADRE'}")

    ob_txs = [t for t in all_txs if t.bank == 'Openbank']
    label, ini, fin = PARSED_BALANCES['Openbank']
    net = sum(_ob_recon_signed(t) for t in ob_txs)
    ok = abs((ini + net) - fin) < 0.01
    print(f"  {label:44}  ini={ini:>11.2f}  + mov={net:>11.2f}  = {ini + net:>11.2f}  "
          f"(esperado {fin:.2f})  {'OK' if ok else '!!! DESCUADRE'}")

    for label, (ini, fin, rows) in BBVA_BALANCES.items():
        net = sum(signed_real for *_x, signed_real in rows)
        ok = abs((ini + net) - fin) < 0.01
        print(f"  {label:44}  ini={ini:>11.2f}  + mov={net:>11.2f}  = {ini + net:>11.2f}  "
              f"(esperado {fin:.2f})  {'OK' if ok else '!!! DESCUADRE'}")

    # ── Agregados de cash flow (income / expense) ───────────────────────
    universe = all_txs  # agosto de Pablo es todo nuevo; incluye lo ya presente por si acaso
    print(f"\n{'=' * 96}\nAGREGADOS agosto 2026  (Pablo Cavaller + Conjunta)\n{'=' * 96}")

    income_rows = [t for t in universe if t.tx_type == 'income']
    expense_rows = [t for t in universe if t.tx_type == 'expense']
    total_income = sum(t.amount for t in income_rows)
    total_expense = sum(t.amount for t in expense_rows)

    print(f"\nINGRESOS (Tipo=income): {total_income:.2f}   ({len(income_rows)} filas)")
    cat_in = defaultdict(float)
    tit_in = defaultdict(float)
    for t in income_rows:
        cat_in[t.category] += t.amount
        tit_in[titular_of[id(t)]] += t.amount
    print("  por categoría:")
    for c, v in sorted(cat_in.items(), key=lambda kv: -kv[1]):
        print(f"    {c:22} {v:>11.2f}")
    print("  por titular:")
    for c, v in sorted(tit_in.items(), key=lambda kv: -kv[1]):
        print(f"    {c:22} {v:>11.2f}")
    print("  renta de trabajo?:")
    for t in income_rows:
        tag = 'renta_trabajo' if is_renta_trabajo(t.description) else 'NO'
        print(f"    {t.fmt_date()} {t.amount:>10.2f} [{tag:14}] {t.bank:14} {t.description}")

    print(f"\nGASTOS (Tipo=expense, ABS): {total_expense:.2f}   ({len(expense_rows)} filas)")
    cat_ex = defaultdict(float)
    tit_ex = defaultdict(float)
    for t in expense_rows:
        cat_ex[t.category] += t.amount
        tit_ex[titular_of[id(t)]] += t.amount
    print("  por categoría:")
    for c, v in sorted(cat_ex.items(), key=lambda kv: -kv[1]):
        print(f"    {c:22} {v:>11.2f}")
    print("  por titular:")
    for c, v in sorted(tit_ex.items(), key=lambda kv: -kv[1]):
        print(f"    {c:22} {v:>11.2f}")

    for tp in ('internal', 'investment', 'patrimonio', 'fianza'):
        rows = [t for t in universe if t.tx_type == tp]
        print(f"\n{tp}: {len(rows)} filas, suma(abs) {sum(t.amount for t in rows):.2f}")
        for t in rows:
            print(f"    {t.fmt_date()} {t.amount:>11.2f}  {t.bank:14} {titular_of[id(t)]:15} {t.description}")

    # ── Chequeos de sanidad ────────────────────────────────────────────
    print(f"\n{'=' * 96}\nCHEQUEOS DE SANIDAD\n{'=' * 96}")
    empty_cat = [t for t in universe if not t.category]
    print(f"1. Filas con Categoría vacía: {len(empty_cat)}")
    for t in empty_cat:
        print(f"     {t.fmt_date()} {t.bank} {t.description}")

    bad_tipo = [t for t in universe if t.tx_type not in KNOWN_TIPOS]
    print(f"2. Filas con Tipo desconocido: {len(bad_tipo)}  {[t.tx_type for t in bad_tipo]}")
    bad_tit = [titular_of[id(t)] for t in universe if titular_of[id(t)] not in KNOWN_TITULARES]
    print(f"3. Titulares desconocidos: {len(bad_tit)}  {bad_tit}")

    massimo = [t for t in universe if 'MASSIMODUTTI' in t.description.upper() or 'MASSIMO DUTTI' in t.description.upper()]
    print(f"4. MassimoDutti.com: " + (
        "  ".join(f"[{t.tx_type}] {_signed(t):+.2f} '{t.description}'" for t in massimo) or "NO ENCONTRADA"))
    abono = [t for t in universe if 'ABONO EN LA TARJETA' in t.description.upper() or t.description == 'Devolución tarjeta']
    print(f"5. ABONO EN LA TARJETA (Openbank 5,95): " + (
        "  ".join(f"[{t.tx_type}] {_signed(t):+.2f} '{t.description}'" for t in abono) or "NO ENCONTRADA"))
    nomina = [t for t in universe if 'NOMINA DIVERINVEST' in t.description.upper() or 'NÓMINA DIVERINVEST' in t.description.upper()]
    print(f"6. Nómina DiverInvest: " + (
        "  ".join(f"[{t.tx_type}] {t.amount:+.2f} renta_trabajo={is_renta_trabajo(t.description)} '{t.description}'"
                  for t in nomina) or "NO ENCONTRADA"))

    # conteo mensual histórico
    recs = client._get_all_records()
    for titular in ('Pablo Cavaller', 'Conjunta'):
        monthly = defaultdict(int)
        for r in recs:
            if r.get('Titular') == titular:
                monthly[r.get('Mes', '')] += 1
        prev = [monthly[m] for m in sorted(monthly) if m < '2026-08'][-6:]
        avg = sum(prev) / len(prev) if prev else 0
        new_n = sum(1 for t in to_write if titular_of[id(t)] == titular)
        proj = monthly.get('2026-08', 0) + new_n
        line = f"7. {titular}: media 6m previos={avg:.1f} {prev}  |  agosto tras import={proj} (ya {monthly.get('2026-08', 0)}, nuevas {new_n})"
        if avg:
            dev = (proj - avg) / avg * 100
            line += f"  desv {dev:+.0f}% {'<-- REVISAR' if abs(dev) > 40 else 'OK'}"
        print(line)

    # ── Escritura ──────────────────────────────────────────────────────
    if write:
        client.add_titular('Pablo Cavaller')
        client.add_titular('Conjunta')

        pablo_batch = [t for t in all_txs if titular_of[id(t)] == 'Pablo Cavaller']
        conj_batch = [t for t in all_txs if titular_of[id(t)] == 'Conjunta']

        saved_p = client.write_transactions(pablo_batch, titular='Pablo Cavaller')
        saved_c = client.write_transactions(conj_batch, titular='Conjunta')
        saved = saved_p + saved_c
        print(f"\n>>> ESCRITAS {len(saved)} filas "
              f"(Pablo {len(saved_p)}, Conjunta {len(saved_c)}; "
              f"descartadas dup: {len(all_txs) - len(saved)})")

        by = defaultdict(lambda: defaultdict(int))
        for tx in saved:
            by[tx.bank][tx.tx_type] += 1
        for bank, d in by.items():
            print(f"    {bank:14} " + "  ".join(f"{k}={v}" for k, v in sorted(d.items())))

        marked = client.mark_vivienda_by_category(['Compra vivienda', 'Hipoteca'])
        print(f"\n>>> Vivienda marcada por Categoría (Compra vivienda / Hipoteca): {len(marked)} filas nuevas")
        for row in marked:
            print(f"    {row['fecha']}  {row['importe']:>12}  {row['banco']:8}  {row['descripcion']}")
    else:
        print("\n(dry-run: nada escrito. Ejecuta con --write para confirmar.)")


if __name__ == '__main__':
    main()
