# Finance Bot — Gestor de Gastos Personal

## Directorios y repos

- **Local:** `/Users/pablocavallergrau/finance-bot/`
- **GitHub:** `https://github.com/pcavaller/finance-tracker` (público)
- **Web app (Render):** `https://finance-tracker-l4tn.onrender.com`
- **Google Sheet ID:** en `.env` — sheet "Transacciones"

## Columnas Google Sheets

Fecha, Descripción, Importe, Categoría, Banco, Titular, Mes, Tipo

## Archivos clave

- `bot.py` — bot Telegram (python-telegram-bot v20)
- `parsers.py` — parsers PDF: Trade Republic, Openbank, Revolut, Santander; parser xlsx: BBVA
- `classifier.py` — clasificación keywords + reglas custom en Sheets
- `sheets.py` — cliente Google Sheets (gspread, UNFORMATTED_VALUE)
- `webapp.py` — FastAPI para Mini App web
- `static/index.html` — SPA Alpine.js + Tailwind + Chart.js + Telegram WebApp SDK

## Titulares

- Pablo: `Pablo Cavaller` (nunca `Pablo` a secas)
- María: `María Ruisánchez` — normalizar siempre, variantes conocidas: "María", "Meri", "María Ruiz Sánchez", "María Ruisanchez"
- Conjunta (Pablo + María, cuenta BBVA de la hipoteca): `Conjunta` — añadido 2026-08-01
- Preguntar qué titular corresponde a cada PDF antes de subir

## Comandos

```bash
# Reiniciar bot
pkill -f bot.py && python3 /Users/pablocavallergrau/finance-bot/bot.py &
```

## Estado

Funcional. Bot lee PDFs de TR/Openbank/Revolut y xlsx de BBVA, clasifica, guarda en Sheets. Mini App desplegada en Render. Datos importados: 818 transacciones 2024–2026 de Pablo + 93 de julio 2026 (BBVA Conjunta/Pablo, Openbank, Trade Republic — compra de vivienda, 2026-08-01).

## Reglas operativas

- Nómina DiverInvest excluida del cálculo de ingresos compensatorios
- Parking fusionado en Coche (categoría eliminada)
- Santander (María Ruisánchez): todo income → `income` excluido del tracking de gastos; todo outgoing → `internal` (nunca `expense`). Implementado en `SantanderPDFParser._parse_block` (parsers.py).
- Trade Republic: Interest payment / Cash Dividend / Cash reward allocation → `investment`, nunca `income` (son rendimiento de inversión, no ingreso líquido). Implementado en `TradeRepublicParser._parse_block` (parsers.py). Fix aplicado retroactivamente a filas históricas (2026-07-10).

### BBVA (hipoteca, desde 2026-08-01)

Pablo y María compraron vivienda con hipoteca; dos cuentas BBVA (banco nuevo): **Conjunta** (titular `Conjunta`) y la **personal de Pablo** (titular `Pablo Cavaller`, misma cuenta que el resto de sus extractos). Un único parser sirve para ambas — `BBVAParser` (parsers.py), xlsx con openpyxl, hoja "Informe BBVA", cabecera localizada dinámicamente (no asume fila 5 fija). `detect_bank()` reconoce el patrón de nombre de archivo de la app de BBVA (`"... - Últimos movimientos.xlsx"`), que no lleva "bbva" en el nombre. Banco se guarda siempre como `BBVA`; el titular (Conjunta vs. Pablo Cavaller) se decide fuera del parser, igual que con el resto de bancos.

- **Transferencias internas** (Tipo `internal`, nunca income/expense): cualquier fila con Concepto `Transferencia recibida` / `Transferencia realizada` / `Traspaso desde cuenta` / `Traspaso a cuenta` cuya contraparte (Movimiento u Observaciones) sea una identidad propia conocida. Lista en `_BBVA_OWN_IDENTITIES` (parsers.py): `PABLO CAVALLER GRAU`, `PABLO CAVALLER`, `PABLO BBVA`, `MARIA RUISANCHEZ`, `RUISANCHEZ GONZALEZ-BARROS`, `BBVA MERI`, `MERI`, `MYINVESTO`, `MYINVESTOR`, `ACTIVAR` — más un match laxo para María (contiene "RUISANCHEZ" y "MARIA" aunque no case ninguna variante exacta, p.ej. nombre completo truncado distinto) y reutilización de `OWN_ACCOUNT_KEYWORDS` (Trade Republic, Sabadell...) ya usado por el resto de parsers. Implementado en `_is_bbva_known_counterparty` (parsers.py).
- **Compra de vivienda** (Tipo nuevo `patrimonio`, Categoría `Compra vivienda`): evento puntual de cierre — abono del préstamo, adeudos de seguros, cheques bancarios y tasación. Conceptos exactos en `_BBVA_VIVIENDA_CONCEPTS` / `_BBVA_VIVIENDA_TASACION_PREFIX` (parsers.py). `patrimonio` es paralelo a `internal`/`investment`: queda fuera de `SUM Importe donde Tipo=income` y `SUM ABS(Importe) donde Tipo=expense` en cualquier agregación — no requirió tocar `webapp.py`/`sheets.py`, ya excluyen implícitamente cualquier Tipo que no sea `expense`/`income`.
- **Hipoteca** (Categoría `Hipoteca`, Tipo `expense` normal — SÍ cuenta en cash flow): coste recurrente mensual (de momento solo intereses; cuando empiece a cobrarse la cuota completa, mismo tratamiento). Concepto exacto en `_BBVA_HIPOTECA_CONCEPT` (parsers.py).
- **Duplicados legítimos con misma fecha/concepto/importe** (p.ej. los dos cheques de -234.000€ del cierre, o dos transferencias idénticas a Trade Republic el mismo día): el dedupe de `SheetsClient.write_transactions` los trataría como el mismo duplicado y descartaría el segundo. `disambiguate_duplicates()` (parsers.py) añade sufijo `(i/n)` a la descripción antes de escribir cuando detecta colisión en el batch — usarlo siempre en imports que puedan traer movimientos idénticos genuinamente distintos.
- **Préstamos familiares que financian la vivienda (padre de María, 90.000€ en dos tramos: 50.000€ junio + 40.000€ julio) NO son transferencias internas**, aunque el nombre de contraparte en el extracto coincida con una identidad propia conocida (p.ej. el tramo de julio llegó etiquetado "RUISANCHEZ GONZALEZ-BARROS MA" y el match laxo de María lo habría marcado `internal` por defecto — corregido a mano el 2026-08-01: Tipo `patrimonio`, Categoría `Compra vivienda`, descripción reescrita para dejar constancia de que es deuda con el padre, no dinero propio moviéndose). Antes de aceptar el match laxo de María en `_is_bbva_known_counterparty` para importes grandes y redondos (>10.000€) en fechas de cierre de compra, preguntar a Pablo si es dinero propio o un préstamo familiar — no asumir. El tramo de junio (50.000€ + 2.000€ propios de la señal/arras) se pagó desde la cuenta Openbank de María, ya registrado en esa cuenta, no en BBVA.
- Import de julio 2026 (compra de vivienda + extractos del mes): `import_bbva_agosto2026.py` — plantilla para futuros imports puntuales de varios ficheros con distinto titular a la vez.
- Gestoría/otros gastos de cierre pendientes (~28.520,54€, aún no cargados a fecha 2026-08-01): no importar hasta que aparezcan como cargo real en un extracto — no crear filas estimadas/pendientes.

## Pendiente: cash flow mensual (Pablo + María)

Agregación sobre datos ya limpios, no requiere captura nueva.

- **Ingresos** = `SUM(Importe)` donde `Tipo="income"` (aquí SÍ cuenta la nómina DiverInvest — la exclusión de "ingresos compensatorios" es para otro cálculo, no aplica al cash flow). **Gastos** = `SUM(ABS(Importe))` donde `Tipo="expense"` (incluye ahora "Hipoteca" como gasto recurrente), pivotado por Categoría y por Titular. `internal`, `investment` y `patrimonio` (compra de vivienda) fuera de ambos lados — patrimonio puede mostrarse como línea informativa aparte, es un evento puntual, no cash flow recurrente. Ahorro neto = ingresos − gastos; tasa = ahorro/ingresos.
- **Unidad familiar como número principal**, desglose Pablo/María como columnas secundarias — imputar gastos compartidos por titular es arbitrario y no aporta a la decisión que importa (libertad financiera del hogar). El desglose por titular es solo para detectar asimetrías.
- **Formato con menos código posible**: nueva pestaña "Cashflow" en el mismo Sheet con `SUMIFS` + tabla dinámica por Categoría — cero Python, se actualiza sola con cada import del bot. Comando `/cashflow` en `bot.py` solo si luego se quiere consultar desde el móvil (no empezar por ahí).
- **Chequeo de sanidad antes de confiar en el número del mes** (precedente: hubo gastos sin categorizar y una renta de María sin importar): cero filas con `Categoría` vacía; recurrentes esperados presentes (nómina, alquiler de María, suscripciones fijas); conteo de filas del mes vs. media de 6 meses previos (desviación >40% → revisar imports); ningún valor de `Tipo`/`Titular` fuera de los conocidos.
- Si un mes da tasa de ahorro negativa o >80%: verificar los 4 chequeos antes de reportarlo; si pasan y el número sigue raro, mostrarlo a Pablo con las filas soporte, no "corregirlo" solo.
