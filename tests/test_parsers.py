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

from parsers import OpenbankPDFParser, RevolutPDFParser, TradeRepublicParser

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
        assert len(self.txs) == 174

    def test_no_contaminated_descriptions(self):
        # El pie de página legal ("Sucursal en España...") se cuela en algunas
        # descripciones cerca del fin de página (ver test xfail más abajo) —
        # eso es un bug distinto al que este test vigila (bloques de dos
        # transacciones fusionados en una descripción), así que se descarta
        # aquí para no enmascarar ninguno de los dos.
        cleaned = [d.split("Sucursal en España")[0] for d in (t.description for t in self.txs)]
        assert max((len(d) for d in cleaned), default=0) < MAX_SANE_DESCRIPTION_LENGTH

    def test_dates_and_amounts_valid(self):
        for t in self.txs:
            assert t.date is not None
            assert t.amount >= 0

    def test_no_trailing_null_in_description(self):
        assert not any(t.description.strip().lower().endswith("null") for t in self.txs)

    @pytest.mark.xfail(
        reason="Bug conocido sin arreglar: el pie de página legal se cuela en "
        "descripciones de Buy/Sell/Savings plan cerca del fin de página "
        "(descubierto 2026-07-04, fuera del alcance del refactor actual).",
        strict=True,
    )
    def test_no_footer_boilerplate_leaking_into_description(self):
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
