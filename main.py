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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Pastikan ini disetel di Render
DATABASE_URL = os.getenv("DATABASE_URL")
REMINDER_CHECK_INTERVAL_SECONDS = 3600  # Cek reminder setiap 1 jam

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

# Global variable for bot application instance
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
                connect_timeout=10  # Timeout koneksi 10 detik
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
                    # Tabel users
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            first_name TEXT,
                            last_name TEXT,
                            registered_at TIMESTAMP DEFAULT NOW(),
                            last_active TIMESTAMP
                        )
                    """)
                    
                    # Tabel tasks
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            creator_id BIGINT REFERENCES users(user_id),
                            title TEXT NOT NULL,
                            recipients TEXT NOT NULL,  -- Simpan ID penerima dipisahkan spasi
                            deadline TIMESTAMP NOT NULL,
                            note TEXT,
                            status TEXT DEFAULT 'pending',  -- 'pending', 'completed', 'cancelled'
                            created_at TIMESTAMP DEFAULT NOW(),
                            last_reminded_at TIMESTAMP,
                            completed_at TIMESTAMP
                        )
                    """)
                    
                    # Tabel reminders
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS reminders (
                            id SERIAL PRIMARY KEY,
                            task_id INTEGER REFERENCES tasks(id),
                            user_id BIGINT REFERENCES users(user_id),
                            reminder_type TEXT,  -- '7days', '3days', '1day', '1hour', 'overdue'
                            sent_at TIMESTAMP DEFAULT NOW()
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
# States for conversation
TASK_TITLE, ASK_RECIPIENTS, GET_RECIPIENTS, ASK_DEADLINE, GET_DEADLINE, TASK_NOTE_FINAL = range(6)

# Authorization Helper
def is_owner(user_id: int) -> bool:
    """Memeriksa apakah user adalah OWNER_ID."""
    return user_id == OWNER_ID

async def is_user_registered(user_id: int) -> bool:
    """Memeriksa apakah user terdaftar di database."""
    if user_id == OWNER_ID:  # Owner selalu dianggap terdaftar
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

async def register_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    """Mendaftarkan user baru ke database."""
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (user_id, username, first_name, last_name) 
                VALUES (%s, %s, %s, %s) 
                ON CONFLICT (user_id) DO UPDATE 
                SET username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    last_active = NOW()""",
                (user_id, username, first_name, last_name)
            )
            conn.commit()
            logger.info(f"User {user_id} registered/updated in database.")
    except Exception as e:
        logger.error(f"Error registering user {user_id}: {e}")
        raise
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
        await update.message.reply_text("⚠️ Format: `/adduser [ID_TELEGRAM_NUMERIK]`", parse_mode="Markdown")
        return
    
    conn = None
    try:
        user_id_to_add = int(context.args[0])
        username = update.effective_user.username or "N/A"
        first_name = update.effective_user.first_name or "N/A"
        
        await register_user(user_id_to_add, username, first_name)
        
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
            cur.execute("""
                SELECT user_id, username, first_name, last_name, registered_at 
                FROM users 
                ORDER BY registered_at DESC
            """)
            users = cur.fetchall()
            
            if users:
                msg = "📋 Daftar User Terdaftar:\n\n"
                for user in users:
                    user_id, username, first_name, last_name, reg_date = user
                    name = f"{first_name} {last_name}".strip()
                    msg += (
                        f"👤 ID: `{user_id}`\n"
                        f"📛 Nama: {name}\n"
                        f"📧 Username: @{username if username != 'N/A' else '-'}\n"
                        f"📅 Terdaftar: {reg_date.strftime('%Y-%m-%d %H:%M')}\n"
                        f"―――――――――――――――――――\n"
                    )
            else:
                msg = "📭 Tidak ada user terdaftar."
            
            await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error listing users by {update.effective_user.id}: {e}")
        await update.message.reply_text("❌ Gagal menampilkan user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

# TASK MANAGEMENT
async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memulai alur pembuatan tugas."""
    user = update.effective_user
    
    # Register/update user data
    try:
        await register_user(
            user.id,
            user.username or "N/A",
            user.first_name or "N/A",
            user.last_name or ""
        )
    except Exception as e:
        logger.error(f"Error registering user {user.id}: {e}")
        await update.message.reply_text("❌ Gagal memproses data user. Silakan coba lagi.")
        return ConversationHandler.END
    
    if not await is_user_registered(user.id):
        await update.message.reply_text("🔐 Anda belum terdaftar sebagai pengguna bot ini. Silakan hubungi pemilik bot.")
        return ConversationHandler.END
    
    context.user_data['task_data'] = {
        'creator_id': user.id,
        'creator_name': user.full_name
    }
    
    await update.message.reply_text(
        "📝 Mari buat tugas baru. Silakan berikan judul tugas:\n"
        "(Contoh: 'Perbaikan laporan keuangan')\n\n"
        "Ketik /cancel untuk membatalkan."
    )
    return TASK_TITLE

async def get_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima judul tugas."""
    title = update.message.text.strip()
    if len(title) > 100:
        await update.message.reply_text("⚠️ Judul terlalu panjang (max 100 karakter). Silakan berikan judul yang lebih singkat.")
        return TASK_TITLE
    
    context.user_data['task_data']['title'] = title
    
    await update.message.reply_text(
        "👥 Apakah Anda ingin menambahkan penerima khusus?\n"
        "(Default: Hanya Anda)\n\n"
        "Balas dengan:\n"
        "✅ `ya` - Untuk menambahkan penerima lain\n"
        "❌ `tidak` - Untuk hanya diri Anda sendiri\n\n"
        "Ketik /cancel untuk membatalkan.",
        parse_mode="Markdown"
    )
    return ASK_RECIPIENTS

async def ask_recipients_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menanyakan apakah user ingin menambahkan penerima."""
    choice = update.message.text.lower().strip()
    user_id = update.effective_user.id
    
    if choice == 'tidak':
        context.user_data['task_data']['recipients'] = str(user_id)
        await update.message.reply_text(
            "⏰ Deadline tugas:\n"
            "Anda bisa:\n"
            "✅ `ya` - Set deadline khusus\n"
            "❌ `tidak` - Gunakan deadline default (7 hari dari sekarang)\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return ASK_DEADLINE
    elif choice == 'ya':
        await update.message.reply_text(
            "👥 Masukkan ID numerik penerima tugas (pisahkan dengan spasi jika lebih dari satu):\n"
            "(Contoh: `123456789 987654321`)\n\n"
            "Anda bisa mendapatkan ID user dengan forward pesan dari user tersebut ke @userinfobot\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_RECIPIENTS
    else:
        await update.message.reply_text(
            "⚠️ Mohon ketik `ya` atau `tidak`.\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return ASK_RECIPIENTS

async def get_task_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima daftar penerima tugas dan memvalidasinya."""
    recipients_input = update.message.text.strip()
    creator_id = context.user_data['task_data']['creator_id']
    recipient_ids = []
    
    # Validasi format
    if not all(r_id.isdigit() for r_id in recipients_input.split()):
        await update.message.reply_text(
            "⚠️ Format ID tidak valid. Mohon masukkan ID numerik Telegram yang dipisahkan spasi.\n"
            "(Contoh: `123456789 987654321`)\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_RECIPIENTS
    
    # Konversi ke integer dan validasi unik
    recipient_ids = list(set(int(r_id) for r_id in recipients_input.split()))
    
    # Pastikan creator termasuk dalam penerima
    if creator_id not in recipient_ids:
        recipient_ids.append(creator_id)
    
    # Validasi user terdaftar
    invalid_users = []
    for user_id in recipient_ids:
        if not await is_user_registered(user_id):
            invalid_users.append(str(user_id))
    
    if invalid_users:
        await update.message.reply_text(
            f"⚠️ User berikut belum terdaftar: {', '.join(invalid_users)}\n"
            "Silakan tambahkan mereka dengan /adduser terlebih dahulu.\n\n"
            "Ketik /cancel untuk membatalkan."
        )
        return GET_RECIPIENTS
    
    context.user_data['task_data']['recipients'] = " ".join(map(str, recipient_ids))
    
    await update.message.reply_text(
        "⏰ Apakah Anda ingin menetapkan deadline khusus?\n"
        "Balas dengan:\n"
        "✅ `ya` - Untuk menetapkan deadline\n"
        "❌ `tidak` - Gunakan deadline default (7 hari dari sekarang)\n\n"
        "Ketik /cancel untuk membatalkan.",
        parse_mode="Markdown"
    )
    return ASK_DEADLINE

async def ask_deadline_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menanyakan apakah user ingin menambahkan deadline."""
    choice = update.message.text.lower().strip()
    
    if choice == 'tidak':
        default_deadline = datetime.now() + timedelta(days=7)
        context.user_data['task_data']['deadline'] = default_deadline
        await update.message.reply_text(
            f"⏳ Deadline otomatis di-set: {default_deadline.strftime('%Y-%m-%d %H:%M')}\n\n"
            "📌 Silakan berikan catatan tambahan untuk tugas ini:\n"
            "(Anda bisa mengetik '-' jika tidak ada catatan)\n\n"
            "Ketik /cancel untuk membatalkan."
        )
        return TASK_NOTE_FINAL
    elif choice == 'ya':
        await update.message.reply_text(
            "⏰ Masukkan deadline tugas dalam format:\n"
            "`YYYY-MM-DD HH:MM`\n"
            "(Contoh: `2025-12-31 23:59`)\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_DEADLINE
    else:
        await update.message.reply_text(
            "⚠️ Mohon ketik `ya` atau `tidak`.\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return ASK_DEADLINE

async def get_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima deadline tugas dan memvalidasinya."""
    try:
        deadline_str = update.message.text.strip()
        deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
        
        if deadline_dt < datetime.now():
            await update.message.reply_text(
                "⚠️ Deadline tidak bisa di masa lalu. Mohon masukkan tanggal/waktu di masa depan.\n\n"
                "Ketik /cancel untuk membatalkan."
            )
            return GET_DEADLINE
        
        context.user_data['task_data']['deadline'] = deadline_dt
        
        await update.message.reply_text(
            "📌 Silakan berikan catatan tambahan untuk tugas ini:\n"
            "(Anda bisa mengetik '-' jika tidak ada catatan)\n\n"
            "Ketik /cancel untuk membatalkan."
        )
        return TASK_NOTE_FINAL
    except ValueError:
        await update.message.reply_text(
            "⚠️ Format deadline salah. Mohon gunakan format:\n"
            "`YYYY-MM-DD HH:MM`\n"
            "(Contoh: `2025-12-31 23:59`)\n\n"
            "Ketik /cancel untuk membatalkan.",
            parse_mode="Markdown"
        )
        return GET_DEADLINE

async def save_task_and_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menyimpan tugas ke database dan mengirim notifikasi."""
    user_id = update.effective_user.id
    task_data = context.user_data.get('task_data')
    
    if not task_data:
        await update.message.reply_text("❌ Data tugas tidak ditemukan. Silakan mulai ulang dengan /addtask.")
        return ConversationHandler.END
    
    # Ambil note dari pesan user
    note = update.message.text.strip()
    if note == "-":
        note = "Tidak ada catatan"
    
    task_data['note'] = note
    
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            # Simpan ke database
            cur.execute(
                """INSERT INTO tasks (
                    creator_id, 
                    title, 
                    recipients, 
                    deadline, 
                    note
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING id""",
                (
                    task_data['creator_id'],
                    task_data['title'],
                    task_data['recipients'],
                    task_data['deadline'],
                    task_data['note']
                )
            )
            task_id = cur.fetchone()[0]
            conn.commit()
            
            logger.info(f"Task #{task_id} created by user {user_id}")
            
            # Kirim notifikasi ke semua penerima
            recipients = task_data['recipients'].split()
            for recipient_id in recipients:
                try:
                    await context.bot.send_message(
                        chat_id=int(recipient_id),
                        text=f"📋 **TUGAS BARU** #{task_id}\n\n"
                             f"📛 **Judul:** {task_data['title']}\n"
                             f"👤 **Pemberi tugas:** {task_data['creator_name']}\n"
                             f"⏰ **Deadline:** {task_data['deadline'].strftime('%Y-%m-%d %H:%M')}\n"
                             f"📝 **Catatan:** {task_data['note']}\n\n"
                             f"Gunakan perintah `/done{task_id}` untuk menandai selesai.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify recipient {recipient_id}: {e}")
            
            await update.message.reply_text(
                f"✅ Tugas berhasil dibuat!\n"
                f"📋 ID Tugas: `#{task_id}`\n"
                f"👥 Penerima: {len(recipients)} orang\n"
                f"⏰ Deadline: {task_data['deadline'].strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error saving task: {e}")
        await update.message.reply_text("❌ Gagal menyimpan tugas. Silakan coba lagi.")
    finally:
        if conn:
            conn.close()
        if 'task_data' in context.user_data:
            del context.user_data['task_data']
    
    return ConversationHandler.END

async def cancel_task_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan pembuatan tugas."""
    if 'task_data' in context.user_data:
        del context.user_data['task_data']
    
    await update.message.reply_text(
        "❌ Pembuatan tugas dibatalkan.",
        reply_markup=None
    )
    return ConversationHandler.END

async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menandai tugas sebagai selesai."""
    user_id = update.effective_user.id
    
    # Parse task ID dari command
    if context.args and context.args[0].isdigit():  # Format: /done 123
        task_id = int(context.args[0])
    elif update.message.text.startswith('/done') and len(update.message.text) > 5:  # Format: /done123
        task_id_str = update.message.text[5:].strip()
        if task_id_str.isdigit():
            task_id = int(task_id_str)
        else:
            await update.message.reply_text(
                "⚠️ Format perintah tidak valid. Gunakan:\n"
                "`/done123` atau `/done 123`",
                parse_mode="Markdown"
            )
            return
    else:
        await update.message.reply_text(
            "⚠️ Format perintah tidak valid. Gunakan:\n"
            "`/done123` atau `/done 123`",
            parse_mode="Markdown"
        )
        return
    
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            # Dapatkan info tugas
            cur.execute(
                """SELECT creator_id, recipients, title, status 
                FROM tasks 
                WHERE id = %s""",
                (task_id,)
            )
            task = cur.fetchone()
            
            if not task:
                await update.message.reply_text(f"❌ Tugas #{task_id} tidak ditemukan.")
                return
            
            creator_id, recipients_str, title, status = task
            
            # Validasi status
            if status == 'completed':
                await update.message.reply_text(f"ℹ️ Tugas `{title}` (#{task_id}) sudah selesai sebelumnya.", parse_mode="Markdown")
                return
            elif status == 'cancelled':
                await update.message.reply_text(f"❌ Tugas `{title}` (#{task_id}) telah dibatalkan.", parse_mode="Markdown")
                return
            
            # Validasi kepemilikan
            recipients = recipients_str.split()
            if str(user_id) not in recipients and user_id != creator_id:
                await update.message.reply_text("❌ Anda tidak memiliki izin untuk menandai tugas ini sebagai selesai.")
                return
            
            # Update status tugas
            cur.execute(
                """UPDATE tasks 
                SET status = 'completed', 
                    completed_at = NOW() 
                WHERE id = %s""",
                (task_id,)
            )
            conn.commit()
            
            await update.message.reply_text(
                f"✅ Tugas `{title}` (#{task_id}) berhasil ditandai selesai!",
                parse_mode="Markdown"
            )
            
            # Kirim notifikasi ke pembuat tugas (jika berbeda dengan yang menandai selesai)
            if user_id != creator_id:
                try:
                    await context.bot.send_message(
                        chat_id=creator_id,
                        text=f"🎉 **TUGAS SELESAI** #{task_id}\n\n"
                             f"📛 **Judul:** {title}\n"
                             f"👤 **Diselesaikan oleh:** {update.effective_user.full_name}\n"
                             f"🕒 **Waktu penyelesaian:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify creator {creator_id}: {e}")
            
            logger.info(f"Task #{task_id} marked as completed by {user_id}")
    except Exception as e:
        logger.error(f"Error marking task #{task_id} as done: {e}")
        await update.message.reply_text("❌ Gagal menandai tugas. Silakan coba lagi.")
    finally:
        if conn:
            conn.close()

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar tugas user."""
    user_id = update.effective_user.id
    
    conn = None
    try:
        conn = DatabaseManager.get_connection()
        with conn.cursor() as cur:
            # Dapatkan semua tugas yang terkait dengan user (sebagai creator atau penerima)
            cur.execute(
                """SELECT id, title, creator_id, recipients, deadline, status, created_at, completed_at 
                FROM tasks 
                WHERE creator_id = %s OR recipients LIKE %s
                ORDER BY 
                    CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                    deadline ASC""",
                (user_id, f'%{user_id}%')
            )
            tasks = cur.fetchall()
            
            if not tasks:
                await update.message.reply_text("📭 Anda belum memiliki tugas.")
                return
            
            # Format pesan
            message = ["📋 **DAFTAR TUGAS ANDA**\n"]
            
            pending_tasks = []
            completed_tasks = []
            
            for task in tasks:
                (task_id, title, creator_id, recipients_str, deadline, status, created_at, 
                 completed_at) = task
                
                recipients = recipients_str.split()
                is_creator = user_id == creator_id
                role = "Pembuat" if is_creator else "Penerima"
                
                task_info = (
                    f"🔹 **ID:** #{task_id}\n"
                    f"📛 **Judul:** {title}\n"
                    f"👤 **Peran:** {role}\n"
                    f"⏰ **Deadline:** {deadline.strftime('%Y-%m-%d %H:%M')}\n"
                )
                
                if status == 'pending':
                    time_left = deadline - datetime.now()
                    if time_left < timedelta(0):
                        status_text = "⌛ TERLAMBAT"
                    else:
                        days = time_left.days
                        hours = time_left.seconds // 3600
                        status_text = f"⏳ {days} hari {hours} jam tersisa"
                    
                    task_info += f"🔄 **Status:** {status_text}\n"
                    pending_tasks.append(task_info)
                elif status == 'completed':
                    task_info += (
                        f"✅ **Status:** SELESAI\n"
                        f"🕒 **Selesai pada:** {completed_at.strftime('%Y-%m-%d %H:%M')}\n"
                    )
                    completed_tasks.append(task_info)
            
            if pending_tasks:
                message.append("\n⏳ **TUGAS PENDING**\n")
                message.extend(pending_tasks)
            
            if completed_tasks:
                message.append("\n✅ **TUGAS SELESAI**\n")
                message.extend(completed_tasks)
            
            await update.message.reply_text(
                "\n―――――――――――――――――――\n".join(message),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error listing tasks for user {user_id}: {e}")
        await update.message.reply_text("❌ Gagal mengambil daftar tugas. Silakan coba lagi.")
    finally:
        if conn:
            conn.close()

# BASIC COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start."""
    user = update.effective_user
    
    # Register/update user data
    try:
        await register_user(
            user.id,
            user.username or "N/A",
            user.first_name or "N/A",
            user.last_name or ""
        )
    except Exception as e:
        logger.error(f"Error registering user {user.id}: {e}")
    
    commands = [
        "/start - Tampilkan pesan ini",
        "/addtask - Buat tugas baru",
        "/listtasks - Lihat daftar tugas Anda",
        "/done [ID] - Tandai tugas selesai (contoh: /done 123)",
        "/help - Tampilkan bantuan"
    ]
    
    if is_owner(user.id):
        commands.extend([
            "/adduser [ID] - Tambah user baru",
            "/listusers - Lihat daftar user"
        ])
    
    await update.message.reply_text(
        f"👋 Halo {user.full_name}!\n\n"
        "🤖 **Task Management Bot**\n\n"
        "📌 **Perintah yang tersedia:**\n" + 
        "\n".join([f"• {cmd}" for cmd in commands]),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /help."""
    await start(update, context)

# ======================================
# REMINDER SYSTEM
# ======================================
async def send_reminder(chat_id: int, task_id: int, title: str, deadline: datetime, reminder_type: str):
    """Mengirim pesan pengingat ke user."""
    if not bot_application:
        logger.error("Bot application not initialized for reminders")
        return
    
    try:
        time_left = deadline - datetime.now()
        
        if reminder_type == "overdue":
            status_text = "⌛ TERLAMBAT!"
        else:
            status_text = f"⏳ {reminder_type} menuju deadline"
        
        await bot_application.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 **PENGINGAT TUGAS** #{task_id}\n\n"
                 f"📛 **Judul:** {title}\n"
                 f"⏰ **Deadline:** {deadline.strftime('%Y-%m-%d %H:%M')}\n"
                 f"🚨 **Status:** {status_text}\n\n"
                 f"Segera selesaikan dan tandai dengan `/done{task_id}`",
            parse_mode="Markdown"
        )
        
        logger.info(f"Sent {reminder_type} reminder for task #{task_id} to {chat_id}")
    except Exception as e:
        logger.error(f"Failed to send reminder to {chat_id}: {e}")

def reminder_worker(app: ApplicationBuilder):
    """Background worker untuk mengecek dan mengirim reminder."""
    global bot_application
    bot_application = app
    
    while True:
        conn = None
        try:
            conn = DatabaseManager.get_connection()
            with conn.cursor() as cur:
                # Dapatkan tugas yang perlu diingatkan
                cur.execute(
                    """SELECT id, creator_id, title, recipients, deadline, last_reminded_at 
                    FROM tasks 
                    WHERE status = 'pending' 
                    AND deadline > NOW() - INTERVAL '7 days'
                    AND (last_reminded_at IS NULL OR last_reminded_at < NOW() - INTERVAL '1 hour')
                    ORDER BY deadline ASC"""
                )
                tasks = cur.fetchall()
                
                current_time = datetime.now()
                
                for task in tasks:
                    task_id, creator_id, title, recipients_str, deadline, last_reminded = task
                    recipients = recipients_str.split()
                    
                    # Tentukan jenis reminder
                    time_left = deadline - current_time
                    reminder_type = None
                    
                    if time_left < timedelta(0):
                        reminder_type = "overdue"
                    elif time_left <= timedelta(hours=1):
                        reminder_type = "1 JAM"
                    elif time_left <= timedelta(days=1):
                        reminder_type = "1 HARI"
                    elif time_left <= timedelta(days=3):
                        reminder_type = "3 HARI"
                    elif time_left <= timedelta(days=7):
                        reminder_type = "7 HARI"
                    
                    if reminder_type:
                        # Kirim ke semua penerima dan pembuat
                        all_recipients = set(recipients + [str(creator_id)])
                        
                        for recipient_id in all_recipients:
                            try:
                                app.create_task(
                                    send_reminder(
                                        int(recipient_id),
                                        task_id,
                                        title,
                                        deadline,
                                        reminder_type
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to schedule reminder for {recipient_id}: {e}")
                        
                        # Update last_reminded_at
                        with conn.cursor() as update_cur:
                            update_cur.execute(
                                "UPDATE tasks SET last_reminded_at = %s WHERE id = %s",
                                (current_time, task_id)
                            )
                            conn.commit()
            
            logger.info("Reminder cycle completed")
        except Exception as e:
            logger.error(f"Error in reminder worker: {e}")
        finally:
            if conn:
                conn.close()
        
        time.sleep(REMINDER_CHECK_INTERVAL_SECONDS)

# ======================================
# FLASK WEBHOOK SETUP
# ======================================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """Endpoint health check untuk Render."""
    return "🤖 Task Management Bot is Running | Owner ID: {}".format(OWNER_ID)

@flask_app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def telegram_webhook():
    """Handler untuk webhook Telegram."""
    if not bot_application:
        return "Bot not initialized", 503
    
    try:
        update = Update.de_json(await request.get_json(), bot_application.bot)
        await bot_application.process_update(update)
        return "ok"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "error", 500

def run_flask_server():
    """Menjalankan Flask server di thread terpisah."""
    logger.info(f"Starting Flask server on port {PORT}")
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ======================================
# MAIN APPLICATION
# ======================================
def main():
    """Fungsi utama untuk menjalankan bot."""
    global bot_application
    
    # Inisialisasi database
    try:
        DatabaseManager.init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        exit(1)
    
    # Jalankan Flask server di background
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    # Buat bot application
    bot_application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(lambda app: logger.info("Bot application initialized"))
        .build()
    )
    
    # Tambahkan handlers
    # Admin commands
    bot_application.add_handler(CommandHandler("adduser", add_user))
    bot_application.add_handler(CommandHandler("listusers", list_users))
    
    # Task management conversation
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
    bot_application.add_handler(CommandHandler("done", done_task))
    bot_application.add_handler(MessageHandler(filters.Regex(r'^/done\d+$'), done_task))
    bot_application.add_handler(CommandHandler("listtasks", list_tasks))
    
    # Basic commands
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CommandHandler("help", help_command))
    
    # Jalankan reminder worker di background
    reminder_thread = threading.Thread(
        target=reminder_worker,
        args=(bot_application,),
        daemon=True
    )
    reminder_thread.start()
    
    # Mulai bot
    logger.info("Starting bot...")
    
    if WEBHOOK_URL:
        try:
            bot_application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
                url_path=BOT_TOKEN
            )
            logger.info(f"Bot running in webhook mode: {WEBHOOK_URL}")
        except Exception as e:
            logger.critical(f"Webhook setup failed: {e}")
            exit(1)
    else:
        try:
            bot_application.run_polling()
            logger.info("Bot running in polling mode")
        except Exception as e:
            logger.critical(f"Polling failed: {e}")
            exit(1)

if __name__ == "__main__":
    main()
