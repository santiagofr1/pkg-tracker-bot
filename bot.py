from __future__ import annotations

import asyncio
import io
import logging
import os
import sqlite3

import httpx
import qrcode
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
from tracking import TrackingClient

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tracking = TrackingClient(api_key=os.environ["POSTAL_NINJA_API_KEY"])

TELEGRAM_MAX_LEN = 4000

# Shown in the Telegram "☰" command menu — /start and /help are omitted since
# they're how you'd get here in the first place, not something to pick from the menu.
MENU_COMMANDS = [
    ("mispaquetes", "Ver tus paquetes guardados, actualizarlos o borrarlos"),
    ("renombrar", "Cambiar el alias: /renombrar <código o alias> <alias nuevo>"),
    ("compartir", "Generar un QR para invitar a alguien a este bot"),
]


def _help_text() -> str:
    lines = [
        "Mandame un código de tracking y te muestro el estado. Si el paquete existe, "
        "te pregunto si lo querés guardar.\n",
        "En /mispaquetes cada paquete tiene botones para actualizar su estado o borrarlo.\n",
        "Comandos:",
    ]
    lines += [f"/{cmd} — {desc}" for cmd, desc in MENU_COMMANDS]
    return "\n".join(lines)


def _chunk_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_len:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _reply(update: Update, text: str):
    for chunk in _chunk_message(text):
        await update.message.reply_text(chunk, link_preview_options=LinkPreviewOptions(is_disabled=True))


def _error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return "⏳ Se agotó la cuota diaria/mensual de la API de tracking. Probá de nuevo más tarde."
    return "No pude rastrear ese código, revisá que esté bien escrito."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"¡Hola! Te ayudo a rastrear paquetes.\n\n{_help_text()}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_help_text())


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if context.user_data.get("awaiting_alias"):
        await _save_pending(update, context, alias=text)
        return

    code = text
    await update.message.reply_text("Buscando...")
    try:
        pkg_id, info = await tracking.create_and_fetch(code)
    except Exception as exc:
        logger.exception("tracking failed for %s", code)
        await update.message.reply_text(_error_message(exc))
        return

    context.user_data["pending_pkg"] = {"code": code, "pkg_id": pkg_id}
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Sí", callback_data="add_yes"),
          InlineKeyboardButton("❌ No", callback_data="add_no")]]
    )
    await _reply(update, info)
    await update.message.reply_text("¿Querés guardar este paquete?", reply_markup=keyboard)


async def _save_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, alias: str | None):
    pending = context.user_data.pop("pending_pkg", None)
    context.user_data.pop("awaiting_alias", None)
    if not pending:
        await update.message.reply_text("Esa consulta ya venció, mandá el código de nuevo.")
        return

    db.add_package(update.effective_chat.id, pending["code"], pending["pkg_id"], alias)
    label = f" ({alias})" if alias else ""
    await update.message.reply_text(f"📦 Guardado{label}: {pending['code']}")


async def add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_no":
        context.user_data.pop("pending_pkg", None)
        context.user_data.pop("awaiting_alias", None)
        await query.edit_message_text("Ok, no lo guardé.")
        return

    if query.data == "add_yes":
        if not context.user_data.get("pending_pkg"):
            await query.edit_message_text("Esa consulta ya venció, mandá el código de nuevo.")
            return
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏷️ Con alias", callback_data="alias_yes"),
              InlineKeyboardButton("📦 Sin alias", callback_data="alias_no")]]
        )
        await query.edit_message_text("¿Le querés poner un alias?", reply_markup=keyboard)
        return

    if query.data == "alias_no":
        pending = context.user_data.pop("pending_pkg", None)
        context.user_data.pop("awaiting_alias", None)
        if not pending:
            await query.edit_message_text("Esa consulta ya venció, mandá el código de nuevo.")
            return
        db.add_package(update.effective_chat.id, pending["code"], pending["pkg_id"], None)
        await query.edit_message_text(f"📦 Guardado: {pending['code']}")
        return

    if query.data == "alias_yes":
        if not context.user_data.get("pending_pkg"):
            await query.edit_message_text("Esa consulta ya venció, mandá el código de nuevo.")
            return
        context.user_data["awaiting_alias"] = True
        await query.edit_message_text("Escribí el alias que querés usar:")
        return


def _package_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Actualizar", callback_data=f"refresh:{code}"),
          InlineKeyboardButton("🗑️ Borrar", callback_data=f"del:{code}")]]
    )


def _package_text(row: sqlite3.Row) -> str:
    label = row["alias"] or row["tracking_code"]
    status = row["last_status"] or "Sin estado todavía, tocá actualizar."
    return f"📦 {label} ({row['tracking_code']})\n{status}"


async def mispaquetes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_packages(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No tenés paquetes guardados. Mandame un código de tracking para sumar uno.")
        return

    for row in rows:
        await update.message.reply_text(_package_text(row), reply_markup=_package_keyboard(row["tracking_code"]))


async def package_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, _, code = query.data.partition(":")
    chat_id = update.effective_chat.id

    if action == "del":
        db.remove_package(chat_id, code)
        await query.edit_message_text(f"🗑️ Borrado: {code}")
        await query.answer("Borrado")
        return

    row = db.get_package(chat_id, code)
    if not row:
        await query.answer("Ese paquete ya no está guardado.", show_alert=True)
        return

    try:
        status = await tracking.fetch(row["pkg_id"], row["tracking_code"], full=False)
    except Exception as exc:
        logger.exception("refresh failed for %s", code)
        await query.answer(_error_message(exc), show_alert=True)
        return

    db.update_last_status(chat_id, code, status)
    row = db.get_package(chat_id, code)
    await query.edit_message_text(_package_text(row), reply_markup=_package_keyboard(code))
    await query.answer("Actualizado")


async def renombrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /renombrar <código o alias> <alias nuevo>")
        return

    chat_id = update.effective_chat.id
    code = db.resolve_code(chat_id, context.args[0])
    if not code:
        await update.message.reply_text("No tenías ese código ni alias guardado.")
        return

    new_alias = " ".join(context.args[1:])
    db.update_alias(chat_id, code, new_alias)
    await update.message.reply_text(f"Listo, {code} ahora es «{new_alias}»")


async def compartir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}"

    qr = qrcode.make(link)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    buf.seek(0)

    await update.message.reply_photo(
        photo=buf,
        caption=f"📦 Escaneá para hablar con este bot:\n{link}",
    )


async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    for row in db.list_all_packages():
        if row["last_status"] and row["last_status"].startswith("✅"):
            continue  # already delivered, no point burning quota on it

        try:
            status = await tracking.fetch(row["pkg_id"], row["tracking_code"], full=False)
        except Exception:
            logger.exception("check_updates fetch failed for %s", row["tracking_code"])
            continue

        if status == row["last_status"]:
            continue

        db.update_last_status(row["chat_id"], row["tracking_code"], status)
        if row["last_status"] is None:
            continue  # first check right after saving, nothing to notify yet

        label = row["alias"] or row["tracking_code"]
        try:
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=f"🔔 Novedad en {label} ({row['tracking_code']}):\n{status}",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            logger.exception("failed to notify chat_id=%s", row["chat_id"])

        await asyncio.sleep(1)  # respect Postal Ninja's 1 req/sec rate limit


async def _post_init(app: Application):
    await app.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in MENU_COMMANDS])


def main():
    db.init_db()
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mispaquetes", mispaquetes))
    app.add_handler(CommandHandler("renombrar", renombrar))
    app.add_handler(CommandHandler("compartir", compartir))
    app.add_handler(CallbackQueryHandler(add_callback, pattern="^(add_yes|add_no|alias_yes|alias_no)$"))
    app.add_handler(CallbackQueryHandler(package_callback, pattern="^(refresh|del):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track))

    interval = int(os.environ.get("CHECK_INTERVAL_SECONDS", "86400"))
    app.job_queue.run_repeating(check_updates, interval=interval, first=interval)

    app.run_polling()


if __name__ == "__main__":
    main()
