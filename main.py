import os
import logging
import threading
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
flask_app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================
# Flask Routes (for Render health checks)
# ======================
@flask_app.route('/')
def health_check():
    return jsonify({
        "status": "running",
        "service": "telegram-bot",
        "version": "1.0"
    })

@flask_app.route('/status')
def status():
    return jsonify({"bot_status": "active"})

def run_flask_app():
    flask_app.run(host='0.0.0.0', port=PORT)

# ======================
# Telegram Bot Handlers
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command"""
    user = update.effective_user
    await update.message.reply_html(
        f"👋 Halo <b>{user.first_name}</b>!\n\n"
        "Saya adalah bot template untuk Render.com\n"
        "Gunakan /help untuk melihat perintah yang tersedia"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command"""
    help_text = [
        "<b>Daftar Perintah:</b>",
        "/start - Memulai bot",
        "/help - Menampilkan pesan ini",
        "/about - Tentang bot ini"
    ]
    await update.message.reply_html("\n".join(help_text))

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /about command"""
    await update.message.reply_text(
        "🤖 Bot Template\n"
        "Dibuat untuk deploy di Render.com\n"
        "Dengan Python Telegram Bot + Flask"
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Echo user message"""
    await update.message.reply_text(update.message.text)

# ======================
# Bot Initialization
# ======================
def setup_bot():
    """Configure and return Telegram bot application"""
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    return application

# ======================
# Main Application
# ======================
def main():
    """Main application entry point"""
    try:
        # Start Flask server in a separate thread
        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()

        # Initialize and run Telegram bot
        bot_app = setup_bot()
        
        logger.info("Starting bot in polling mode...")
        bot_app.run_polling()

    except Exception as e:
        logger.critical(f"Application failed: {e}")
        raise

if __name__ == "__main__":
    main()
