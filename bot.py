#!/usr/bin/env python3
"""Finance Tracker Telegram Bot"""

import os
import tempfile
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

load_dotenv()

from parsers import (
    Transaction, CATEGORIES, CATEGORY_EMOJI,
    detect_bank, TradeRepublicParser, OpenbankParser,
)
from classifier import classify_batch, classify_cash_text
from sheets import SheetsClient

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
sheets = SheetsClient(
    os.getenv('GOOGLE_CREDENTIALS_PATH'),
    os.getenv('GOOGLE_SHEET_ID'),
)


# ── Formatting helpers ─────────────────────────────────────────────────────────

def h(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def fmt_eur(amount: float) -> str:
    s = f"{amount:,.2f}€"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def summary_text(expenses: list[Transaction], bank_name: str, excluded: int) -> str:
    lines = [f"<b>📊 {h(bank_name)} — {len(expenses)} gastos clasificados</b>\n"]
    for i, tx in enumerate(expenses, 1):
        emoji = CATEGORY_EMOJI.get(tx.category, '📦')
        lines.append(
            f"{i}. {h(tx.fmt_date())}  {h(tx.description[:32])}  "
            f"<b>{h(tx.fmt_amount())}</b>  →  {emoji} {h(tx.category)}"
        )
    if excluded:
        lines.append(f"\n<i>⏭ {excluded} excluidas automáticamente (internas/inversiones/ingresos)</i>")
    lines.append("\n¿Confirmas las categorías?")
    return "\n".join(lines)


def review_text(tx: Transaction, index: int, total: int) -> str:
    return (
        f"<b>📋 Transacción {index + 1} de {total}</b>\n\n"
        f"📅 {h(tx.fmt_date())}\n"
        f"🏪 {h(tx.description)}\n"
        f"💶 <b>{h(tx.fmt_amount())}</b>\n"
        f"📂 {tx.category_label}"
    )


# ── Keyboards ──────────────────────────────────────────────────────────────────

def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar todas", callback_data="confirm_all"),
        InlineKeyboardButton("✏️ Revisar una a una", callback_data="review_all"),
    ]])


def kb_review(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ OK", callback_data=f"ok:{idx}"),
        InlineKeyboardButton("📂 Cambiar", callback_data=f"change:{idx}"),
        InlineKeyboardButton("🗑 Ignorar", callback_data=f"skip:{idx}"),
    ]])


def kb_categories(idx: int) -> InlineKeyboardMarkup:
    cats = CATEGORIES
    rows = []
    for i in range(0, len(cats), 2):
        row = []
        for cat in cats[i:i + 2]:
            emoji = CATEGORY_EMOJI.get(cat, '📦')
            row.append(InlineKeyboardButton(f"{emoji} {cat}", callback_data=f"cat:{idx}:{cat}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Volver", callback_data=f"back:{idx}")])
    return InlineKeyboardMarkup(rows)


def kb_save_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💾 Guardar todo", callback_data="save_all"),
        InlineKeyboardButton("❌ Cancelar", callback_data="cancel_all"),
    ]])


# ── Session helpers ────────────────────────────────────────────────────────────

def get_session(user_data: dict) -> dict | None:
    return user_data.get('session')


def set_session(user_data: dict, expenses: list[Transaction], bank: str, excluded: int):
    user_data['session'] = {
        'expenses': expenses,
        'bank': bank,
        'excluded': excluded,
    }


def clear_session(user_data: dict):
    user_data.pop('session', None)


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "👋 <b>Finance Bot</b>\n\n"
        "• Envíame un <b>PDF de Trade Republic</b> o <b>XLS de Openbank</b>\n"
        "• Escríbeme gastos en efectivo: <i>\"taxi 15€\"</i> o <i>\"gasté 20 en el mercado\"</i>\n\n"
        "/resumen — resumen del mes actual\n"
        "/resumen 2026-02 — resumen de un mes concreto"
    )


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    if context.args:
        try:
            year, month = map(int, context.args[0].split('-'))
        except (ValueError, AttributeError):
            await update.message.reply_text("Formato: /resumen 2026-02")
            return
    else:
        year, month = now.year, now.month

    msg = await update.message.reply_text("⏳ Consultando...")
    summary = sheets.get_monthly_summary(year, month)
    total = summary.pop('__total__', 0.0)

    if not summary:
        await msg.edit_text(f"Sin datos para {year}-{month:02d}.")
        return

    lines = [f"<b>📊 Resumen {year}-{month:02d}</b>\n"]
    for cat, amount in sorted(summary.items(), key=lambda x: -x[1]):
        emoji = CATEGORY_EMOJI.get(cat, '📦')
        pct = (amount / total * 100) if total > 0 else 0
        bar = '▓' * int(pct / 5)
        lines.append(f"{emoji} {h(cat)}: <b>{h(fmt_eur(amount))}</b>  {bar} {pct:.0f}%")
    lines.append(f"\n💰 <b>Total: {h(fmt_eur(total))}</b>")

    await msg.edit_text("\n".join(lines), parse_mode='HTML')


# ── Document handler ───────────────────────────────────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    filename = doc.file_name or ''
    mime = doc.mime_type or ''

    bank_key = detect_bank(filename)
    if bank_key == 'unknown':
        if 'pdf' in mime:
            bank_key = 'trade_republic'
        elif 'excel' in mime or 'xls' in mime or 'html' in mime:
            bank_key = 'openbank'
        else:
            await update.message.reply_text(
                "No reconozco este archivo.\n"
                "Envíame PDF de Trade Republic o XLS de Openbank."
            )
            return

    msg = await update.message.reply_text(f"⏳ Procesando {h(filename)}...")

    suffix = '.pdf' if 'pdf' in mime or 'pdf' in filename.lower() else '.xls'

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        file = await doc.get_file()
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        if bank_key == 'trade_republic':
            parser = TradeRepublicParser()
            all_txs = parser.parse(tmp_path)
            bank_name = 'Trade Republic'
        else:
            parser = OpenbankParser()
            all_txs = parser.parse(tmp_path)
            bank_name = 'Openbank'

        expenses = [tx for tx in all_txs if tx.tx_type == 'expense']
        excluded = len(all_txs) - len(expenses)

        if not expenses:
            await msg.edit_text(
                f"✅ Procesado. Sin gastos nuevos en <b>{h(filename)}</b>.\n"
                f"<i>({excluded} movimientos excluidos automáticamente)</i>",
                parse_mode='HTML',
            )
            return

        await msg.edit_text(f"🧠 Clasificando {len(expenses)} gastos con IA...")
        categories = classify_batch(expenses)
        for tx, cat in zip(expenses, categories):
            tx.category = cat

        set_session(context.user_data, expenses, bank_name, excluded)

        await msg.edit_text(
            summary_text(expenses, bank_name, excluded),
            parse_mode='HTML',
            reply_markup=kb_confirm(),
        )

    except Exception as e:
        await msg.edit_text(f"❌ Error procesando el archivo: {h(str(e))}", parse_mode='HTML')
        raise
    finally:
        os.unlink(tmp_path)


# ── Text handler (cash expenses) ───────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not any(c.isdigit() for c in text):
        return

    msg = await update.message.reply_text("⏳ Procesando...")

    try:
        amount, description, category = classify_cash_text(text)
        if amount <= 0:
            await msg.edit_text(
                "No pude identificar el importe. Ejemplo: <i>\"taxi 15€\"</i>",
                parse_mode='HTML',
            )
            return

        tx = Transaction(
            date=datetime.now(),
            description=description,
            amount=amount,
            tx_type='expense',
            bank='Efectivo',
            category=category,
        )

        set_session(context.user_data, [tx], 'Efectivo', 0)
        emoji = CATEGORY_EMOJI.get(category, '📦')

        await msg.edit_text(
            f"<b>💵 Gasto en efectivo</b>\n\n"
            f"🏪 {h(description)}\n"
            f"💶 <b>{h(fmt_eur(amount))}</b>\n"
            f"📂 {emoji} {h(category)}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirmar", callback_data="ok:0"),
                InlineKeyboardButton("📂 Cambiar", callback_data="change:0"),
                InlineKeyboardButton("🗑 Cancelar", callback_data="cancel_all"),
            ]]),
        )

    except Exception as e:
        await msg.edit_text(f"❌ Error: {h(str(e))}", parse_mode='HTML')


# ── Callback handler ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    session = get_session(context.user_data)

    # ── Confirm all (after initial summary screen) ─────────────────────────────
    if data == 'confirm_all':
        if not session:
            await query.edit_message_text("❌ Sesión expirada. Vuelve a subir el archivo.")
            return
        await _save_and_done(query, context, session['expenses'])

    # ── Save all (after one-by-one review) ────────────────────────────────────
    elif data == 'save_all':
        if not session:
            await query.edit_message_text("❌ Sesión expirada.")
            return
        await _save_and_done(query, context, session['expenses'])

    # ── Cancel ────────────────────────────────────────────────────────────────
    elif data == 'cancel_all':
        clear_session(context.user_data)
        await query.edit_message_text("❌ Cancelado. No se guardó nada.")

    # ── Start one-by-one review ───────────────────────────────────────────────
    elif data == 'review_all':
        if not session:
            await query.edit_message_text("❌ Sesión expirada.")
            return
        tx = session['expenses'][0]
        await query.edit_message_text(
            review_text(tx, 0, len(session['expenses'])),
            parse_mode='HTML',
            reply_markup=kb_review(0),
        )

    # ── Actions with index ────────────────────────────────────────────────────
    elif ':' in data:
        parts = data.split(':')
        action = parts[0]
        idx = int(parts[1])

        if not session:
            await query.edit_message_text("❌ Sesión expirada.")
            return

        expenses = session['expenses']

        if action == 'ok':
            await _advance(query, context, session, expenses, idx)

        elif action == 'skip':
            expenses.pop(idx)
            if not expenses:
                clear_session(context.user_data)
                await query.edit_message_text("✅ Listo. No quedaron transacciones.")
                return
            new_idx = min(idx, len(expenses) - 1)
            tx = expenses[new_idx]
            await query.edit_message_text(
                review_text(tx, new_idx, len(expenses)),
                parse_mode='HTML',
                reply_markup=kb_review(new_idx),
            )

        elif action == 'change':
            tx = expenses[idx]
            await query.edit_message_text(
                f"📂 <b>Elige categoría para:</b>\n{h(tx.description)}  {h(tx.fmt_amount())}",
                parse_mode='HTML',
                reply_markup=kb_categories(idx),
            )

        elif action == 'back':
            tx = expenses[idx]
            await query.edit_message_text(
                review_text(tx, idx, len(expenses)),
                parse_mode='HTML',
                reply_markup=kb_review(idx),
            )

        elif action == 'cat':
            new_cat = parts[2] if len(parts) > 2 else 'Otros'
            expenses[idx].category = new_cat
            await _advance(query, context, session, expenses, idx)


async def _advance(query, context, session, expenses, idx):
    """Move to next transaction or show final confirmation."""
    next_idx = idx + 1
    if next_idx >= len(expenses):
        # All reviewed — show final summary before saving
        total = sum(tx.amount for tx in expenses)
        lines = [f"<b>✅ Revisión completa — {len(expenses)} gastos</b>\n"]
        for tx in expenses:
            emoji = CATEGORY_EMOJI.get(tx.category, '📦')
            lines.append(f"• {h(tx.fmt_date())}  {h(tx.description[:28])}  <b>{h(tx.fmt_amount())}</b>  {emoji} {h(tx.category)}")
        lines.append(f"\n💰 <b>Total: {h(fmt_eur(total))}</b>")
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode='HTML',
            reply_markup=kb_save_cancel(),
        )
    else:
        tx = expenses[next_idx]
        await query.edit_message_text(
            review_text(tx, next_idx, len(expenses)),
            parse_mode='HTML',
            reply_markup=kb_review(next_idx),
        )


async def _save_and_done(query, context, expenses):
    """Write expenses to Google Sheets and confirm."""
    await query.edit_message_text("⏳ Guardando en Google Sheets...")
    sheets.write_transactions(expenses)
    total = sum(tx.amount for tx in expenses)
    clear_session(context.user_data)
    await query.edit_message_text(
        f"✅ <b>{len(expenses)} transacciones guardadas</b>\n"
        f"💰 Total: <b>{h(fmt_eur(total))}</b>\n\n"
        f"Usa /resumen para ver el resumen del mes.",
        parse_mode='HTML',
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('resumen', cmd_resumen))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🤖 Finance bot iniciado. Ctrl+C para parar.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
