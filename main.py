import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
from supabase import create_client, Client

# Konfigurasi Environment Variables
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Validasi Config
if not all([TOKEN, WEBHOOK_URL, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables!")

# Inisialisasi Supabase dengan Error Handling
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized")
except Exception as e:
    print(f"❌ Supabase init error: {e}")
    raise

# Inisialisasi Bot
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk /start"""
    user = update.effective_user
    try:
        # Simpan user ke Supabase
        supabase.table("users").upsert({
            "id": user.id,
            "name": user.full_name,
            "username": user.username
        }).execute()
        
        await update.message.reply_text(
            f"👋 Halo {user.mention_markdown()}!\n"
            "Gunakan /add [task] untuk menambah tugas"
        )
    except Exception as e:
        print(f"Database error: {e}")
        await update.message.reply_text("⚠️ Gagal menyimpan data")

# ================= WEBHOOK SETUP =================
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    """Endpoint untuk webhook Telegram"""
    try:
        update = Update.de_json(request.get_json(), app.bot)
        asyncio.create_task(app.process_update(update))
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "Error", 500

@flask_app.route("/")
def health_check():
    """Endpoint untuk health check Render"""
    return "Bot Running", 200

# ================= INITIALIZATION =================
async def setup():
    """Setup awal bot"""
    await app.initialize()
    await app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    print(f"🔄 Webhook set to: {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    # Jalankan setup dan Flask
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup())
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
