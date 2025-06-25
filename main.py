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

# ======================================
# INITIAL SETUP
# ======================================
load_dotenv()

# Environment variables configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # Default 0 jika tidak ada
PORT = int(os.getenv("PORT", "10000"))

# Validate critical variables
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable must be set!")
if OWNER_ID == 0:
    logging.warning("OWNER_ID not set, admin commands will be disabled")

# Flask app for Render health checks
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return "Bot is running 🚀 | Owner ID: {}".format(OWNER_ID)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================================
# DATABASE MOCK (Replace with real DB)
# ======================================
users_db = {OWNER_ID: "owner"}
tasks_db = []

# ======================================
# BOT HANDLERS
# ======================================
# ADMIN COMMANDS
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add new user (owner only)"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Hanya owner yang bisa menambah user!")
        return
    
    try:
        user_id = int(context.args[0])
        users_db[user_id] = "user"
        await update.message.reply_text(f"✅ User {user_id} ditambahkan!")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Format: /adduser [USER_ID]")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users (owner only)"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Akses ditolak!")
        return
    
    if not users_db:
        await update.message.reply_text("📭 Database user kosong")
        return
    
    users_list = "\n".join([f"👤 {uid} ({role})" for uid, role in users_db.items()])
    await update.message.reply_text(f"📋 Daftar User:\n{users_list}")

# TASK MANAGEMENT
TASK_TITLE, TASK_DESC = range(2)

async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start task creation flow"""
    if update.effective_user.id not in users_db:
        await update.message.reply_text("🔐 Anda belum terdaftar!")
        return ConversationHandler.END
    
    await update.message.reply_text("📝 Masukkan judul task:")
    return TASK_TITLE

async def save_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save task title"""
    context.user_data['task_title'] = update.message.text
    await update.message.reply_text("📄 Masukkan deskripsi task:")
    return TASK_DESC

async def save_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save task description"""
    tasks_db.append({
        'title': context.user_data['task_title'],
        'desc': update.message.text,
        'creator': update.effective_user.id
    })
    await update.message.reply_text("✅ Task berhasil ditambahkan!")
    return ConversationHandler.END

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks"""
    if not tasks_db:
        await update.message.reply_text("📭 Tidak ada task yang tersedia")
        return
    
    tasks_list = "\n\n".join([
        f"📌 {i+1}. {task['title']}\n"
        f"   {task['desc']}\n"
        f"   👤 oleh: {task['creator']}"
        for i, task in enumerate(tasks_db)
    ])
    await update.message.reply_text(f"📋 Daftar Task:\n{tasks_list}")

# BASIC COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    commands = [
        "/start - Tampilkan pesan ini",
        "/addtask - Buat task baru",
        "/listtasks - Lihat daftar task",
        "/help - Tampilkan bantuan"
    ]
    
    if user.id == OWNER_ID:
        commands.extend([
            "/adduser [ID] - Tambah user baru",
            "/listusers - Lihat daftar user"
        ])
    
    await update.message.reply_text(
        f"👋 Halo {user.first_name}!\n\n"
        "📌 Perintah yang tersedia:\n" + 
        "\n".join(commands)
    )

# ======================================
# BOT SETUP
# ======================================
def setup_bot():
    """Configure Telegram bot"""
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin commands
    app.add_handler(CommandHandler("adduser", add_user))
    app.add_handler(CommandHandler("listusers", list_users))

    # Task management
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_title)],
            TASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_desc)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    app.add_handler(task_conv)
    app.add_handler(CommandHandler("listtasks", list_tasks))

    # Basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))  # Same as start

    return app

# ======================================
# MAIN APPLICATION
# ======================================
def run_flask():
    """Run Flask web server"""
    flask_app.run(host='0.0.0.0', port=PORT)

def main():
    """Main application entry point"""
    try:
        # Start Flask in background thread
        threading.Thread(target=run_flask, daemon=True).start()
        
        # Setup and run bot
        bot = setup_bot()
        logger.info("Starting bot in polling mode...")
        bot.run_polling()

    except Exception as e:
        logger.critical(f"Application failed: {e}")
        raise

if __name__ == "__main__":
    main()
