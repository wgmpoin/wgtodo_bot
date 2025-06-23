import logging import sqlite3 import os from datetime import datetime, timedelta from telegram import Update, ForceReply from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CallbackContext)

--- Konstanta tahap input tugas ---

JUDUL, PENERIMA, DEADLINE, KETERANGAN = range(4)

--- Load token dari environment ---

BOT_TOKEN = os.getenv("BOT_TOKEN") OWNER_ID = int(os.getenv("OWNER_ID"))  # Telegram user ID kamu

--- Setup logging ---

logging.basicConfig( format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO ) logger = logging.getLogger(name)

--- Inisialisasi Database ---

def init_db(): conn = sqlite3.connect("data.db") c = conn.cursor() c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''') c.execute('''CREATE TABLE IF NOT EXISTS tasks ( id INTEGER PRIMARY KEY AUTOINCREMENT, creator_id INTEGER, title TEXT, recipients TEXT, deadline TEXT, note TEXT, status TEXT DEFAULT 'ongoing' )''') conn.commit() conn.close()

--- Cek apakah user terdaftar ---

def is_user_registered(user_id: int) -> bool: conn = sqlite3.connect("data.db") c = conn.cursor() c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)) result = c.fetchone() conn.close() return result is not None or user_id == OWNER_ID

--- Hanya untuk owner ---

def is_owner(user_id: int) -> bool: return user_id == OWNER_ID

=== OWNER COMMAND ===

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE): if not is_owner(update.effective_user.id): return if not context.args: await update.message.reply_text("Gunakan: /adduser 123456789") return user_id = int(context.args[0]) conn = sqlite3.connect("data.db") c = conn.cursor() c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)) conn.commit() conn.close() await update.message.reply_text(f"User {user_id} ditambahkan.")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE): if not is_owner(update.effective_user.id): return if not context.args: await update.message.reply_text("Gunakan: /removeuser 123456789") return user_id = int(context.args[0]) conn = sqlite3.connect("data.db") c = conn.cursor() c.execute("DELETE FROM users WHERE user_id=?", (user_id,)) conn.commit() conn.close() await update.message.reply_text(f"User {user_id} dihapus.")

async def list_user(update: Update, context: ContextTypes.DEFAULT_TYPE): if not is_owner(update.effective_user.id): return conn = sqlite3.connect("data.db") c = conn.cursor() c.execute("SELECT user_id FROM users") users = c.fetchall() conn.close() msg = "Daftar user: " + "\n".join(str(u[0]) for u in users) await update.message.reply_text(msg)

=== ALUR BUAT TUGAS ===

task_data = {}

async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE): user_id = update.effective_user.id if not is_user_registered(user_id): await update.message.reply_text("Maaf, kamu tidak terdaftar untuk menggunakan bot ini.") return ConversationHandler.END task_data[user_id] = {} await update.message.reply_text("📝 Judul tugas?") return JUDUL

async def input_judul(update: Update, context: ContextTypes.DEFAULT_TYPE): task_data[update.effective_user.id]['judul'] = update.message.text await update.message.reply_text("👥 Siapa penerima tugas? (pisahkan dengan spasi, tanpa @)") return PENERIMA

async def input_penerima(update: Update, context: ContextTypes.DEFAULT_TYPE): task_data[update.effective_user.id]['penerima'] = update.message.text.strip() await update.message.reply_text("⏰ Deadline tugas? (format: YYYY-MM-DD HH:MM)") return DEADLINE

async def input_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE): try: deadline = datetime.strptime(update.message.text.strip(), "%Y-%m-%d %H:%M") task_data[update.effective_user.id]['deadline'] = deadline await update.message.reply_text("📌 Keterangan tugas?") return KETERANGAN except ValueError: await update.message.reply_text("Format salah. Coba lagi: YYYY-MM-DD HH:MM") return DEADLINE

async def input_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE): user_id = update.effective_user.id task_data[user_id]['keterangan'] = update.message.text.strip() data = task_data[user_id]

# Simpan ke database
conn = sqlite3.connect("data.db")
c = conn.cursor()
c.execute("INSERT INTO tasks (creator_id, title, recipients, deadline, note) VALUES (?, ?, ?, ?, ?)",
          (user_id, data['judul'], data['penerima'], data['deadline'].isoformat(), data['keterangan']))
task_id = c.lastrowid
conn.commit()
conn.close()

# Kirim ke penerima
for name in data['penerima'].split():
    try:
        await context.bot.send_message(chat_id=name, text=f"📋 Tugas Baru #{task_id}: {data['judul']}\n🕒 Deadline: {data['deadline']}\n📌 {data['keterangan']}\n\nBalas dengan /done{task_id} jika selesai, /extend{task_id} jika butuh tambahan waktu.")
    except:
        logger.warning(f"Gagal kirim ke {name}")

await update.message.reply_text("✅ Tugas berhasil dibuat dan dikirim.")
return ConversationHandler.END

async def cancel_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Dibatalkan.") return ConversationHandler.END

=== MAIN ===

if name == 'main': init_db() app = ApplicationBuilder().token(BOT_TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("addtask", start_add_task)],
    states={
        JUDUL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_judul)],
        PENERIMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_penerima)],
        DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_deadline)],
        KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_keterangan)],
    },
    fallbacks=[CommandHandler("cancel", cancel_add_task)]
)

app.add_handler(conv_handler)
app.add_handler(CommandHandler("adduser", add_user))
app.add_handler(CommandHandler("removeuser", remove_user))
app.add_handler(CommandHandler("listuser", list_user))

app.run_polling()

