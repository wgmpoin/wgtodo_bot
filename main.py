import os import logging from dotenv import load_dotenv from telegram import Update from telegram.ext import ( ApplicationBuilder, CommandHandler, ContextTypes, ) from flask import Flask, request

Load environment variables

load_dotenv() BOT_TOKEN = os.getenv("BOT_TOKEN") WEBHOOK_URL = os.getenv("WEBHOOK_URL") PORT = int(os.environ.get("PORT", 8080))

Set up logging

logging.basicConfig( format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO ) logger = logging.getLogger(name)

Flask app

flask_app = Flask(name)

Telegram command handler

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Halo! Bot siap.")

Main function to start the bot

async def run_bot(): app = ApplicationBuilder().token(BOT_TOKEN).build() app.add_handler(CommandHandler("start", start))

# Set webhook
await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
return app

import asyncio telegram_app = asyncio.run(run_bot())

Flask route to receive webhook

@flask_app.route("/webhook", methods=["POST"]) def webhook(): if request.method == "POST": update = telegram_app.update_queue.put_nowait(Update.de_json(request.get_json(force=True), telegram_app.bot)) return "OK"

Run Flask

if name == "main": flask_app.run(host="0.0.0.0", port=PORT)

