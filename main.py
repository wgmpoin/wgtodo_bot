import os
import logging
import threading
import psycopg2
import time
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, Bot
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

# Konfigurasi logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Konfigurasi variabel lingkungan
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Pastikan ini disetel di Render
DATABASE_URL = os.getenv("DATABASE_URL")
REMINDER_CHECK_INTERVAL_SECONDS = 3600 # Cek reminder setiap 1 jam

# Validasi variabel penting
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable must be set! Exiting.")
    exit(1)
if not DATABASE_URL:
    logger.critical("DATABASE_URL environment variable must be set! Exiting.")
    exit(1)
if not WEBHOOK_URL:
    logger.warning("WEBHOOK_URL environment variable is not set. Bot will run in polling mode (not recommended for Render).")
if OWNER_ID == 0:
    logger.warning("OWNER_ID not set, admin commands will be disabled.")

# Global variable for bot application instance (needed for reminder thread)
bot_application = None

# ======================================
# DATABASE MANAGER (PostgreSQL)
# ======================================
class DatabaseManager:
    @staticmethod
    def get_connection():
        """Mendapatkan koneksi ke database PostgreSQL."""
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
                            created_at TIMESTAMP DEFAULT NOW(),
                            last_reminded_at TIMESTAMP -- Kapan terakhir diingatkan untuk pengingat otomatis
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

# TASK MANAGEMENT (Conversation Handler) - **MODIFIED**
TASK_TITLE, ASK_RECIPIENTS, GET_RECIPIENTS, ASK_DEADLINE, GET_DEADLINE, TASK_NOTE_FINAL = range(6)

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
    await update.message.reply_text(
        "👥 Apakah Anda ingin menambahkan **penerima**? (Ketik `ya` atau `tidak`)\n\n"
        "Ketik `/cancel` untuk membatalkan.",
        parse_mode="Markdown"
    )
    return ASK_RECIPIENTS

async def ask_recipients_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menanyakan apakah user ingin menambahkan penerima."""
    choice = update.message.text.lower().strip()
    user_id = update.effective_user.id

    if choice == 'tidak':
        context.user_data['task_data']['recipients'] = str(user_id) # Set diri sendiri sebagai penerima
        await update.message.reply_text(
            "⏰ Apakah Anda ingin menambahkan **deadline**? (Ketik `ya` atau `tidak`)\n\n"
            "Ketik `/cancel` untuk membatalkan.",
            parse_mode="Markdown"
        )
        return ASK_DEADLINE
    elif choice == 'ya':
        await update.message.reply_text(
            "👥 Siapa penerima tugas ini? (Mohon gunakan ID numerik Telegram mereka, pisahkan dengan spasi jika lebih dari satu. Contoh: `123456789 987654321`)\n\n"
            "Ketik `/cancel` untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_RECIPIENTS
    else:
        await update.message.reply_text("⚠️ Mohon ketik `ya` atau `tidak`.\n\nKetik `/cancel` untuk membatalkan.")
        return ASK_RECIPIENTS

async def get_task_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima daftar penerima tugas dan memvalidasinya."""
    recipients_input = update.message.text.strip()
    
    recipient_ids = []
    for r_id_str in recipients_input.split():
        if not r_id_str.isdigit():
            await update.message.reply_text(f"⚠️ `{r_id_str}` bukan ID numerik yang valid. Mohon masukkan ID numerik Telegram yang dipisahkan spasi.\n\nKetik `/cancel` untuk membatalkan.", parse_mode="Markdown")
            return GET_RECIPIENTS
        recipient_ids.append(r_id_str)

    context.user_data['task_data']['recipients'] = " ".join(recipient_ids)
    await update.message.reply_text(
        "⏰ Apakah Anda ingin menambahkan **deadline**? (Ketik `ya` atau `tidak`)\n\n"
        "Ketik `/cancel` untuk membatalkan.",
        parse_mode="Markdown"
    )
    return ASK_DEADLINE

async def ask_deadline_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menanyakan apakah user ingin menambahkan deadline."""
    choice = update.message.text.lower().strip()

    if choice == 'tidak':
        # Default deadline: 7 hari dari sekarang
        context.user_data['task_data']['deadline'] = datetime.now() + timedelta(days=7)
        await update.message.reply_text("📌 Terakhir, apa keterangan atau detail tugasnya?\n\nKetik `/cancel` untuk membatalkan.")
        return TASK_NOTE_FINAL
    elif choice == 'ya':
        await update.message.reply_text(
            "⏰ Kapan deadline tugas ini? Format: `YYYY-MM-DD HH:MM` (Contoh: `2025-07-01 15:00`)\n\n"
            "Ketik `/cancel` untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_DEADLINE
    else:
        await update.message.reply_text("⚠️ Mohon ketik `ya` atau `tidak`.\n\nKetik `/cancel` untuk membatalkan.")
        return ASK_DEADLINE

async def get_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima deadline tugas dan memvalidasinya."""
    try:
        deadline_str = update.message.text.strip()
        deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
        
        if deadline_dt < datetime.now():
            await update.message.reply_text("⚠️ Deadline tidak bisa di masa lalu. Mohon masukkan tanggal dan waktu di masa depan.\n\nKetik `/cancel` untuk membatalkan.")
            return GET_DEADLINE

        context.user_data['task_data']['deadline'] = deadline_dt
        await update.message.reply_text("📌 Terakhir, apa keterangan atau detail tugasnya?\n\nKetik `/cancel` untuk membatalkan.")
        return TASK_NOTE_FINAL
    except ValueError:
        await update.message.reply_text("⚠️ Format deadline salah. Mohon ikuti format `YYYY-MM-DD HH:MM`. Contoh: `2025-07-01 15:00`\n\nKetik `/cancel` untuk membatalkan.", parse_mode="Markdown")
        return GET_DEADLINE

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

async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menandai tugas sebagai selesai berdasarkan ID tugas."""
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await update.message.reply_text("🔐 Anda belum terdaftar!")
        return

    # Perintah /doneXYZ, ambil XYZ
    task_id_str = None
    if context.args: # Jika format /done ID
        task_id_str = context.args[0]
    elif update.message.text.startswith('/done'): # Jika format /doneID
        task_id_str = update.message.text[5:] # Ambil setelah '/done'

    if not task_id_str or not task_id_str.isdigit():
        await update.message.reply_text("⚠️ ID tugas tidak valid. Mohon masukkan angka. Contoh: `/done123` atau `/done 123`", parse_mode="Markdown")
        return

    task_id = int(task_id_str)
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            # Periksa apakah user adalah penerima tugas atau pembuat tugas
            cur.execute(
                "SELECT creator_id, recipients, title, status FROM tasks WHERE id = %s",
                (task_id,)
            )
            task_info = cur.fetchone()

            if not task_info:
                await update.message.reply_text(f"❌ Tugas dengan ID `{task_id}` tidak ditemukan.", parse_mode="Markdown")
                return

            creator_id, recipients_str, title, current_status = task_info
            recipients_list = recipients_str.split()

            if current_status == 'completed':
                await update.message.reply_text(f"✅ Tugas `{title}` (ID: `{task_id}`) sudah selesai sebelumnya.", parse_mode="Markdown")
                return
            if current_status == 'cancelled':
                await update.message.reply_text(f"🚫 Tugas `{title}` (ID: `{task_id}`) telah dibatalkan.", parse_mode="Markdown")
                return

            # Hanya penerima atau pembuat yang bisa menandai selesai
            if str(user_id) in recipients_list or user_id == creator_id:
                cur.execute(
                    "UPDATE tasks SET status = 'completed' WHERE id = %s",
                    (task_id,)
                )
                conn.commit()
                await update.message.reply_text(f"✅ Tugas `{title}` (ID: `{task_id}`) berhasil ditandai sebagai selesai!", parse_mode="Markdown")
                logger.info(f"Task #{task_id} marked completed by user {user_id}.")

                # Kirim notifikasi ke pembuat tugas
                if user_id != creator_id: # Hindari duplikasi notif jika pembuat sendiri yang done
                    try:
                        await context.bot.send_message(
                            chat_id=creator_id,
                            text=f"🎉 **Pemberitahuan:**\n\n"
                                 f"Tugas Anda: `{title}` (ID: `{task_id}`)\n"
                                 f"Telah ditandai selesai oleh `{update.effective_user.id}`.",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify creator {creator_id} for task {task_id} completion: {e}")
            else:
                await update.message.reply_text("❌ Anda tidak memiliki izin untuk menandai tugas ini sebagai selesai.")

    except Exception as e:
        logger.error(f"Error marking task {task_id} as done by {user_id}: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat memproses permintaan Anda. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar tugas berdasarkan status."""
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await update.message.reply_text("🔐 Anda belum terdaftar!")
        return

    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            # Mengambil tugas di mana user adalah creator atau recipient
            cur.execute(
                """SELECT id, title, recipients, deadline, note, status, creator_id
                   FROM tasks
                   WHERE creator_id = %s OR recipients LIKE %s
                   ORDER BY deadline ASC, status ASC""",
                (user_id, f'%{user_id}%') # Mencari ID user di kolom recipients
            )
            tasks = cur.fetchall()

        if not tasks:
            await update.message.reply_text("📭 Tidak ada tugas yang terkait dengan Anda.")
            return

        response_msg = "📋 **Daftar Tugas Anda:**\n\n"
        pending_tasks = []
        completed_tasks = []
        cancelled_tasks = []

        for task_id, title, recipients, deadline, note, status, creator_id in tasks:
            recipients_ids = recipients.split()
            is_creator = (user_id == creator_id)
            is_recipient = (str(user_id) in recipients_ids) # recipients_ids adalah list string

            # Tentukan label status
            status_label = ""
            if status == 'pending':
                status_label = "⏳ _(Pending)_"
            elif status == 'completed':
                status_label = "✅ _(Selesai)_"
            elif status == 'cancelled':
                status_label = "🚫 _(Dibatalkan)_"

            # Tentukan peran user dalam tugas ini
            role_label = ""
            if is_creator:
                role_label += "Pembuat"
            if is_recipient:
                if role_label: role_label += "/"
                role_label += "Penerima"

            task_details = (
                f"**ID:** `{task_id}` {status_label}\n"
                f"**Judul:** {title}\n"
                f"**Deadline:** {deadline.strftime('%Y-%m-%d %H:%M')}\n"
                f"**Note:** {note}\n"
                f"**Peran Anda:** {role_label}\n"
            )
            
            if status == 'pending':
                pending_tasks.append(task_details)
            elif status == 'completed':
                completed_tasks.append(task_details)
            elif status == 'cancelled':
                cancelled_tasks.append(task_details)

        if pending_tasks:
            response_msg += "*Tugas Menunggu:*\n" + ("\n---\n".join(pending_tasks)) + "\n\n"
        if completed_tasks:
            response_msg += "*Tugas Selesai:*\n" + ("\n---\n".join(completed_tasks)) + "\n\n"
        if cancelled_tasks:
            response_msg += "*Tugas Dibatalkan:*\n" + ("\n---\n".join(cancelled_tasks)) + "\n\n"
        
        await update.message.reply_text(response_msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error listing tasks for user {user_id}: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat menampilkan daftar tugas. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

# BASIC COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mengirim pesan selamat datang dan daftar perintah."""
    user = update.effective_user
    commands = [
        "/start - Tampilkan pesan ini",
        "/addtask - Buat tugas baru",
        "/listtasks - Lihat daftar tugas Anda",
        "/done[ID] - Tandai tugas selesai (contoh: /done123)",
        "/cancel - Batalkan pembuatan tugas",
        "/help - Tampilkan bantuan"
    ]
    
    if is_owner(user.id):
        commands.extend([
            "/adduser [ID] - Tambah user baru",
            "/listusers - Lihat daftar user terdaftar"
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
# REMINDER FEATURE (Background Thread)
# ======================================
async def send_reminder(chat_id: int, task_id: int, title: str, deadline: datetime, reminder_type: str):
    """Mengirim pesan pengingat ke user."""
    if bot_application: # Pastikan bot_application sudah terinisialisasi
        try:
            await bot_application.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 **Pengingat Tugas!**\n\n"
                     f"**ID Tugas:** `#{task_id}`\n"
                     f"**Judul:** {title}\n"
                     f"**Deadline:** {deadline.strftime('%Y-%m-%d %H:%M')}\n"
                     f"**Status:** {reminder_type} lagi menuju deadline!\n\n"
                     f"Segera selesaikan atau tandai dengan `/done{task_id}`.",
                parse_mode="Markdown"
            )
            logger.info(f"Reminder ({reminder_type}) sent for task #{task_id} to {chat_id}")
        except Exception as e:
            logger.warning(f"Failed to send reminder for task #{task_id} to {chat_id}: {e}")

def reminder_worker(app: ApplicationBuilder):
    """Fungsi worker yang berjalan di background untuk mengecek reminder."""
    global bot_application # Akses global bot_application
    bot_application = app # Set global reference to the application

    while True:
        conn = None
        try:
            conn = DatabaseManager.get_connection()
            with conn.cursor() as cur:
                # Ambil semua tugas yang pending dan memiliki deadline di masa depan
                # Serta belum diingatkan dalam REMINDER_CHECK_INTERVAL_SECONDS terakhir
                cur.execute(
                    """SELECT id, creator_id, title, recipients, deadline, last_reminded_at
                       FROM tasks
                       WHERE status = 'pending' AND deadline > NOW()
                       AND (last_reminded_at IS NULL OR last_reminded_at < NOW() - INTERVAL '%s seconds')""",
                    (REMINDER_CHECK_INTERVAL_SECONDS,)
                )
                tasks_to_check = cur.fetchall()

            current_time = datetime.now()
            
            for task_id, creator_id, title, recipients_str, deadline, last_reminded_at in tasks_to_check:
                time_left = deadline - current_time
                recipients_list = recipients_str.split()
                
                reminder_needed = False
                reminder_type = ""

                # Reminder 7 hari
                if timedelta(days=6, hours=23) < time_left <= timedelta(days=7, hours=23):
                    reminder_needed = True
                    reminder_type = "7 Hari"
                # Reminder 3 hari
                elif timedelta(days=2, hours=23) < time_left <= timedelta(days=3, hours=23):
                    reminder_needed = True
                    reminder_type = "3 Hari"
                # Reminder 2 hari
                elif timedelta(days=1, hours=23) < time_left <= timedelta(days=2, hours=23):
                    reminder_needed = True
                    reminder_type = "2 Hari"
                # Reminder 1 hari (24 jam)
                elif timedelta(hours=23) < time_left <= timedelta(days=1, hours=23):
                    reminder_needed = True
                    reminder_type = "1 Hari"
                # Reminder 1 jam
                elif timedelta(minutes=59) < time_left <= timedelta(hours=1, minutes=1): # Sedikit buffer
                    reminder_needed = True
                    reminder_type = "1 Jam"
                # Reminder sudah lewat
                elif time_left <= timedelta(minutes=0) and time_left > timedelta(minutes=-10): # Baru saja lewat
                     reminder_needed = True
                     reminder_type = "Telah Lewat"


                if reminder_needed:
                    # Kirim reminder ke setiap penerima dan pembuat
                    all_involved_users = set([creator_id] + [int(r) for r in recipients_list])
                    for user_chat_id in all_involved_users:
                        # Schedule the async send_reminder call using application.create_task
                        # This runs the async function in the bot's event loop
                        app.create_task(send_reminder(user_chat_id, task_id, title, deadline, reminder_type))
                    
                    # Update last_reminded_at di database
                    with conn.cursor() as update_cur:
                        update_cur.execute(
                            "UPDATE tasks SET last_reminded_at = %s WHERE id = %s",
                            (current_time, task_id)
                        )
                        conn.commit()
                    logger.info(f"Task #{task_id} reminder processed for '{reminder_type}'.")
        
        except Exception as e:
            logger.error(f"Error in reminder worker: {e}")
        finally:
            if conn:
                conn.close()
        
        time.sleep(REMINDER_CHECK_INTERVAL_SECONDS) # Tunggu sebelum cek lagi

# ======================================
# FLASK WEBHOOK SETUP
# ======================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """Simple health check for Render."""
    return "Bot is running 🚀 | Owner ID: {}".format(OWNER_ID)

@flask_app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def telegram_webhook():
    """Handles Telegram updates via webhook."""
    update = Update.de_json(request.get_json(force=True), bot_application.bot)
    await bot_application.process_update(update)
    return "ok"

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
    global bot_application

    # Start Flask in background thread
    web_thread = threading.Thread(target=run_flask_server, daemon=True)
    web_thread.start()
    
    # Inisialisasi database
    try:
        DatabaseManager.init_db()
    except Exception:
        logger.critical("Database initialization failed. Exiting application.")
        exit(1)

    # Setup bot
    bot_application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin commands
    bot_application.add_handler(CommandHandler("adduser", add_user))
    bot_application.add_handler(CommandHandler("listusers", list_users))

    # Task management ConversationHandler - MODIFIED ENTRY POINTS AND STATES
    task_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_title)],
            ASK_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_recipients_choice)],
            GET_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_recipients)],
            ASK_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_deadline_choice)],
            GET_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_deadline)],
            TASK_NOTE_FINAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_task_and_notify)],
        },
        fallbacks=[CommandHandler("cancel", cancel_task_creation)],
    )
    bot_application.add_handler(task_conv_handler)
    
    # Task actions
    bot_application.add_handler(CommandHandler("done", done_task)) # Handle /done 123
    bot_application.add_handler(MessageHandler(filters.Regex(r'^/done\d+$'), done_task)) # Handle /done123
    bot_application.add_handler(CommandHandler("listtasks", list_tasks))

    # Basic commands
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CommandHandler("help", help_command))

    # Start reminder worker in a separate thread
    reminder_thread = threading.Thread(target=reminder_worker, args=(bot_application,), daemon=True)
    reminder_thread.start()
    
    logger.info("Starting Telegram bot...")
    if WEBHOOK_URL:
        # Set webhook
        try:
            bot_application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=BOT_TOKEN,
                webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
            )
            logger.info(f"Webhook set to {WEBHOOK_URL}/{BOT_TOKEN}")
        except Exception as e:
            logger.critical(f"Failed to set up webhook: {e}")
            os._exit(1)
    else:
        # Fallback to polling (not recommended for Render, will likely lead to Conflict)
        logger.warning("WEBHOOK_URL not set. Bot will run in polling mode (may cause conflicts).")
        try:
            bot_application.run_polling(poll_interval=3, timeout=30)
        except Exception as e:
            logger.critical(f"Telegram bot polling failed: {e}")
            raise

if __name__ == "__main__":
    main()
