import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

# Config
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not all([TOKEN, WEBHOOK_URL]):
    raise RuntimeError("Missing required environment variables!")

# Initialize
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif!")

app.add_handler(CommandHandler("start", start))

# Webhook setup
async def set_webhook():
    await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

# Flask endpoint
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), app.bot)
    asyncio.create_task(app.process_update(update))
    return "OK", 200

# Run setup synchronously for Render
def setup():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(set_webhook())
    print(f"Webhook set to: {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    setup()
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
