#!/usr/bin/env python3
"""One-shot: asigna Categoría a las 4 filas de gasto que la tienen vacía en la
ventana de 12 meses (regla de sanidad de PROJECT.md: cero filas con Categoría vacía).

  15/01/2026 · EDWARDS                 · -43,32 · Trade Republic -> Ropa/Compras
  01/02/2026 · PARKING IBERMOTOR BALMES · -7,57 · Trade Republic -> Coche
  24/02/2026 · CHURRERIA CRISLA         · -6,93 · Trade Republic -> Restaurantes
  28/03/2026 · LAFUENTE                 · -11,50 · Trade Republic -> Otros (ambiguo, revisar)

Criterio:
  - PARKING IBERMOTOR BALMES: parking fusionado en Coche (regla PROJECT.md); además la
    otra ocurrencia del mismo comercio (06/08/2026) ya está en Coche.
  - CHURRERIA CRISLA: churrería -> Restaurantes (instrucción explícita de la tarea).
    Nota: la otra ocurrencia (10/11/2025) está en Alimentación; discrepancia anotada.
  - EDWARDS: mismo comercio y misma tarjeta (Trade Republic) que la fila 31/07/2026
    de -85,95 ya categorizada Ropa/Compras. Importe (-43,32) coherente con ropa.
  - LAFUENTE: única ocurrencia, -11,50, sin señal de comercio (¿librería? ¿bar?
    ¿ferretería?). Genuinamente ambiguo -> Otros EXPLÍCITO, marcado para revisión de Pablo.

Match exacto por (Fecha, Descripción, abs(Importe), Banco) vía
SheetsClient.update_transaction_category. Verifica que la Categoría actual está vacía
antes de tocar; no crea categorías nuevas.

Uso:
  python3 fix_categorias_vacias.py            # DRY-RUN
  python3 fix_categorias_vacias.py --write    # escribe en Google Sheets
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

# (fecha, descripción, importe_abs, banco, categoría_nueva)
ROWS = [
    ('15/01/2026', 'EDWARDS',                  43.32, 'Trade Republic', 'Ropa/Compras'),
    ('01/02/2026', 'PARKING IBERMOTOR BALMES',  7.57, 'Trade Republic', 'Coche'),
    ('24/02/2026', 'CHURRERIA CRISLA',          6.93, 'Trade Republic', 'Restaurantes'),
    ('28/03/2026', 'LAFUENTE',                 11.50, 'Trade Republic', 'Otros'),
]


def main() -> None:
    write = '--write' in sys.argv
    client = SheetsClient(credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'))

    recs = client._get_all_records()

    def _current_cat(fecha: str, desc: str, amount: float, banco: str) -> str | None:
        for r in recs:
            try:
                amt = round(abs(float(str(r.get('Importe', 0)).replace(',', '.'))), 2)
            except ValueError:
                continue
            if (r.get('Fecha') == fecha and r.get('Descripción') == desc
                    and amt == round(amount, 2) and r.get('Banco') == banco):
                return r.get('Categoría', '')
        return None

    print('=' * 92)
    print(f"{'DRY-RUN (no se escribe nada)' if not write else 'MODO ESCRITURA'}")
    print('=' * 92)

    plan = []
    for fecha, desc, amount, banco, cat in ROWS:
        cur = _current_cat(fecha, desc, amount, banco)
        status = 'OK vacía' if cur == '' else (f"NO ENCONTRADA" if cur is None else f"YA TIENE {cur!r} (skip)")
        print(f"  {fecha}  {-amount:>9.2f}  {banco:15}  {desc:26} actual={cur!r:12} -> {cat!r}   [{status}]")
        if cur == '':
            plan.append((fecha, desc, amount, banco, cat))

    if not write:
        print(f"\n(dry-run: {len(plan)} filas se actualizarían. Ejecuta con --write para confirmar.)")
        return

    done = 0
    for fecha, desc, amount, banco, cat in plan:
        ok = client.update_transaction_category(fecha, desc, amount, banco, cat)
        print(f"  {'OK ' if ok else 'FALLO '} {fecha} {desc!r} -> {cat!r}")
        done += ok
    print(f"\n>>> {done}/{len(plan)} filas actualizadas.")


if __name__ == '__main__':
    main()
