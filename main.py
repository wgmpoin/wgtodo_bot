import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from flask import Flask, request

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # contoh: https://namabot.onrender.com
PORT = int(os.environ.get("PORT", 8080))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app
flask_app = Flask(__name__)

# Telegram handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Bot siap dipakai.")

# Build Telegram Application
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "ok", 200

async def set_webhook():
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    # Jalankan sekali saat startup
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_webhook())
    
    # Jalankan Flask
    flask_app.run(host="0.0.0.0", port=PORT)
