import os
import logging
import threading
import psycopg2
from datetime import datetime
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from dotenv import load_dotenv

# ======================================
# INITIAL SETUP
# ======================================
load_dotenv()

# Konfigurasi variabel lingkungan
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

# Validasi variabel penting
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable must be set! Exiting.")
    exit(1)
if OWNER_ID == 0:
    logger.warning("OWNER_ID not set, admin commands will be disabled.")
    # OWNER_ID harus diatur untuk admin commands berfungsi
    # Jika Anda belum menyetelnya, lakukan di Render's Environment Variables

# Flask app untuk Render health checks
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return "Bot is running 🚀 | Owner ID: {}".format(OWNER_ID)

# Konfigurasi Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================================
# DATABASE MANAGER (PostgreSQL)
# ======================================
class DatabaseManager:
    @staticmethod
    def get_connection():
        """Mendapatkan koneksi ke database PostgreSQL."""
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is not set.")
        try:
            return psycopg2.connect(
                DATABASE_URL,
                sslmode="require",
                connect_timeout=10 # Timeout koneksi 10 detik
            )
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    @staticmethod
    def init_db():
        """Menginisialisasi tabel database jika belum ada."""
        try:
            with DatabaseManager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            registered_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            creator_id BIGINT,
                            title TEXT,
                            recipients TEXT, -- Simpan ID penerima dipisahkan spasi
                            deadline TIMESTAMP,
                            note TEXT,
                            status TEXT DEFAULT 'pending', -- 'pending', 'completed', 'cancelled'
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    conn.commit()
            logger.info("Database tables initialized successfully.")
        except Exception as e:
            logger.critical(f"Database initialization failed: {e}")
            raise

# ======================================
# BOT HANDLERS
# ======================================

# Authorization Helper
def is_owner(user_id: int) -> bool:
    """Memeriksa apakah user adalah OWNER_ID."""
    return user_id == OWNER_ID

async def is_user_registered(user_id: int) -> bool:
    """Memeriksa apakah user terdaftar di database."""
    if user_id == OWNER_ID: # Owner selalu dianggap terdaftar
        return True
    
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking if user {user_id} is registered: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ADMIN COMMANDS
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menambahkan user ke daftar user yang diizinkan (hanya owner)."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Maaf, hanya pemilik bot yang bisa menggunakan perintah ini.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ Format: `/adduser [ID_TELEGRAM_NUMERIK]`")
        return
    
    conn = None
    try:
        user_id_to_add = int(context.args[0])
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id_to_add, update.effective_user.username or "N/A")
            )
            conn.commit()
        await update.message.reply_text(f"✅ User `{user_id_to_add}` berhasil ditambahkan!")
        logger.info(f"User {user_id_to_add} added by owner {update.effective_user.id}.")
    except ValueError:
        await update.message.reply_text("⚠️ ID user tidak valid. Mohon masukkan angka.")
    except Exception as e:
        logger.error(f"Error adding user {context.args[0]} by {update.effective_user.id}: {e}")
        await update.message.reply_text("❌ Gagal menambahkan user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar user yang terdaftar (hanya owner)."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Maaf, hanya pemilik bot yang bisa menggunakan perintah ini.")
        return
    
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, username FROM users")
            users = cur.fetchall()
        
        if users:
            msg = "📋 User terdaftar:\n" + "\n".join([f"👤 `{uid}` (@{uname})" if uname != "N/A" else f"👤 `{uid}`" for uid, uname in users])
        else:
            msg = "📭 Tidak ada user terdaftar."
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error listing users by {update.effective_user.id}: {e}")
        await update.message.reply_text("❌ Gagal menampilkan user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

# TASK MANAGEMENT (Conversation Handler)
TASK_TITLE, TASK_RECIPIENTS, TASK_DEADLINE, TASK_NOTE = range(4)

async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memulai alur pembuatan tugas."""
    if not await is_user_registered(update.effective_user.id):
        await update.message.reply_text("🔐 Anda belum terdaftar sebagai pengguna bot ini. Silakan hubungi pemilik bot.")
        return ConversationHandler.END
    
    context.user_data['task_data'] = {} # Inisialisasi data tugas untuk user ini
    await update.message.reply_text("📝 Oke, mari buat tugas baru. Apa judul tugasnya?")
    return TASK_TITLE

async def get_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima judul tugas."""
    context.user_data['task_data']['title'] = update.message.text.strip()
    await update.message.reply_text("👥 Siapa penerima tugas ini? (Mohon gunakan ID numerik Telegram mereka, pisahkan dengan spasi jika lebih dari satu. Contoh: `123456789 987654321`)\n\nKetik `/cancel` untuk membatalkan.")
    return TASK_RECIPIENTS

async def get_task_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima daftar penerima tugas dan memvalidasinya."""
    recipients_input = update.message.text.strip()
    
    recipient_ids = []
    for r_id_str in recipients_input.split():
        if not r_id_str.isdigit():
            await update.message.reply_text(f"⚠️ `{r_id_str}` bukan ID numerik yang valid. Mohon masukkan ID numerik Telegram yang dipisahkan spasi.\n\nKetik `/cancel` untuk membatalkan.", parse_mode="Markdown")
            return TASK_RECIPIENTS
        recipient_ids.append(r_id_str)

    context.user_data['task_data']['recipients'] = " ".join(recipient_ids)
    await update.message.reply_text("⏰ Kapan deadline tugas ini? Format: `YYYY-MM-DD HH:MM` (Contoh: `2025-07-01 15:00`)\n\nKetik `/cancel` untuk membatalkan.", parse_mode="Markdown")
    return TASK_DEADLINE

async def get_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima deadline tugas dan memvalidasinya."""
    try:
        deadline_str = update.message.text.strip()
        deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
        
        if deadline_dt < datetime.now():
            await update.message.reply_text("⚠️ Deadline tidak bisa di masa lalu. Mohon masukkan tanggal dan waktu di masa depan.\n\nKetik `/cancel` untuk membatalkan.")
            return TASK_DEADLINE

        context.user_data['task_data']['deadline'] = deadline_dt
        await update.message.reply_text("📌 Terakhir, apa keterangan atau detail tugasnya?\n\nKetik `/cancel` untuk membatalkan.")
        return TASK_NOTE
    except ValueError:
        await update.message.reply_text("⚠️ Format deadline salah. Mohon ikuti format `YYYY-MM-DD HH:MM`. Contoh: `2025-07-01 15:00`\n\nKetik `/cancel` untuk membatalkan.", parse_mode="Markdown")
        return TASK_DEADLINE

async def save_task_and_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima keterangan tugas, menyimpan ke DB, dan mengirim notifikasi."""
    user_id = update.effective_user.id
    task_data_temp = context.user_data.get('task_data')
    
    if not task_data_temp: # Fallback jika data hilang (misal karena bot restart)
        await update.message.reply_text("❌ Maaf, data pembuatan tugas hilang. Mohon mulai ulang dengan /addtask.")
        return ConversationHandler.END

    task_data_temp['note'] = update.message.text.strip()

    conn = None
    task_id = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tasks (creator_id, title, recipients, deadline, note) 
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (user_id, 
                 task_data_temp['title'], 
                 task_data_temp['recipients'], 
                 task_data_temp['deadline'], 
                 task_data_temp['note']),
            )
            task_id = cur.fetchone()[0]
            conn.commit()
        logger.info(f"Task #{task_id} created by user {user_id}.")
    except Exception as e:
        logger.error(f"Error saving task for user {user_id}: {e}")
        await update.message.reply_text("❌ Gagal membuat tugas. Mohon coba lagi nanti atau hubungi admin.")
        return ConversationHandler.END
    finally:
        if conn:
            conn.close()

    if task_id:
        recipients_list = task_data_temp['recipients'].split()
        for recipient_id_str in recipients_list:
            try:
                recipient_chat_id = int(recipient_id_str)
                await context.bot.send_message(
                    chat_id=recipient_chat_id,
                    text=f"📋 **Tugas Baru!**\n\n"
                         f"**ID Tugas:** `#{task_id}`\n"
                         f"**Judul:** {task_data_temp['title']}\n"
                         f"**Deadline:** {task_data_temp['deadline'].strftime('%Y-%m-%d %H:%M')}\n"
                         f"**Keterangan:** {task_data_temp['note']}\n\n"
                         f"Silakan kerjakan. Balas dengan `/done{task_id}` jika selesai.",
                    parse_mode="Markdown"
                )
                logger.info(f"Notification sent for task #{task_id} to {recipient_chat_id}")
            except Exception as e:
                logger.warning(f"Gagal kirim notifikasi tugas #{task_id} ke {recipient_id_str}: {e}")
        
        await update.message.reply_text(f"✅ Tugas berhasil dibuat dengan ID `#{task_id}` dan notifikasi dikirimkan.")
    
    if 'task_data' in context.user_data:
        del context.user_data['task_data']
    
    return ConversationHandler.END

async def cancel_task_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan alur ConversationHandler pembuatan tugas."""
    if 'task_data' in context.user_data:
        del context.user_data['task_data']
    await update.message.reply_text("❌ Pembuatan tugas dibatalkan.")
    return ConversationHandler.END

# BASIC COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengirim pesan selamat datang dan daftar perintah."""
    user = update.effective_user
    commands = [
        "/start - Tampilkan pesan ini",
        "/addtask - Buat tugas baru",
        "/listtasks - Lihat daftar tugas (belum diimplementasi)",
        "/help - Tampilkan bantuan"
    ]
    
    if is_owner(user.id):
        commands.extend([
            "/adduser [ID] - Tambah user baru",
            "/listusers - Lihat daftar user"
        ])
    
    await update.message.reply_text(
        f"👋 Halo {user.first_name}!\n\n"
        "📌 Perintah yang tersedia:\n" + 
        "\n".join(commands),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan pesan bantuan (sama dengan start untuk saat ini)."""
    await start(update, context)

# ======================================
# BOT SETUP
# ======================================
def setup_bot():
    """Mengkonfigurasi bot Telegram."""
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin commands
    app.add_handler(CommandHandler("adduser", add_user))
    app.add_handler(CommandHandler("listusers", list_users))

    # Task management ConversationHandler
    task_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_title)],
            TASK_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_recipients)],
            TASK_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_deadline)],
            TASK_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_and_notify)],
        },
        fallbacks=[CommandHandler("cancel", cancel_task_creation)], # Tambahkan cancel di sini
    )
    app.add_handler(task_conv_handler)
    # app.add_handler(CommandHandler("listtasks", list_tasks)) # Belum diimplementasi

    # Basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    return app

# ======================================
# MAIN APPLICATION
# ======================================
def run_flask_server():
    """Menjalankan Flask web server di thread terpisah."""
    logger.info(f"Starting Flask health check server on port {PORT}...")
    try:
        flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.critical(f"Flask server failed to start: {e}")
        os._exit(1) # Keluar dari proses jika Flask gagal

def main():
    """Fungsi utama aplikasi."""
    # Pastikan variabel lingkungan penting sudah diatur
    if not BOT_TOKEN or not DATABASE_URL:
        logger.critical("Critical environment variables (BOT_TOKEN, DATABASE_URL) are not set. Exiting.")
        exit(1)

    # Start Flask in background thread
    web_thread = threading.Thread(target=run_flask_server, daemon=True)
    web_thread.start()
    
    # Inisialisasi database
    try:
        DatabaseManager.init_db()
    except Exception:
        logger.critical("Database initialization failed. Exiting application.")
        exit(1)

    # Setup dan jalankan bot
    bot_app = setup_bot()
    logger.info("Starting Telegram bot in polling mode...")
    try:
        bot_app.run_polling(poll_interval=3, timeout=30)
    except Exception as e:
        logger.critical(f"Telegram bot polling failed: {e}")
        raise

if __name__ == "__main__":
    main()
