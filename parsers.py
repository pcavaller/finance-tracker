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
    'Taxi',
    'Coche',
    'Transporte',
    'Cultura/Entretenimiento',
    'Suscripciones/Tech',
    'Hogar/Recibos',
    'Salud',
    'Efectivo',
    'Devolución',
    'Bizum',
    'Apuestas',
    'Clubs',
    'Formación',
    'Regalos',
    'Viajes',
    'Impuestos',
    'Otros',
]

CATEGORY_EMOJI = {
    'Alimentación': '🛒',
    'Restaurantes': '🍽',
    'Ropa/Compras': '👔',
    'Taxi': '🚕',
    'Coche': '🚗',
    'Transporte': '🚌',
    'Cultura/Entretenimiento': '🎭',
    'Suscripciones/Tech': '💻',
    'Hogar/Recibos': '🏠',
    'Salud': '❤',
    'Efectivo': '💵',
    'Bizum': '📲',
    'Apuestas': '🎲',
    'Clubs': '🏛',
    'Formación': '📚',
    'Regalos': '🎁',
    'Viajes': '✈️',
    'Impuestos': '🏛',
    'Devolución': '↩️',
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
    'MYINVESTOR',
    'BINANCE',
    'BIFINITY',
    'NAGA MARKETS',
    'BGET',
    'BUTGET',
    'TRADE REPUBLIC',
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
        return 'openbank_pdf' if name.endswith('.pdf') else 'openbank'
    if 'revolut' in name or 'account-statement' in name:
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
        prev_balance: Optional[float] = None
        for block in tx_blocks:
            tx, current_balance = self._parse_block(block, prev_balance)
            if current_balance is not None:
                prev_balance = current_balance
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

    def _parse_block(self, words: list[dict], prev_balance: Optional[float] = None) -> tuple[Optional[Transaction], Optional[float]]:
        """Parse a transaction from its collected words. Returns (transaction, balance_after)."""
        fecha_words, tipo_words, desc_words, entrada_words, salida_words, balance_words = [], [], [], [], [], []

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
            else:
                balance_words.append(t)

        date = _parse_date_es(' '.join(fecha_words))
        if not date:
            return None, None

        tipo = ' '.join(tipo_words).strip()
        desc = _clean_tr_desc(' '.join(desc_words))
        entrada = _parse_amount(' '.join(entrada_words))
        salida = _parse_amount(' '.join(salida_words))
        # Only keep numeric tokens (ignore "EUR" currency labels)
        balance_numeric = [t for t in balance_words if re.search(r'\d', t)]
        current_balance = _parse_amount(' '.join(balance_numeric[:1]))

        # Interest / bonuses → income
        if any(k in tipo for k in ('Interés', 'Bonificación')):
            if entrada:
                return Transaction(date=date, description=desc or tipo, amount=entrada,
                                   tx_type='income', bank='Trade Republic'), current_balance
            return None, current_balance

        # Investment operations
        if 'Operar' in tipo:
            amount = salida or entrada or 0
            return Transaction(date=date, description=desc, amount=amount,
                               tx_type='investment', bank='Trade Republic'), current_balance

        # Transfers
        if 'Transferencia' in tipo:
            amount = salida or entrada or 0
            if _is_internal(desc):
                return Transaction(date=date, description=desc, amount=amount,
                                   tx_type='internal', bank='Trade Republic'), current_balance
            if salida:
                return Transaction(date=date, description=desc, amount=salida,
                                   tx_type='expense', bank='Trade Republic'), current_balance
            if entrada:
                return Transaction(date=date, description=desc, amount=entrada,
                                   tx_type='income', bank='Trade Republic'), current_balance
            return None, current_balance

        # Card transactions
        if 'tarjeta' in tipo or 'Transacción' in tipo:
            if salida:
                return Transaction(date=date, description=desc, amount=salida,
                                   tx_type='expense', bank='Trade Republic'), current_balance
            if entrada:
                # New PDF format: amounts appear in ENTRADA column for both expenses and refunds.
                # Use balance direction to distinguish: balance drop → expense, balance rise → refund.
                if prev_balance is not None and current_balance is not None:
                    if current_balance < prev_balance:
                        return Transaction(date=date, description=desc, amount=entrada,
                                           tx_type='expense', bank='Trade Republic'), current_balance
                    else:
                        return Transaction(date=date, description=f'[Devolución] {desc}',
                                           amount=entrada, tx_type='income', bank='Trade Republic'), current_balance
                # Fallback: no balance data → assume expense (card transactions are usually expenses)
                return Transaction(date=date, description=desc, amount=entrada,
                                   tx_type='expense', bank='Trade Republic'), current_balance

        return None, current_balance


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


# ── Openbank PDF Parser ────────────────────────────────────────────────────────

class OpenbankPDFParser:
    """
    Parse Openbank account statement PDFs.
    Column layout (x0 boundaries):
      Fecha Operación: ~57 | Fecha Valor: ~122 | Concepto: 180–441
      Importe: 441–530 | Saldo: ≥530
    """
    X_FECHA2 = 115
    X_CONCEPTO = 175
    X_IMPORTE = 441
    X_SALDO = 528

    _AMOUNT_RE = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')
    _DATE_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    _NOISE = re.compile(r'^(EUR|Fecha|Operación|Valor|Concepto|Importe|Saldo|FIN)$', re.I)

    def parse(self, pdf_path: str) -> list[Transaction]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                words = page.extract_words()
                transactions.extend(self._parse_page(words))
        return transactions

    def _parse_page(self, words: list[dict]) -> list[Transaction]:
        # Find top-y of each transaction's fecha column (x0 ~57, DD/MM/YYYY)
        # Concepto can start ~6px BEFORE the date line, so we use date_top - 6 as block start
        date_tops = sorted({
            w['top'] for w in words
            if self._DATE_RE.match(w['text']) and 50 < w['x0'] < 90
        })
        if not date_tops:
            return []

        # Build blocks: words with top in [date_top_i - 6, date_top_{i+1} - 6)
        results = []
        for i, dt in enumerate(date_tops):
            block_start = dt - 6
            block_end = date_tops[i + 1] - 6 if i + 1 < len(date_tops) else float('inf')
            block = [w for w in words if block_start <= w['top'] < block_end]
            tx = self._parse_block(block)
            if tx:
                results.append(tx)
        return results

    def _parse_block(self, block: list[dict]) -> Optional[Transaction]:
        all_words = block

        date_words = [w for w in all_words if self._DATE_RE.match(w['text']) and w['x0'] < self.X_FECHA2]
        if not date_words:
            return None
        try:
            date = datetime.strptime(date_words[0]['text'], '%d/%m/%Y')
        except ValueError:
            return None

        importe_words = [w for w in all_words if self.X_IMPORTE <= w['x0'] < self.X_SALDO
                         and self._AMOUNT_RE.match(w['text'])]
        if not importe_words:
            return None
        try:
            amount = float(importe_words[0]['text'].replace('.', '').replace(',', '.'))
        except ValueError:
            return None

        concepto_words = [w['text'] for w in all_words
                          if self.X_CONCEPTO <= w['x0'] < self.X_IMPORTE
                          and not self._NOISE.match(w['text'])
                          and not self._DATE_RE.match(w['text'])]
        concepto = ' '.join(concepto_words).strip()
        if not concepto:
            return None

        cu = concepto.upper()

        if 'DIVERINVEST' in cu:
            if amount > 0:
                desc = 'Nómina DiverInvest' if 'NOMINA' in cu or 'NÓMINA' in cu else f'DiverInvest: {concepto}'
                return Transaction(date=date, description=desc, amount=amount,
                                   tx_type='income', bank='Openbank')

        if 'CAJERO' in cu or 'DISPOSICION' in cu:
            return Transaction(date=date, description=f'Cajero {abs(amount):.0f}€',
                               amount=abs(amount), tx_type='cash_withdrawal', bank='Openbank')

        if 'REVOLUT' in cu and amount < 0:
            return Transaction(date=date, description=concepto, amount=abs(amount),
                               tx_type='internal', bank='Openbank')

        if _is_internal(concepto):
            return Transaction(date=date, description=concepto, amount=abs(amount),
                               tx_type='internal', bank='Openbank')

        if amount < 0:
            desc = _clean_openbank_desc(concepto)
            return Transaction(date=date, description=desc, amount=abs(amount),
                               tx_type='expense', bank='Openbank')

        if amount > 0:
            if 'BIZUM DE' in cu:
                desc = re.sub(r'^BIZUM DE\s*', '', concepto, flags=re.I)
                desc = re.sub(r'\s+CONCEPTO.*', '', desc, flags=re.I).strip()
                return Transaction(date=date, description=f'Bizum de {desc}',
                                   amount=amount, tx_type='income', bank='Openbank')
            return Transaction(date=date, description=concepto, amount=amount,
                               tx_type='income', bank='Openbank')

        return None


# ── Revolut PDF Parser ─────────────────────────────────────────────────────────

class RevolutPDFParser:
    X_DATE = 46
    X_DESC = 190
    X_SALIENTE = 390
    X_ENTRANTE = 468
    X_SALDO = 525

    _AMOUNT_RE = re.compile(r'^€(\d+(?:\.\d{2})?)$')
    _DAY_RE = re.compile(r'^\d{1,2}$')

    def parse(self, pdf_path: str) -> list[Transaction]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                transactions.extend(self._parse_page(page.extract_words()))
        return transactions

    def _parse_page(self, words: list[dict]) -> list[Transaction]:
        # Use exact top values (no rounding) to avoid float comparison issues
        day_tops = sorted({
            w['top'] for w in words
            if self._DAY_RE.match(w['text']) and w['x0'] < self.X_DATE
        })
        if not day_tops:
            return []
        results = []
        for i, dt in enumerate(day_tops):
            block_end = day_tops[i + 1] if i + 1 < len(day_tops) else float('inf')
            block = [w for w in words if dt <= w['top'] < block_end]
            tx = self._parse_block(block)
            if tx:
                results.append(tx)
        return results

    def _parse_block(self, block: list[dict]) -> Optional[Transaction]:
        first_top = block[0]['top']
        first_line = [w for w in block if abs(w['top'] - first_top) < 3]

        date_words = sorted([w for w in first_line if w['x0'] < 80], key=lambda x: x['x0'])
        date = _parse_date_es(' '.join(w['text'] for w in date_words[:3]))
        if not date:
            return None

        saliente_words = [w for w in first_line if self.X_SALIENTE <= w['x0'] < self.X_ENTRANTE and self._AMOUNT_RE.match(w['text'])]
        entrante_words = [w for w in first_line if self.X_ENTRANTE <= w['x0'] < self.X_SALDO and self._AMOUNT_RE.match(w['text'])]
        saliente = float(self._AMOUNT_RE.match(saliente_words[0]['text']).group(1)) if saliente_words else None
        entrante = float(self._AMOUNT_RE.match(entrante_words[0]['text']).group(1)) if entrante_words else None

        if saliente is None and entrante is None:
            return None

        desc_words = [w['text'] for w in first_line if self.X_DESC <= w['x0'] < self.X_SALIENTE]
        desc = ' '.join(desc_words).strip()
        if not desc:
            return None

        desc_upper = desc.upper()

        if 'RECARGA DE' in desc_upper or _is_internal(desc):
            return Transaction(date=date, description=desc, amount=saliente or entrante or 0,
                               tx_type='internal', bank='Revolut')

        if 'REVOLUT DIGITAL' in desc_upper or 'TRANSFER FROM REVOLUT' in desc_upper:
            return Transaction(date=date, description=desc, amount=entrante or saliente or 0,
                               tx_type='investment', bank='Revolut')

        if saliente:
            return Transaction(date=date, description=desc, amount=saliente,
                               tx_type='expense', bank='Revolut')

        if entrante:
            return Transaction(date=date, description=desc, amount=entrante,
                               tx_type='income', bank='Revolut')

        return None
