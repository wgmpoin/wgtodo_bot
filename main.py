from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
from supabase import create_client
import os
import asyncio

# Config
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SUPABASE = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Bot
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    SUPABASE.table("users").upsert({"id": user_id}).execute()
    await update.message.reply_text("✅ Bot siap! Ketik /add [task]")

app.add_handler(CommandHandler("start", start))

# Webhook
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), app.bot)
    asyncio.create_task(app.process_update(update))
    return "OK", 200

# Setup
async def init():
    await app.initialize()
    await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    print(f"🔄 Webhook aktif: {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    asyncio.run(init())
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
