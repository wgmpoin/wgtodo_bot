import os
import logging
import threading
import psycopg2
from datetime import datetime
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)

# Konfigurasi Aplikasi
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Web Server untuk Health Check
async def health_check(request):
    return web.Response(text="Bot Telegram is running")

def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    web.run_app(app, port=PORT)

# Database Manager
class DatabaseManager:
    @staticmethod
    def get_connection():
        try:
            return psycopg2.connect(
                DATABASE_URL,
                sslmode="require",
                connect_timeout=5
            )
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    @staticmethod
    def init_db():
        try:
            with DatabaseManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            registered_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            creator_id BIGINT,
                            title TEXT,
                            recipients TEXT,
                            deadline TIMESTAMP,
                            note TEXT,
                            status TEXT DEFAULT 'pending',
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.critical(f"Database init failed: {e}")
            raise

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Halo {user.first_name}!\n"
        "Gunakan /addtask untuk membuat tugas baru"
    )

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user(update.effective_user.id):
        await update.message.reply_text("Anda tidak terdaftar!")
        return
    
    await update.message.reply_text("Masukkan judul tugas:")
    return "GET_TITLE"

# [Tambahkan lebih banyak handler sesuai kebutuhan...]

def main():
    # Jalankan web server di thread terpisah
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # Inisialisasi database
    DatabaseManager.init_db()

    # Setup bot Telegram
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Registrasi handler
    app.add_handler(CommandHandler("start", start))
    
    # Conversation handler untuk membuat task
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addtask", add_task)],
        states={
            "GET_TITLE": [MessageHandler(filters.TEXT, get_title)],
            "GET_DESC": [MessageHandler(filters.TEXT, get_description)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    app.add_handler(conv_handler)

    # Mulai bot
    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
