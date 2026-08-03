#!/usr/bin/env python3
"""One-shot: marca la columna Vivienda='Sí' en la hoja Transacciones.

Dos grupos de filas:
1. Automático: toda fila con Categoría exactamente 'Compra vivienda' o 'Hipoteca'
   (julio 2026, BBVA Conjunta).
2. Manual: dos filas históricas de junio 2026 (préstamo del padre de María pagado
   directamente a la notaría + arras propias de la pareja), localizadas por
   fecha+descripción+importe+banco. Su Tipo/Categoría NO se toca.

La columna Vivienda es independiente de Tipo/Categoría y no participa en ningún
cálculo de cash flow existente — solo marca qué filas pertenecen a la vista
dedicada de compra de vivienda en la Mini App (/api/vivienda).

Uso:
  python3 mark_vivienda_agosto2026.py
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sheets import SheetsClient

HISTORICAL_ROWS = [
    # (fecha, descripcion, importe, banco)
    ('08/06/2026', 'TRANSFERENCIA A FAVOR DE LAURA ROS CONCEPTO: ARRAS', 50000, 'Openbank'),
    ('08/06/2026', 'ARRAS', -2000, 'Openbank'),
]


def main() -> None:
    client = SheetsClient(
        credentials_path=os.path.join(os.path.dirname(__file__), 'credentials.json'),
    )

    print("Marcando automáticamente por Categoría (Compra vivienda / Hipoteca)...")
    marked = client.mark_vivienda_by_category(['Compra vivienda', 'Hipoteca'])
    for row in marked:
        print(f"  {row['fecha']}  {row['importe']:>12}  {row['banco']:10}  {row['descripcion']}")
    print(f"  → {len(marked)} filas marcadas (o ya estaban marcadas)")

    print("\nMarcando filas históricas de junio 2026 (préstamo padre + arras propias)...")
    for fecha, descripcion, importe, banco in HISTORICAL_ROWS:
        ok = client.mark_vivienda(fecha, descripcion, importe, banco)
        status = "OK" if ok else "NO ENCONTRADA"
        print(f"  [{status}] {fecha}  {importe:>10}  {banco:10}  {descripcion}")

    print("\nVerificación — filas actuales en la vista Vivienda:")
    for r in client.get_vivienda_transactions():
        print(f"  {r['date']}  {r['amount']:>12.2f}  {r['category']:16}  {r['bank']:10}  {r['description']}")


if __name__ == '__main__':
    main()
