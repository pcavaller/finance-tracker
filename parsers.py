from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import re

import pdfplumber
from bs4 import BeautifulSoup

MONTH_ES = {
    'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12,
}

CATEGORIES = [
    'Alimentación',
    'Restaurantes',
    'Ropa/Compras',
    'Transporte',
    'Cultura/Entretenimiento',
    'Suscripciones/Tech',
    'Parking',
    'Hogar/Recibos',
    'Salud',
    'Efectivo',
    'Otros',
]

CATEGORY_EMOJI = {
    'Alimentación': '🛒',
    'Restaurantes': '🍽',
    'Ropa/Compras': '👔',
    'Transporte': '🚌',
    'Cultura/Entretenimiento': '🎭',
    'Suscripciones/Tech': '💻',
    'Parking': '🅿',
    'Hogar/Recibos': '🏠',
    'Salud': '❤',
    'Efectivo': '💵',
    'Otros': '📦',
}

# Keywords that identify own accounts (to filter internal transfers)
OWN_ACCOUNT_KEYWORDS = [
    'CAVALLER GRAU PABLO',
    'ES2000730100520612209683',  # Openbank IBAN
    'ES3615860001470793034611',  # Trade Republic IBAN
    'TRADE ES',
    'IBKR',
    'PABLO CAVALLER',
]


@dataclass
class Transaction:
    date: datetime
    description: str
    amount: float       # always positive; direction determined by tx_type
    tx_type: str        # 'expense' | 'income' | 'internal' | 'investment' | 'cash_withdrawal'
    bank: str
    category: str = ''

    def fmt_date(self) -> str:
        return self.date.strftime('%d/%m/%Y')

    def fmt_amount(self) -> str:
        s = f"{self.amount:,.2f}€"
        return s.replace(',', 'X').replace('.', ',').replace('X', '.')

    @property
    def category_label(self) -> str:
        emoji = CATEGORY_EMOJI.get(self.category, '📦')
        return f"{emoji} {self.category}" if self.category else "❓ Sin categoría"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_amount(s: str) -> Optional[float]:
    if not s:
        return None
    s = re.sub(r'[€\s]', '', str(s))
    s = s.replace('.', '').replace(',', '.')
    try:
        return abs(float(s))
    except ValueError:
        return None


def _parse_date_es(s: str) -> Optional[datetime]:
    s = ' '.join(str(s).split())
    m = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', s)
    if not m:
        return None
    day, mon, year = m.groups()
    month = MONTH_ES.get(mon.lower())
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day))
    except ValueError:
        return None


def _is_internal(description: str) -> bool:
    d = description.upper()
    return any(k.upper() in d for k in OWN_ACCOUNT_KEYWORDS)


def _clean_tr_desc(s: str) -> str:
    return re.sub(r'\s*null\s*$', '', str(s or ''), flags=re.IGNORECASE).strip()


def _clean_openbank_desc(concepto: str) -> str:
    m = re.match(r'COMPRA EN (.+?)(?:,\s*CON LA TARJETA|$)', concepto, re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r'BIZUM A FAVOR DE (.+?)(?:\s+CONCEPTO|$)', concepto, re.I)
    if m:
        return f"Bizum → {m.group(1).strip()}"
    return concepto


def detect_bank(filename: str) -> str:
    name = filename.lower()
    if 'extracto' in name or 'trade' in name or 'republic' in name:
        return 'trade_republic'
    if 'movimientos' in name or 'openbank' in name or 'cuenta' in name:
        return 'openbank'
    if 'revolut' in name:
        return 'revolut'
    return 'unknown'


# ── Trade Republic PDF Parser ──────────────────────────────────────────────────

class TradeRepublicParser:
    """
    Parse Trade Republic PDFs using word coordinates.
    Column layout (approximate x0 boundaries):
      FECHA: x < 100 | TIPO: 100–149 | DESC: 149–415
      ENTRADA: 415–453 | SALIDA: 453–483 | BALANCE: ≥483
    """

    # x0 column boundaries (from actual PDF word positions)
    X_TIPO = 100
    X_DESC = 149
    X_ENTRADA = 415
    X_SALIDA = 450   # card expenses appear at x0=452.5
    X_BALANCE = 480  # balance appears at x0=483.5

    def parse(self, pdf_path: str) -> list[Transaction]:
        in_section = False
        tx_blocks: list[list[dict]] = []
        current_block: list[dict] = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                h = page.height
                # Filter header (~130px) and footer (~60px from bottom)
                filtered = [w for w in words if w['top'] > 130 and w['top'] < h - 60]
                lines = self._group_lines(filtered, tolerance=6)

                for line in lines:
                    line_text = ' '.join(w['text'] for w in line)

                    # Section start marker (only on page 1)
                    if 'TRANSACCIONES' in line_text.upper() and 'CUENTA' in line_text.upper():
                        in_section = True
                        continue
                    if not in_section:
                        continue
                    # Section end
                    if 'RESUMEN DEL BALANCE' in line_text.upper() or 'NOTAS SOBRE' in line_text.upper():
                        if current_block:
                            tx_blocks.append(current_block)
                            current_block = []
                        in_section = False
                        break

                    # Skip column headers line
                    if 'FECHA' in line_text and 'TIPO' in line_text and 'DESCRIPCIÓN' in line_text:
                        continue

                    # New transaction: line contains a day number in FECHA column
                    fecha_words = [w for w in line if w['x0'] < self.X_TIPO
                                   and re.match(r'^\d{1,2}$', w['text'])]
                    if fecha_words:
                        if current_block:
                            tx_blocks.append(current_block)
                        current_block = list(line)
                    else:
                        current_block.extend(line)

        if current_block:
            tx_blocks.append(current_block)

        transactions = []
        for block in tx_blocks:
            tx = self._parse_block(block)
            if tx:
                transactions.append(tx)
        return transactions

    def _group_lines(self, words: list[dict], tolerance: int = 6) -> list[list[dict]]:
        """Group words into lines by proximity of top coordinate."""
        if not words:
            return []
        sorted_words = sorted(words, key=lambda w: w['top'])
        lines: list[list[dict]] = []
        current: list[dict] = [sorted_words[0]]
        ref_top = sorted_words[0]['top']

        for w in sorted_words[1:]:
            if abs(w['top'] - ref_top) <= tolerance:
                current.append(w)
            else:
                lines.append(sorted(current, key=lambda x: x['x0']))
                current = [w]
                ref_top = w['top']
        lines.append(sorted(current, key=lambda x: x['x0']))
        return lines

    def _parse_block(self, words: list[dict]) -> Optional[Transaction]:
        """Parse a transaction from its collected words."""
        fecha_words, tipo_words, desc_words, entrada_words, salida_words = [], [], [], [], []

        for w in words:
            x = w['x0']
            t = w['text']
            if x < self.X_TIPO:
                fecha_words.append(t)
            elif x < self.X_DESC:
                tipo_words.append(t)
            elif x < self.X_ENTRADA:
                desc_words.append(t)
            elif x < self.X_SALIDA:
                entrada_words.append(t)
            elif x < self.X_BALANCE:
                salida_words.append(t)
            # Balance column ignored

        date = _parse_date_es(' '.join(fecha_words))
        if not date:
            return None

        tipo = ' '.join(tipo_words).strip()
        desc = _clean_tr_desc(' '.join(desc_words))
        entrada = _parse_amount(' '.join(entrada_words))
        salida = _parse_amount(' '.join(salida_words))

        # Interest / bonuses → income
        if any(k in tipo for k in ('Interés', 'Bonificación')):
            if entrada:
                return Transaction(date=date, description=desc or tipo, amount=entrada,
                                   tx_type='income', bank='Trade Republic')
            return None

        # Investment operations
        if 'Operar' in tipo:
            amount = salida or entrada or 0
            return Transaction(date=date, description=desc, amount=amount,
                               tx_type='investment', bank='Trade Republic')

        # Transfers
        if 'Transferencia' in tipo:
            amount = salida or entrada or 0
            if _is_internal(desc):
                return Transaction(date=date, description=desc, amount=amount,
                                   tx_type='internal', bank='Trade Republic')
            if salida:
                return Transaction(date=date, description=desc, amount=salida,
                                   tx_type='expense', bank='Trade Republic')
            if entrada:
                return Transaction(date=date, description=desc, amount=entrada,
                                   tx_type='income', bank='Trade Republic')
            return None

        # Card transactions
        if 'tarjeta' in tipo or 'Transacción' in tipo:
            if salida:
                return Transaction(date=date, description=desc, amount=salida,
                                   tx_type='expense', bank='Trade Republic')
            if entrada:
                return Transaction(date=date, description=f'[Devolución] {desc}',
                                   amount=entrada, tx_type='income', bank='Trade Republic')

        return None


# ── Openbank XLS (HTML) Parser ─────────────────────────────────────────────────

class OpenbankParser:

    def parse(self, xls_path: str) -> list[Transaction]:
        with open(xls_path, encoding='iso-8859-1') as f:
            content = f.read()

        soup = BeautifulSoup(content, 'html.parser')
        transactions = []
        for row in soup.find_all('tr'):
            tx = self._parse_row(row)
            if tx:
                transactions.append(tx)
        return transactions

    def _parse_row(self, row) -> Optional[Transaction]:
        cells = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cells) < 8:
            return None

        # Openbank HTML has empty cols between data cols: [''、date、''、date_valor、''、concepto、''、importe、...]
        try:
            date = datetime.strptime(cells[1], '%d/%m/%Y')
        except ValueError:
            return None

        concepto = cells[5] if len(cells) > 5 else ''
        importe_str = cells[7] if len(cells) > 7 else ''

        if not concepto or not importe_str:
            return None

        importe_clean = importe_str.replace('.', '').replace(',', '.').strip()
        try:
            amount = float(importe_clean)
        except ValueError:
            return None

        cu = concepto.upper()

        # Salary / reimbursements from DiverInvest
        if 'DIVERINVEST' in cu:
            if amount > 0:
                desc = 'Nómina DiverInvest' if 'NOMINA' in cu or 'NÓMINA' in cu else f'DiverInvest: {concepto}'
                return Transaction(date=date, description=desc, amount=amount,
                                   tx_type='income', bank='Openbank')

        # ATM cash withdrawal
        if 'CAJERO' in cu or ('DISPOSICION' in cu and 'CAJERO' in cu):
            return Transaction(date=date, description=f'Cajero {abs(amount):.0f}€',
                               amount=abs(amount), tx_type='cash_withdrawal', bank='Openbank')

        # Revolut top-ups (loading Revolut via Openbank card)
        if 'REVOLUT' in cu and amount < 0:
            return Transaction(date=date, description=concepto, amount=abs(amount),
                               tx_type='internal', bank='Openbank')

        # Internal transfers (own accounts)
        if _is_internal(concepto):
            return Transaction(date=date, description=concepto, amount=abs(amount),
                               tx_type='internal', bank='Openbank')

        # Card refunds
        if 'ABONO EN LA TARJETA' in cu:
            return Transaction(date=date, description='Devolución tarjeta', amount=abs(amount),
                               tx_type='income', bank='Openbank')

        # Direct debits
        if 'RECIBO' in cu:
            desc = re.sub(r'\s*Nº RECIBO.*', '', concepto, flags=re.I).strip()
            return Transaction(date=date, description=desc, amount=abs(amount),
                               tx_type='expense', bank='Openbank')

        # Expenses (negative amount)
        if amount < 0:
            desc = _clean_openbank_desc(concepto)
            return Transaction(date=date, description=desc, amount=abs(amount),
                               tx_type='expense', bank='Openbank')

        # Income (positive, not already handled)
        if amount > 0:
            if 'BIZUM DE' in cu:
                desc = re.sub(r'^BIZUM DE\s*', '', concepto, flags=re.I)
                desc = re.sub(r'\s+CONCEPTO.*', '', desc, flags=re.I).strip()
                return Transaction(date=date, description=f'Bizum de {desc}',
                                   amount=amount, tx_type='income', bank='Openbank')
            return Transaction(date=date, description=concepto, amount=amount,
                               tx_type='income', bank='Openbank')

        return None
