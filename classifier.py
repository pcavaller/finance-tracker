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
        'MANTEQUERIA', 'ARTESANS DEL BACALLA', 'CAPRABO', 'SPAR ', 'CARREF',
        'PATES MAS', '365 BALMES', 'DISCOUNT VIA AUGUSTA', 'SANTA CRISTINA MARKET',
        'MACXIPA',
    ]),
    ('Restaurantes', [
        'LENTRECOTE', 'TOMAS DE SARRIA', 'VIPS', 'HEMINGWAY', 'RODILLA', 'CAFETERIA',
        'OCA LOCA', 'CASA PETRA', 'LAS MUNS', 'BAR LA SALA', 'BODEGON', 'DONOSTIA',
        'BURGUER', 'BURGER', 'MCDONALDS', 'MCDONALD', 'MC DONALDS', 'KFC', 'STARBUCKS', 'FOSTER',
        'BK ', 'BURGER KING',
        'RESTAURANTE', 'RESTAURANT', 'PIZZERIA', 'SUSHI', 'KEBAB', 'TAPAS',
        'GLORIA C STA', 'GATE GOURMET', 'CAFE', 'COFFEE', 'BRUNCH',
        'FORNET', 'EL FORNET', 'GRANJA', 'CHARCUTERIA',
        'VOLAPIE', 'MORRO FI', 'LA PARADA', 'BAR LA BODEGA', 'BAR DEL PI',
        'CREP NOVA', 'CREPNOVA', 'HERMOSO', 'BAKERY', 'FORN DE PA', 'HORA PUNTA',
        'BODEGA', 'MARTINS', 'CHARTER', 'ARTAL', 'RS BARKI', 'CHIQUITO',
        'FUN DIM SUM', 'ENTRECOTE', 'LES BRASES', 'TRATTORIA', 'TAGLIATELLA',
        'PISAPA', 'LA LLESCA', 'ENTREPANES', 'DON CALAMAR', 'DA NANNI',
        'FORN DE SANT', 'UPPER LOUNGE', 'BOUBONVERD', 'SANTA MARKET',
        'SECRETS BONANOVA', 'M. SAN LEOPOLDO', 'RITMO',
        'MANOLO BAKES', 'CA LA TRESA', 'BRAVAS', 'CRISBAR', 'QUINTO PINO',
        'CASAMATA', 'CERVECERIA', 'AUTOGRILL', 'EL FRONTAL', 'CAL MIDI',
        'TIO BIGOTES', 'SABOR ISTAMBUL', 'SANTA GLORIA', 'GELATERIA',
        'FRIGIDARIUM', 'AREAS ', 'TURRIS PANEM', 'SQ *LA BARRA', 'GASTRO PLANET',
        'THE GEORGE PAYNE', 'ENRIQUE TOMAS', 'APERITIVOS', 'LORETO',
        'EXPNUNEZ', 'L OBRADOR', 'PASTISSERIA', 'PIZZAS KING', 'DOMINOS',
        'BAR RTE', 'BAR OLAGUER', 'COMPTE BORELL', 'MICHELANGELO',
        'EL LORO BLANCO', 'LA PLATJA', 'PATES MAS', 'CAL MIDI',
    ]),
    ('Taxi', [
        'TAXI', 'UBER', 'CABIFY', 'BOLT', 'FREE NOW', 'FREENOW', 'MYTAXI',
    ]),
    ('Coche', [
        'GASOLINERA', 'ESTACION SERVICIO', 'ESTA. SERV', 'EE SS', 'REPSOL', 'CEPSA',
        'BP ', 'SHELL', 'GALP', 'PETROL', 'COMBUSTIBLE', 'CARBURANTE',
        'ITV', 'TALLER', 'AUTOLAVADO', 'NEUMATICO', 'AUTOPISTA', 'PEAJE',
        'JOYOSA', 'PARKING', 'IBERMOTOR', 'APARCAMIENTO', 'APARCA',
        'SABA ', 'EMPARK', 'BSM ', 'INDIGO PARK', 'ZITY',
        'MEROIL', 'BALLENOIL', 'CEDIPSA', 'TUNELSPAN', 'AP. PL.', 'AP.TANATORI',
        'MATARO PARK', 'AP. RIERA', 'TANATORI', 'ALCAMPO', 'BON AREA',
    ]),
    ('Transporte', [
        'IRYO', 'RENFE', 'CERCANIAS',
        'VUELING', 'IBERIA', 'RYANAIR', 'EASYJET', 'AENA',
        'BUS ', 'METRO ', 'TMB', 'EMT ', 'BICING', 'BLABLACAR',
        'MARCHITA', 'MARCILLA', 'TIEBAS', 'WIB ADVANCE', 'FGC',
        'TRAINLINE', 'INTERMODALIDAD', 'FREE2MOVE', 'ADR MOBILITY',
        'ATAC ', 'MYCICERO', 'MOBILITA',
    ]),
    ('Ropa/Compras', [
        'CORTEFIEL', 'EDWARDS', 'ESE O ESE', 'RENATTAGO', 'SP RENATTAGO',
        'ZARA', 'H&M', 'MANGO', 'MASSIMO DUTTI', 'PULL', 'BERSHKA', 'STRADIVARIUS',
        'AMAZON', 'WWW.AMAZON', 'AMZN ', 'EL CORTE INGLES', 'FNAC', 'MEDIA MARKT',
        'DECATHLON', 'IKEA', 'PRIMARK', 'NIKE', 'ADIDAS',
        'CHARLES TYRWHITT', 'SPRINGFIELD', 'EDWARD', 'ALIEXPRESS',
        'SINGULARU', 'WETSUIT', 'DRIM', 'NATURA ', 'SP POLO', 'SP OTOMI',
        'BONPRIX', 'MYCORNER', 'ABACUS', 'MULAYA', 'KEBI',
    ]),
    ('Cultura/Entretenimiento', [
        'LICEU', 'TEATRO', 'TEATRE', 'LIBRO', 'CASA DEL LIBRO', 'FNAC LIBRO',
        'CINEMA', 'CINE ', 'CONCERT', 'MUSEU', 'MUSEO', 'SPOTIFY', 'NETFLIX',
        'HBO', 'DISNEY', 'PRIME VIDEO', 'TWITCH', 'STEAM', 'PLAYSTATION',
        'ESQUI', 'ESQUÍ', 'MASELLA', 'SKI', 'PISTAS',
    ]),
    ('Suscripciones/Tech', [
        'CLAUDE', 'ANTHROPIC', 'SUBSCRIPTION', 'OPENAI', 'CHATGPT',
        'APPLE.COM', 'GOOGLE ONE', 'DROPBOX', 'NOTION', 'GITHUB',
        'MICROSOFT', 'ADOBE', 'FIGMA', 'SLACK', 'ZOOM', 'ICLOUD',
        'TRADINGVIEW', 'RFRANCO', 'HETZNER',
    ]),
    ('Hogar/Recibos', [
        'TRES TORRES DIR', 'RECIBO', 'CITY SPORT', 'GIMNASIO', 'GYM',
        'ENDESA', 'IBERDROLA', 'NATURGY', 'GAS NATURAL', 'AIGUES',
        'COMUNIDAD', 'SEGURO', 'MAPFRE', 'ADESLAS', 'SANITAS',
        'VODAFONE', 'MOVISTAR', 'ORANGE', 'JAZZTEL', 'MASMOVIL',
        'TINTORERIA', 'TINTORERA', 'UBEXTEL', 'FAMILY ENERGY', 'MAYA MOBILE',
        'INICIATIVES DE L', 'UTE DEVAS',
    ]),
    ('Bizum', [
        'BIZUM →', 'BIZUM A FAVOR',
    ]),
    ('Apuestas', [
        'BETFAIR', 'BWIN', 'CODERE', 'SPORTIUM', 'BET365', 'POKERSTARS',
        'RETABET', 'CASUMO', 'INTERWETTEN', 'KIROLBET', 'SAINTPAY',
    ]),
    ('Clubs', [
        'YOUTH ECONOMIC CIRCLE', 'REAL CLUB DE POLO', 'CLUB DE POLO',
        'CLUB DE TENIS', 'R.CLUB', 'SOC.COOP.CULTURE',
    ]),
    ('Formación', [
        'SOUL COLLEGE', 'COURSERA', 'UDEMY', 'MASTERCLASS', 'STOYNOV', 'CFA ',
        'JOBLEADS', 'VIARO', 'COL.LEGI', 'ESCOLA ',
    ]),
    ('Regalos', [
        'FLORES', 'FLORISTERIA', 'FLORESADOMICILIO', 'RAMO',
        'JAUME Y NINA', 'NACHO SANCHEZ Y MARISOL', 'NACHO SÁNCHEZ Y MARISOL',
    ]),
    ('Viajes', [
        'HOTEL', 'HOSTAL', 'AIRBNB', 'BOOKING', 'ROC BLANC',
        'AEROPORT', 'AIRPORT', 'AEROPUERTO', 'AÉROPORT', 'AEROP',
    ]),
    ('Impuestos', [
        'IRPF', 'IMPUESTO', 'HACIENDA', 'AGENCIA TRIBUTARIA',
    ]),
    ('Salud', [
        'FARMACIA', 'CLINICA', 'DOCTOR', 'DENTAL', 'OPTICA', 'FISIO',
        'HOSPITAL', 'MEDICO', 'LABORATORIO', 'BLOOM', 'DR. ', 'DR.',
    ]),
]


_custom_rules: list[tuple[str, str]] = []
_learned: dict[str, str] = {}


def load_custom_rules(sheets_client) -> None:
    """Load user-defined rules and historical classifications from Google Sheets."""
    global _custom_rules, _learned
    _custom_rules = sheets_client.get_custom_rules()
    _learned = sheets_client.get_learned_classifications()


def _classify_description(description: str) -> str:
    desc_upper = description.upper()
    # 1. Custom rules (from Sheets) — highest priority
    for keyword, category in _custom_rules:
        if keyword in desc_upper:
            return category
    # 2. Static keyword rules
    for category, keywords in RULES:
        if any(kw.upper() in desc_upper for kw in keywords):
            return category
    # 3. Historical learning — exact match on description
    if desc_upper in _learned:
        return _learned[desc_upper]
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
