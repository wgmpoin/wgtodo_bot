import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

# Config
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + "/webhook"  # Pastikan ada di Render

# Bot
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktif!")

app.add_handler(CommandHandler("start", start))

# Webhook
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), app.bot)
    app.update_queue.put(update)
    return "OK", 200

if __name__ == "__main__":
    # Set webhook saat startup
    app.run_once(lambda: app.bot.set_webhook(WEBHOOK_URL))
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
