import logging
import sqlite3
import os
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# DB setup
def init_db():
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
    c.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            title TEXT,
            recipients TEXT,
            deadline TEXT,
            note TEXT,
            status TEXT DEFAULT 'ongoing'
        )"""
    )
    conn.commit()
    conn.close()

# Authorization
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_user(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

# States
JUDUL, PENERIMA, DEADLINE, KETERANGAN = range(4)
task_data = {}

# Owner commands
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Contoh: /adduser 123456789")
        return
    user_id = int(context.args[0])
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"User {user_id} ditambahkan.")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Contoh: /removeuser 123456789")
        return
    user_id = int(context.args[0])
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"User {user_id} dihapus.")

async def list_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    msg = "📋 User terdaftar:\n" + "\n".join(str(u[0]) for u in users)
    await update.message.reply_text(msg)

# Tambah tugas
async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user(update.effective_user.id):
        await update.message.reply_text("Kamu tidak terdaftar.")
        return ConversationHandler.END
    task_data[update.effective_user.id] = {}
    await update.message.reply_text("📝 Judul tugas?")
    return JUDUL

async def input_judul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_data[update.effective_user.id]["judul"] = update.message.text
    await update.message.reply_text("👥 Penerima tugas? (pisahkan spasi, tanpa @)")
    return PENERIMA

async def input_penerima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_data[update.effective_user.id]["penerima"] = update.message.text
    await update.message.reply_text("⏰ Deadline? Format: YYYY-MM-DD HH:MM")
    return DEADLINE

async def input_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        deadline = datetime.strptime(update.message.text.strip(), "%Y-%m-%d %H:%M")
        task_data[update.effective_user.id]["deadline"] = deadline
        await update.message.reply_text("📌 Keterangan tugas?")
        return KETERANGAN
    except:
        await update.message.reply_text("Format salah. Contoh: 2025-07-01 15:00")
        return DEADLINE

async def input_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    task_data[uid]["keterangan"] = update.message.text.strip()
    d = task_data[uid]

    # Simpan ke DB
    conn = sqlite3.connect("data.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (creator_id, title, recipients, deadline, note) VALUES (?, ?, ?, ?, ?)",
        (uid, d["judul"], d["penerima"], d["deadline"].isoformat(), d["keterangan"]),
    )
    task_id = c.lastrowid
    conn.commit()
    conn.close()

    # Kirim notifikasi ke penerima
    for name in d["penerima"].split():
        try:
            await context.bot.send_message(
                chat_id=name,
                text=f"📋 Tugas #{task_id}: {d['judul']}\n🕒 {d['deadline']}\n📌 {d['keterangan']}\nBalas dengan /done{task_id} jika selesai.",
            )
        except Exception as e:
            logger.warning(f"Gagal kirim ke {name}: {e}")

    await update.message.reply_text("✅ Tugas berhasil dibuat.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END

# Start & test
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Gunakan /addtask untuk membuat tugas.")

# Main app
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            JUDUL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_judul)],
            PENERIMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_penerima)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_deadline)],
            KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_keterangan)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("adduser", add_user))
    app.add_handler(CommandHandler("removeuser", remove_user))
    app.add_handler(CommandHandler("listuser", list_user))

    app.run_polling()

if __name__ == "__main__":
    main()
