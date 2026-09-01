"""Tests de regresión para los parsers de PDF bancarios.

Usan los extractos reales en Extractos/ (no versionados en git por ser datos
financieros personales) como fixtures. Si esa carpeta no existe en el entorno
donde se ejecutan los tests, se saltan en vez de fallar.

Cubren dos cosas:
1. Snapshot de recuento de transacciones por extracto — cualquier cambio en
   los parsers que altere cuántas transacciones se extraen debe ser explícito
   y revisado, no un efecto colateral silencioso.
2. Regresión de bugs ya arreglados (contaminación de descripciones en
   Openbank, "null" colándose en Trade Republic, transferencias internas mal
   clasificadas como gasto).
"""

from pathlib import Path

import pytest

from parsers import (
    OpenbankPDFParser,
    RevolutPDFParser,
    SantanderPDFParser,
    TradeRepublicParser,
    _is_maria_own_identity,
)

FIXTURES = Path(__file__).parent.parent / "Extractos"

TR_PDF = FIXTURES / "Extracto de cuenta (2).pdf"
OPENBANK_PDF = FIXTURES / "Movimientos de Cuenta-2.pdf"
REVOLUT_PDF = FIXTURES / "account-statement_2026-01-01_2026-03-12_es-es_0537bb (1).pdf"

requires_fixtures = pytest.mark.skipif(
    not (TR_PDF.exists() and OPENBANK_PDF.exists() and REVOLUT_PDF.exists()),
    reason="PDFs reales en Extractos/ no disponibles en este entorno",
)

MAX_SANE_DESCRIPTION_LENGTH = 150


def _max_description_length(txs) -> int:
    return max((len(t.description) for t in txs), default=0)


def _duplicated_markers(txs, markers: list[str]) -> list[str]:
    """Una descripción con el mismo marcador de transacción repetido dos veces
    es el síntoma del bug de junio 2026: bloques mal delimitados fusionando
    dos movimientos en una sola descripción."""
    bad = []
    for t in txs:
        up = t.description.upper()
        if any(up.count(m) > 1 for m in markers):
            bad.append(t.description)
    return bad


@requires_fixtures
class TestTradeRepublicParser:
    @classmethod
    def setup_class(cls):
        cls.txs = TradeRepublicParser().parse(str(TR_PDF))

    def test_parses_expected_transaction_count(self):
        assert len(self.txs) == 177

    def test_no_contaminated_descriptions(self):
        assert max((len(t.description) for t in self.txs), default=0) < MAX_SANE_DESCRIPTION_LENGTH

    def test_dates_and_amounts_valid(self):
        for t in self.txs:
            assert t.date is not None
            assert t.amount >= 0

    def test_no_trailing_null_in_description(self):
        assert not any(t.description.strip().lower().endswith("null") for t in self.txs)

    def test_no_footer_boilerplate_leaking_into_description(self):
        # Arreglado 2026-08-07: el filtro de pie de página cortaba a 60px del
        # borde inferior, pero el bloque de datos de la empresa empieza a
        # ~82.7px y se colaba en la última transacción de cada página (ver
        # parse()). Ahora el margen es 90px.
        leaked = [t for t in self.txs if "Sucursal en España" in t.description]
        assert not leaked


@requires_fixtures
class TestOpenbankPDFParser:
    @classmethod
    def setup_class(cls):
        cls.txs = OpenbankPDFParser().parse(str(OPENBANK_PDF))

    def test_parses_expected_transaction_count(self):
        assert len(self.txs) == 938

    def test_no_contaminated_descriptions(self):
        assert _max_description_length(self.txs) < MAX_SANE_DESCRIPTION_LENGTH
        assert not _duplicated_markers(
            self.txs, ["COMPRA EN", "BIZUM A FAVOR DE", "TRANSFERENCIA INMEDIATA"]
        )

    def test_dates_and_amounts_valid(self):
        for t in self.txs:
            assert t.date is not None
            assert t.amount >= 0

    def test_known_internal_transfers_not_expense(self):
        internal_like = [
            t for t in self.txs
            if "REVOLUT**" in t.description.upper()
            or "TRANSFERENCIA INMEDIATA DE PABLO CAVALLER GRAU" in t.description.upper()
        ]
        assert internal_like, "el extracto de prueba debería contener alguna de estas transferencias"
        assert all(t.tx_type == "internal" for t in internal_like)


class TestMariaOwnIdentity:
    """`_is_maria_own_identity` reconoce la contraparte 'María a sí misma' en
    cualquier banco (traspaso propio, no ingreso), tolerando los acentos rotos y
    el mojibake de los extractos reales de Santander."""

    @pytest.mark.parametrize("desc", [
        "Transferencia Inmediata De Mar A Rosalia Ruis Nchez Gonzalez Barros,",
        "Transferencia De Marãa Rosalia Ruisã¡nchez Gonzalez Barros, .",
        "Transferencia De Ruisanchez Gonzalez-barros Maria Rosalia, .",
        "Transferencia de MARIA RUISANCHEZ",
        "Transferencia A Favor De María Ruisánchez Concepto: Julio",
    ])
    def test_maria_name_variants_detected(self, desc):
        assert _is_maria_own_identity(desc)

    @pytest.mark.parametrize("desc", [
        "Transferencia De Stripe, Concepto Buencoco Slu.",
        "Transferencia De Hospital Sant Joan De Deu, .",
        "Transferencia Inmediata De Chevere J.r. Sl, Concepto Sesion Psicologia",
        "Transferencia Inmediata De Monteagudo Martinez, Victor, Concepto Terapia",
        "Transferencia Inmediata De Natalia Castillero Yuste, Concepto Enviada Desde Revolut",
        "Transferencia Inmediata De Parroquia Sant Sebastia De Verdum, Concepto Pago Ftra. M-7",
    ])
    def test_third_parties_not_detected(self, desc):
        assert not _is_maria_own_identity(desc)


class TestSantanderParserOwnTransfer:
    """Regresión: una transferencia entrante en el Santander de María cuya
    contraparte es ella misma → 'internal'; de un tercero → 'income'."""

    def _tx(self, line: str):
        return SantanderPDFParser()._parse_block([line])

    def test_incoming_from_maria_is_internal(self):
        tx = self._tx("26 ene 2026 Transferencia inmediata de MAR A ROSALIA RUIS "
                      "NCHEZ GONZALEZ BARROS 1.897,06€ 10.000,00€")
        assert tx is not None
        assert tx.bank == "Santander"
        assert tx.tx_type == "internal"
        assert tx.amount == pytest.approx(1897.06)

    def test_incoming_from_third_party_is_income(self):
        tx = self._tx("25 jul 2026 Transferencia inmediata de Chevere J.R. SL "
                      "Concepto Sesion Psicologia 70,00€ 10.070,00€")
        assert tx is not None
        assert tx.tx_type == "income"
        assert tx.amount == pytest.approx(70.0)

    def test_outgoing_still_internal(self):
        tx = self._tx("10 jul 2026 Recibo Ballester Xxi S.l. -74,78€ 9.000,00€")
        assert tx is not None
        assert tx.tx_type == "internal"


@requires_fixtures
class TestRevolutPDFParser:
    @classmethod
    def setup_class(cls):
        cls.txs = RevolutPDFParser().parse(str(REVOLUT_PDF))

    def test_parses_expected_transaction_count(self):
        assert len(self.txs) == 18

    def test_no_contaminated_descriptions(self):
        assert _max_description_length(self.txs) < MAX_SANE_DESCRIPTION_LENGTH

    def test_dates_and_amounts_valid(self):
        for t in self.txs:
            assert t.date is not None
            assert t.amount >= 0
