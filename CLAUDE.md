# finance-bot

Bot de Telegram para gestión de gastos personales. Parsea PDFs bancarios, clasifica transacciones y las guarda en Google Sheets. Incluye Mini App web.

## Arranque

```bash
pkill -f bot.py; python3 bot.py &   # siempre reiniciar tras cambios
python3 webapp.py                    # FastAPI Mini App (puerto 8000)
```

## Archivos clave

- `bot.py` — bot Telegram (python-telegram-bot v20), entry point principal
- `parsers.py` — parsers PDF: Trade Republic, Openbank, Revolut
- `classifier.py` — clasificación por keywords + reglas custom desde Sheets
- `sheets.py` — cliente Google Sheets (gspread, UNFORMATTED_VALUE)
- `webapp.py` — FastAPI para la Mini App web
- `static/index.html` — SPA Alpine.js + Tailwind + Chart.js + Telegram WebApp SDK

## Google Sheet

Hoja "Transacciones": columnas Fecha, Descripción, Importe, Categoría, Banco, Titular, Mes, Tipo.
Credenciales en `.env`. Usar siempre `UNFORMATTED_VALUE` para leer números.

## Convenciones

- Nómina DiverInvest excluida del cálculo de ingresos compensatorios
- Parking fusionado en categoría "Coche" (no existe como categoría separada)
- Deploy en Render — `render.yaml` define el servicio

## Estado

Funcional. 818+ transacciones importadas (2024–2026).
