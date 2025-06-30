import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from supabase import create_client
from flask import Flask, request

# Config
TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Init
bot = Application.builder().token(TOKEN).build()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
flask_app = Flask(__name__)

# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    supabase.table("users").upsert({
        "id": user.id,
        "name": user.full_name
    }).execute()
    await update.message.reply_text(f"✅ Halo {user.first_name}! Bot siap pakai.")

# Command: /add [@penerima] [task]
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("Contoh: /add @diana Beli bahan presentasi")
            return

        penerima = args[0].replace("@", "")  # Hapus @
        tugas = " ".join(args[1:])
        
        # Simpan ke tasks_temp (tabel baru)
        supabase.table("tasks_temp").insert({
            "user_id": update.effective_user.id,
            "assigned_by": update.effective_user.id,
            "assigned_to": penerima,
            "task": tugas,
            "completed": False
        }).execute()

        await update.message.reply_text(
            f"✅ Tugas untuk @{penerima}:\n"
            f"📝 {tugas}"
        )
    except Exception as e:
        await update.message.reply_text("❌ Gagal menyimpan. Coba lagi nanti.")

# Webhook
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), bot.bot)
    bot.process_update(update)
    return "OK", 200

if __name__ == "__main__":
    # Register commands
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("add", add_task))
    
    # Run bot
    flask_app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
