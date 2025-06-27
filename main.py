from telegram.ext import Application, CommandHandler
from flask import Flask, request
import os

TOKEN = os.getenv("BOT_TOKEN")

app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# Handler sederhana
async def start(update, context):
    await update.message.reply_text("Bot berjalan!")

app.add_handler(CommandHandler("start", start))

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), app.bot)
    app.update_queue.put(update)
    return "OK", 200

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=os.getenv("PORT", 8080))
