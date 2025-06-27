import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,   # ✅ Tambah ini
    ConversationHandler,
    CallbackContext,
)
from telegram.ext import ApplicationBuilder
from telegram.constants import ParseMode
from flask import Flask, request
import httpx

# Load .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for webhook
app = Flask(__name__)
telegram_app = None  # global reference

# State untuk ConversationHandler
ADD_TITLE, ADD_RECIPIENTS, ADD_DEADLINE, ADD_NOTE = range(4)
TEMP_TASK = {}

# Command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Gunakan /addtask untuk membuat tugas.")

# Mulai tambah tugas
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK[update.effective_chat.id] = {"creator_id": update.effective_user.id}
    await update.message.reply_text("Masukkan judul tugas:")
    return ADD_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK[update.effective_chat.id]["title"] = update.message.text
    await update.message.reply_text("Masukkan ID penerima tugas (pisahkan dengan koma):")
    return ADD_RECIPIENTS

async def add_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ids = [x.strip() for x in update.message.text.split(",")]
    TEMP_TASK[update.effective_chat.id]["recipients"] = ids
    await update.message.reply_text("Masukkan deadline (format: YYYY-MM-DD HH:MM):")
    return ADD_DEADLINE

async def add_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dt = datetime.strptime(update.message.text, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Format salah. Ulangi: YYYY-MM-DD HH:MM")
        return ADD_DEADLINE
    TEMP_TASK[update.effective_chat.id]["deadline"] = dt.isoformat()
    await update.message.reply_text("Masukkan catatan (boleh kosong):")
    return ADD_NOTE

async def add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK[update.effective_chat.id]["note"] = update.message.text
    task = TEMP_TASK.pop(update.effective_chat.id)

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "creator_id": task["creator_id"],
        "title": task["title"],
        "recipients": task["recipients"],
        "deadline": task["deadline"],
        "note": task["note"]
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=headers, json=payload)

    if res.status_code in [200, 201]:
        await update.message.reply_text("✅ Tugas berhasil ditambahkan.")
    else:
        await update.message.reply_text("❌ Gagal menambahkan tugas.")
        logger.error(res.text)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK.pop(update.effective_chat.id, None)
    await update.message.reply_text("❌ Proses dibatalkan.")
    return ConversationHandler.END

# Flask route untuk webhook
@app.route("/webhook", methods=["POST"])
def webhook_handler():
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        telegram_app.update_queue.put_nowait(update)
    return "OK"

# Main async
async def run_bot():
    global telegram_app

    telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handler
    conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", add_task_start)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recipients)],
            ADD_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deadline)],
            ADD_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(conv)

    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    await telegram_app.start()
    logger.info("Bot webhook aktif")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
