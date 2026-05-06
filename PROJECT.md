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
- `parsers.py` — parsers PDF: Trade Republic, Openbank, Revolut
- `classifier.py` — clasificación keywords + reglas custom en Sheets
- `sheets.py` — cliente Google Sheets (gspread, UNFORMATTED_VALUE)
- `webapp.py` — FastAPI para Mini App web
- `static/index.html` — SPA Alpine.js + Tailwind + Chart.js + Telegram WebApp SDK

## Titulares

- Pablo: `Pablo Cavaller` (nunca `Pablo` a secas)
- María: `María Ruisánchez` — normalizar siempre, variantes conocidas: "María", "Meri", "María Ruiz Sánchez", "María Ruisanchez"
- Preguntar qué titular corresponde a cada PDF antes de subir

## Comandos

```bash
# Reiniciar bot
pkill -f bot.py && python3 /Users/pablocavallergrau/finance-bot/bot.py &
```

## Estado

Funcional. Bot lee PDFs de TR/Openbank/Revolut, clasifica, guarda en Sheets. Mini App desplegada en Render. Datos importados: 818 transacciones 2024–2026 de Pablo.

## Reglas operativas

- Nómina DiverInvest excluida del cálculo de ingresos compensatorios
- Parking fusionado en Coche (categoría eliminada)
- Santander (María Ruisánchez): todo income → `income` excluido del tracking de gastos; todo outgoing → `internal` (nunca `expense`). Implementado en `SantanderPDFParser._parse_block` (parsers.py).
