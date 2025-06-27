import os
import logging
import httpx
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADD_TITLE, ADD_RECIPIENTS, ADD_DEADLINE, ADD_NOTE = range(4)
TEMP_TASK = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Gunakan /addtask untuk membuat tugas.")

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK[update.effective_chat.id] = {"creator_id": update.effective_user.id}
    await update.message.reply_text("Masukkan judul tugas:")
    return ADD_TITLE

async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK[update.effective_chat.id]["title"] = update.message.text
    await update.message.reply_text("Masukkan ID penerima tugas (pisahkan dengan koma jika lebih dari satu):")
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
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/tasks",
            headers=headers,
            json=payload
        )

    if r.status_code in [200, 201]:
        await update.message.reply_text("✅ Tugas berhasil ditambahkan!")
    else:
        await update.message.reply_text("❌ Gagal menambahkan tugas.")
        logger.error(r.text)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TEMP_TASK.pop(update.effective_chat.id, None)
    await update.message.reply_text("❌ Proses dibatalkan.")
    return ConversationHandler.END

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logger.info("Bot berjalan...")
    app.run_polling()
