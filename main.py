import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

# Config dengan error handling
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set!")
if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL not set!")

FULL_WEBHOOK_URL = f"{WEBHOOK_URL}/webhook"  # Pakai f-string lebih aman

# Bot setup
app = Application.builder().token(TOKEN).build()

# Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif!")

app.add_handler(CommandHandler("start", start))

# Webhook setup
async def set_webhook():
    await app.bot.set_webhook(FULL_WEBHOOK_URL)

# Flask server
server = Flask(__name__)

@server.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), app.bot)
    app.update_queue.put(update)
    return "OK", 200

if __name__ == "__main__":
    app.run_once(set_webhook())
    server.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
