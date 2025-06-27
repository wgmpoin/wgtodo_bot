import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app
flask_app = Flask(__name__)

# Telegram handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Bot siap dipakai.")

# Build Telegram app
application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    async def process_update():
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
    
    # Run the async function in the event loop
    application.create_task(process_update())
    return "OK"

# Set webhook saat startup
async def set_webhook():
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

# Run the Flask app
if __name__ == "__main__":
    # Set the webhook when starting
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(set_webhook())
    
    # Start Flask
    flask_app.run(host="0.0.0.0", port=PORT)
