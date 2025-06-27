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

# Load .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8080))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app
flask_app = Flask(__name__)

# Command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Bot siap digunakan.")

# Init Telegram bot
import asyncio
app = asyncio.run(
    ApplicationBuilder().token(BOT_TOKEN).build()
)
app.add_handler(CommandHandler("start", start))
asyncio.run(app.bot.set_webhook(f"{WEBHOOK_URL}/webhook"))

# Flask webhook route
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), app.bot)
        app.update_queue.put_nowait(update)
        return "OK"

# Run Flask
if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
