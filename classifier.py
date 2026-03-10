from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from parsers import Transaction

from parsers import CATEGORIES

# ── Keyword rules per category ─────────────────────────────────────────────────

RULES: list[tuple[str, list[str]]] = [
    ('Alimentación', [
        'MERCADONA', 'BON PREU', 'GUISSONA', 'AMETLLER', 'GRANIER', 'CORTE INGLES-SUPERMERC',
        'SANTAGLORIA', 'CHURRERIA', 'FORNERIA', 'BONANOVA 96', 'ALIMENTACIO', 'SUPERMERCAT',
        'SUPERMERCADO', 'LIDL', 'ALDI', 'CARREFOUR', 'EROSKI', 'CONSUM', 'DIA ',
        'EL CORTE INGLES-SUPER', 'CONDIS', 'SUPERCOR', 'FROIZ', 'MERCADO',
    ]),
    ('Restaurantes', [
        'LENTRECOTE', 'TOMAS DE SARRIA', 'VIPS', 'HEMINGWAY', 'RODILLA', 'CAFETERIA',
        'OCA LOCA', 'CASA PETRA', 'LAS MUNS', 'BAR LA SALA', 'BODEGON', 'DONOSTIA',
        'BURGUER', 'BURGER', 'MCDONALDS', 'MCDONALD', 'KFC', 'STARBUCKS', 'FOSTER',
        'RESTAURANTE', 'RESTAURANT', 'PIZZERIA', 'SUSHI', 'KEBAB', 'TAPAS',
        'GLORIA C STA', 'GATE GOURMET', 'CAFE', 'COFFEE', 'BRUNCH',
        'FORNET', 'EL FORNET', 'GRANJA', 'CHARCUTERIA',
    ]),
    ('Transporte', [
        'IRYO', 'RENFE', 'CERCANIAS', 'UBER', 'JOYOSA', 'GASOLINERA', 'ESTACION SERVICIO',
        'ESTA. SERV', 'REPSOL', 'CEPSA', 'BP ', 'SHELL', 'GALP', 'CABIFY',
        'VUELING', 'IBERIA', 'RYANAIR', 'EASYJET', 'AENA', 'PEAJE', 'AUTOPISTA',
        'TAXI', 'BUS ', 'METRO ', 'TMB', 'EMT ', 'BICING', 'BLABLACAR',
        'MARCHITA', 'MARCILLA', 'TIEBAS',
    ]),
    ('Ropa/Compras', [
        'CORTEFIEL', 'EDWARDS', 'ESE O ESE', 'RENATTAGO', 'SP RENATTAGO',
        'ZARA', 'H&M', 'MANGO', 'MASSIMO DUTTI', 'PULL', 'BERSHKA', 'STRADIVARIUS',
        'AMAZON', 'WWW.AMAZON', 'EL CORTE INGLES', 'FNAC', 'MEDIA MARKT',
        'DECATHLON', 'IKEA', 'PRIMARK', 'NIKE', 'ADIDAS',
    ]),
    ('Cultura/Entretenimiento', [
        'LICEU', 'TEATRO', 'TEATRE', 'LIBRO', 'CASA DEL LIBRO', 'FNAC LIBRO',
        'CINEMA', 'CINE ', 'CONCERT', 'MUSEU', 'MUSEO', 'SPOTIFY', 'NETFLIX',
        'HBO', 'DISNEY', 'PRIME VIDEO', 'TWITCH', 'STEAM', 'PLAYSTATION',
    ]),
    ('Suscripciones/Tech', [
        'CLAUDE', 'ANTHROPIC', 'SUBSCRIPTION', 'OPENAI', 'CHATGPT',
        'APPLE.COM', 'GOOGLE ONE', 'DROPBOX', 'NOTION', 'GITHUB',
        'MICROSOFT', 'ADOBE', 'FIGMA', 'SLACK', 'ZOOM', 'ICLOUD',
    ]),
    ('Parking', [
        'PARKING', 'IBERMOTOR', 'TANATORI', 'APARCAMIENTO', 'APARCA',
        'SABA ', 'EMPARK', 'BSM ', 'INDIGO PARK',
    ]),
    ('Hogar/Recibos', [
        'TRES TORRES DIR', 'RECIBO', 'CITY SPORT', 'GIMNASIO', 'GYM',
        'ENDESA', 'IBERDROLA', 'NATURGY', 'GAS NATURAL', 'AIGUES',
        'COMUNIDAD', 'SEGURO', 'MAPFRE', 'ADESLAS', 'SANITAS',
        'VODAFONE', 'MOVISTAR', 'ORANGE', 'JAZZTEL', 'MASMOVIL',
    ]),
    ('Salud', [
        'FARMACIA', 'CLINICA', 'DOCTOR', 'DENTAL', 'OPTICA', 'FISIO',
        'HOSPITAL', 'MEDICO', 'LABORATORIO', 'BLOOM DE SARRIA',
    ]),
]


def _classify_description(description: str) -> str:
    desc_upper = description.upper()
    for category, keywords in RULES:
        if any(kw.upper() in desc_upper for kw in keywords):
            return category
    return 'Otros'


def classify_batch(transactions: list[Transaction]) -> list[str]:
    return [_classify_description(tx.description) for tx in transactions]


def classify_cash_text(text: str) -> tuple[float, str, str]:
    """Parse a natural language cash expense. Returns (amount, description, category)."""
    # Extract amount
    m = re.search(r'(\d+(?:[.,]\d{1,2})?)\s*(?:€|euros?|eur)?', text, re.IGNORECASE)
    amount = float(m.group(1).replace(',', '.')) if m else 0.0

    # Clean description: remove amount and common filler words
    desc = text
    if m:
        desc = text[:m.start()].strip() + ' ' + text[m.end():].strip()
    desc = re.sub(r'\b(gasté|gaste|pagué|pague|en|de|por|el|la|los|las|un|una)\b', '', desc, flags=re.IGNORECASE)
    desc = ' '.join(desc.split()).strip() or text

    category = _classify_description(desc)

    return amount, desc, category
