"""Regresión de `is_renta_trabajo` (sheets.py): qué ingresos cuentan como renta
de trabajo (nómina o negocio propio) y por tanto quedan fuera de los ingresos
compensatorios de /api/summary y /api/annual.

Cobertura clave (ampliación 2026-09-01): las sesiones de psicología que los
pacientes de María pagan directamente por transferencia son negocio propio, no
ingreso puntual. No deben colarse ingresos de Pablo ni Bizums/regalos de familia.
"""

import pytest

from sheets import is_renta_trabajo

RENTA_TRABAJO = [
    # Nómina (Pablo y María)
    "TRANSFERENCIA DIVERINVEST NOMINA JULIO",
    "Transferencia De Fundacio Pro Vida De Catalunya, Concepto Nomina.",
    # Negocio / sueldo de María ya reconocidos antes de 2026-09
    "Transferencia De Stripe, Concepto Buencoco Slu.",
    "TRANSFERENCIA DE HOSPITAL SANT JOAN DE DEU,",
    "SAMARANCH GALLART datafono octubre 2025 dat",
    # Sesiones de psicología pagadas por pacientes (ampliación 2026-09-01)
    "Transferencia Inmediata De Chevere J.r. Sl, Concepto Sesion Psicologia",
    "Transferencia Inmediata De Chevere J.r. Sl, Concepto Sesion Psicologia (1/2)",
    "Transferencia Inmediata De Fernando Dominguez Navarro, Concepto Sesion 26 Agosto",
    "Transferencia Inmediata De Natalia Castillero Yuste, Concepto Enviada Desde Revolut",
    "Transferencia De Natalia Castillero Yuste, Concepto Natalia Castillero.",
    # Paciente nuevo, no en la lista de pagadores, pero concepto "Sesion"
    "Transferencia Inmediata De Miriam Blanch Moliner, Concepto Sesion 13 Julio",
    # Ampliación 2026-09-01: concepto "terapia"/"cita" gated por pagadora conocida
    "Bizum de VALERIA DUARTE RAMIREZ terapia",
    "Bizum de VALERIA D R cita",
    "Transferencia Inmediata De Monteagudo Martinez, Victor, Concepto Terapia-drama",
    "Transferencia Inmediata De Monteagudo Martinez, Victor, Concepto Terapia",
    # "ó" mal codificada como guion en "Sesi-n"
    "Transferencia De Rocio Novella Ceperuelo, Concepto Sesi-n 3 Octubre.",
]

NO_RENTA_TRABAJO = [
    # Ingresos no-sesión de María: regalos, Bizums, familia, premios, devoluciones
    "LIQUIDACION DINERARIA DE PREMIOS",
    "Bizum de Rosalia G B Cena",
    "Bizum de ANTONIO GONZALEZ BARROS BELLSOLA Regalo master",
    "TRANSFERENCIA DE IGNACIO RUISANCHEZ CAPELASTEGUI, GIMNASIO MARIA",
    "DEVOLUCION COMPRA BIZUM DRUNI 2024-12-09 PEDIDO 0280038265",
    "TRANSFERENCIA DE Mangopay, Vinted",
    # Ingreso de Pablo que contiene "sesion" pero NO es la consulta de María
    "Bizum de Carlos M R sesion de padel del sabado",
    "Transferencia De Un Cliente, Concepto Sesion De Coaching Financiero",
    # "terapia"/"cita" sin pagadora conocida: palabras demasiado comunes, no disparan
    "Bizum de Pablo G R cita con el gestor del banco",
    "Transferencia De Terapia De Pareja Centre, Concepto Sesiones",
    "DEVOLUCION / REEMBOLSO CITA MEDICO PRIVADO",
    "Bizum de Un Amigo terapia",
    "Transferencia De Una Clinica, Concepto Cita Fisioterapia",
]


@pytest.mark.parametrize("desc", RENTA_TRABAJO)
def test_es_renta_trabajo(desc):
    assert is_renta_trabajo(desc) is True


@pytest.mark.parametrize("desc", NO_RENTA_TRABAJO)
def test_no_es_renta_trabajo(desc):
    assert is_renta_trabajo(desc) is False
