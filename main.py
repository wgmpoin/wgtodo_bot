import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from dotenv import load_dotenv

# Load config
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PORT = int(os.getenv("PORT", 10000))

# Flask setup (for Render health check)
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return "Bot is running 🚀"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =====================
# BOT FUNCTIONALITY
# =====================
# Fake DB (replace with real DB in production)
users_db = {ADMIN_ID: "admin"}
tasks_db = []

# States for conversation
TASK_TITLE, TASK_DESC = range(2)

# ADMIN COMMANDS
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu siapa? Gak boleh pake ini!")
        return
    
    try:
        user_id = int(context.args[0])
        users_db[user_id] = "user"
        await update.message.reply_text(f"User {user_id} ditambahkan!")
    except:
        await update.message.reply_text("Goblok! Format: /adduser [ID]")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Dihh mau ngapain?")
        return
    
    user_list = "\n".join([f"{uid}: {name}" for uid, name in users_db.items()])
    await update.message.reply_text(f"Daftar User:\n{user_list}")

# TASK MANAGEMENT
async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in users_db:
        await update.message.reply_text("Lu belum terdaftar cuk!")
        return ConversationHandler.END
    
    await update.message.reply_text("Kasih judul tasknya:")
    return TASK_TITLE

async def process_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['task_title'] = update.message.text
    await update.message.reply_text("Sekarang deskripsinya:")
    return TASK_DESC

async def process_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks_db.append({
        'title': context.user_data['task_title'],
        'desc': update.message.text,
        'creator': update.effective_user.id
    })
    await update.message.reply_text("Task berhasil ditambahkan!")
    return ConversationHandler.END

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not tasks_db:
        await update.message.reply_text("Gak ada task nih!")
        return
    
    tasks_list = "\n\n".join(
        f"📌 {task['title']}\n{task['desc']}\n(by: {task['creator']})" 
        for task in tasks_db
    )
    await update.message.reply_text(f"Daftar Task:\n{tasks_list}")

# MAIN BOT SETUP
def setup_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin commands
    app.add_handler(CommandHandler("adduser", add_user))
    app.add_handler(CommandHandler("listusers", list_users))

    # Task management
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            TASK_TITLE: [MessageHandler(filters.TEXT, process_task_title)],
            TASK_DESC: [MessageHandler(filters.TEXT, process_task_desc)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    app.add_handler(task_conv)
    app.add_handler(CommandHandler("listtasks", list_tasks))

    # Basic commands
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Gunakan /addtask atau /listtasks")))
    
    return app

# =====================
# RUN APPLICATION
# =====================
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    # Start Flask in background
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Run bot
    logger.info("Starting bot...")
    bot = setup_bot()
    bot.run_polling()
