import asyncio
import io
import logging
import os

import httpx
import qrcode
from dotenv import load_dotenv
from telegram import BotCommand, LinkPreviewOptions, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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
    ("agregar", "Guardar un paquete: /agregar <código> [alias]"),
    ("mispaquetes", "Ver el estado de todos tus paquetes guardados"),
    ("renombrar", "Cambiar el alias: /renombrar <código o alias> <alias nuevo>"),
    ("borrar", "Sacar un paquete de tu lista: /borrar <código o alias>"),
    ("compartir", "Generar un QR para invitar a alguien a este bot"),
]


def _help_text() -> str:
    lines = ["También podés mandar un código de tracking suelto para consultarlo sin guardarlo.\n", "Comandos:"]
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
    code = update.message.text.strip()
    await update.message.reply_text("Buscando...")
    try:
        _, info = await tracking.create_and_fetch(code)
    except Exception as exc:
        logger.exception("tracking failed for %s", code)
        await update.message.reply_text(_error_message(exc))
        return
    await _reply(update, info)


async def agregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /agregar <código> [alias]")
        return

    code = context.args[0]
    alias = " ".join(context.args[1:]) or None

    await update.message.reply_text("Registrando...")
    try:
        pkg_id, info = await tracking.create_and_fetch(code)
    except Exception as exc:
        logger.exception("agregar failed for %s", code)
        await update.message.reply_text(_error_message(exc))
        return

    db.add_package(update.effective_chat.id, code, pkg_id, alias)
    label = f" ({alias})" if alias else ""
    await update.message.reply_text(f"📦 Guardado{label}: {code}")
    await _reply(update, info)


async def mispaquetes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_packages(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("No tenés paquetes guardados. Usá /agregar <código> para sumar uno.")
        return

    await update.message.reply_text(f"Consultando {len(rows)} paquete(s)...")
    lines = []
    for row in rows:
        label = row["alias"] or row["tracking_code"]
        try:
            status = await tracking.fetch(row["pkg_id"], row["tracking_code"], full=False)
        except Exception:
            logger.exception("mispaquetes fetch failed for pkg_id=%s", row["pkg_id"])
            status = "⚠️ No pude consultar este paquete ahora."
        lines.append(f"📦 {label} ({row['tracking_code']})\n{status}")
        await asyncio.sleep(1)  # respect Postal Ninja's 1 req/sec rate limit

    await _reply(update, "\n\n".join(lines))


async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /borrar <código o alias>")
        return

    chat_id = update.effective_chat.id
    code = db.resolve_code(chat_id, context.args[0])
    if not code:
        await update.message.reply_text("No tenías ese código ni alias guardado.")
        return

    db.remove_package(chat_id, code)
    await update.message.reply_text(f"Borrado: {code}")


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
            continue  # first check after /agregar, nothing to notify yet

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
    app.add_handler(CommandHandler("agregar", agregar))
    app.add_handler(CommandHandler("mispaquetes", mispaquetes))
    app.add_handler(CommandHandler("renombrar", renombrar))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("compartir", compartir))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track))

    interval = int(os.environ.get("CHECK_INTERVAL_SECONDS", "86400"))
    app.job_queue.run_repeating(check_updates, interval=interval, first=interval)

    app.run_polling()


if __name__ == "__main__":
    main()
