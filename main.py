import logging
import psycopg2 # Menggantikan sqlite3
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

# Load .env file (hanya untuk pengembangan lokal, di Render pakai Environment Variables)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL") # Variabel baru untuk koneksi database

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# DB setup
def init_db():
    """Menginisialisasi tabel database di PostgreSQL."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        # BIGINT untuk user_id karena ID Telegram bisa sangat besar
        c.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY)")
        # SERIAL untuk auto-increment di PostgreSQL
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                creator_id BIGINT,
                title TEXT,
                recipients TEXT,
                deadline TEXT,
                note TEXT,
                status TEXT DEFAULT 'ongoing'
            )"""
        )
        conn.commit()
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to connect or initialize database: {e}")
    finally:
        if conn:
            conn.close()

# Authorization
def is_owner(user_id: int) -> bool:
    """Memeriksa apakah user adalah OWNER_ID."""
    return user_id == OWNER_ID

def is_user(user_id: int) -> bool:
    """Memeriksa apakah user terdaftar (termasuk OWNER_ID)."""
    if user_id == OWNER_ID:
        return True
    
    conn = None
    result = False
    try:
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        c.execute("SELECT 1 FROM users WHERE user_id=%s", (user_id,))
        result = c.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking user {user_id}: {e}")
    finally:
        if conn:
            conn.close()
    return result

# States untuk ConversationHandler
JUDUL, PENERIMA, DEADLINE, KETERANGAN = range(4)
task_data = {} # Dictionary sementara untuk menyimpan data tugas selama percakapan

# Owner commands
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menambahkan user ke daftar user yang diizinkan."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Maaf, hanya pemilik bot yang bisa menggunakan perintah ini.")
        return
    
    if not context.args:
        await update.message.reply_text("Contoh: /adduser 123456789")
        return
    
    conn = None
    try:
        user_id = int(context.args[0])
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        # ON CONFLICT DO NOTHING mencegah error jika user_id sudah ada
        c.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        conn.commit()
        await update.message.reply_text(f"User {user_id} ditambahkan.")
        logger.info(f"User {user_id} added by owner {update.effective_user.id}.")
    except ValueError:
        await update.message.reply_text("ID user tidak valid. Mohon masukkan angka.")
    except Exception as e:
        logger.error(f"Error adding user {user_id} by {update.effective_user.id}: {e}")
        await update.message.reply_text("Gagal menambahkan user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menghapus user dari daftar user yang diizinkan."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Maaf, hanya pemilik bot yang bisa menggunakan perintah ini.")
        return
    
    if not context.args:
        await update.message.reply_text("Contoh: /removeuser 123456789")
        return
    
    conn = None
    try:
        user_id = int(context.args[0])
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
        conn.commit()
        await update.message.reply_text(f"User {user_id} dihapus.")
        logger.info(f"User {user_id} removed by owner {update.effective_user.id}.")
    except ValueError:
        await update.message.reply_text("ID user tidak valid. Mohon masukkan angka.")
    except Exception as e:
        logger.error(f"Error removing user {user_id} by {update.effective_user.id}: {e}")
        await update.message.reply_text("Gagal menghapus user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

async def list_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar user yang terdaftar."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Maaf, hanya pemilik bot yang bisa menggunakan perintah ini.")
        return
    
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        users = c.fetchall()
        
        if users:
            msg = "📋 User terdaftar:\n" + "\n".join(str(u[0]) for u in users)
        else:
            msg = "Tidak ada user terdaftar."
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error listing users by {update.effective_user.id}: {e}")
        await update.message.reply_text("Gagal menampilkan user. Cek log untuk detail.")
    finally:
        if conn:
            conn.close()

# Tambah tugas (Conversation Handler)
async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memulai alur pembuatan tugas."""
    if not is_user(update.effective_user.id):
        await update.message.reply_text("Maaf, kamu tidak terdaftar sebagai pengguna bot ini. Silakan hubungi pemilik bot.")
        return ConversationHandler.END
    
    # Inisialisasi data tugas untuk user ini
    task_data[update.effective_user.id] = {}
    await update.message.reply_text("📝 Oke, mari buat tugas baru. Apa judul tugasnya?")
    return JUDUL

async def input_judul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima judul tugas."""
    task_data[update.effective_user.id]["judul"] = update.message.text
    await update.message.reply_text("👥 Siapa penerima tugas ini? (Mohon gunakan ID numerik Telegram mereka, pisahkan dengan spasi jika lebih dari satu. Contoh: 123456789 987654321)")
    return PENERIMA

async def input_penerima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima daftar penerima tugas."""
    recipients_input = update.message.text.strip()
    
    # Validasi bahwa input adalah ID numerik
    recipient_ids = []
    for r_id_str in recipients_input.split():
        if not r_id_str.isdigit():
            await update.message.reply_text(f"'{r_id_str}' bukan ID numerik yang valid. Mohon masukkan ID numerik Telegram yang dipisahkan spasi.")
            return PENERIMA # Kembali ke langkah ini jika ada invalid input
        recipient_ids.append(r_id_str)

    task_data[update.effective_user.id]["recipients"] = " ".join(recipient_ids) # Simpan sebagai string ID dipisahkan spasi
    await update.message.reply_text("⏰ Kapan deadline tugas ini? Format: YYYY-MM-DD HH:MM (Contoh: 2025-07-01 15:00)")
    return DEADLINE

async def input_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima deadline tugas dan memvalidasinya."""
    try:
        deadline = datetime.strptime(update.message.text.strip(), "%Y-%m-%d %H:%M")
        task_data[update.effective_user.id]["deadline"] = deadline
        await update.message.reply_text("📌 Terakhir, apa keterangan atau detail tugasnya?")
        return KETERANGAN
    except ValueError: # Tangani ValueError dari strptime jika format salah
        await update.message.reply_text("Format deadline salah. Mohon ikuti format YYYY-MM-DD HH:MM. Contoh: 2025-07-01 15:00")
        return DEADLINE

async def input_keterangan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menerima keterangan tugas, menyimpan ke DB, dan mengirim notifikasi."""
    uid = update.effective_user.id
    task_data[uid]["keterangan"] = update.message.text.strip()
    d = task_data[uid]

    conn = None
    task_id = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        c = conn.cursor()
        c.execute(
            "INSERT INTO tasks (creator_id, title, recipients, deadline, note) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (uid, d["judul"], d["recipients"], d["deadline"].isoformat(), d["keterangan"]),
        )
        task_id = c.fetchone()[0] # Dapatkan ID tugas yang baru dibuat
        conn.commit()
        logger.info(f"Task #{task_id} created by user {uid}.")
    except Exception as e:
        logger.error(f"Error saving task for user {uid}: {e}")
        await update.message.reply_text("Gagal membuat tugas. Mohon coba lagi nanti atau hubungi admin.")
        return ConversationHandler.END
    finally:
        if conn:
            conn.close()

    if task_id: # Hanya kirim notifikasi jika tugas berhasil disimpan
        # Kirim notifikasi ke penerima
        # Penting: 'recipients' sekarang diasumsikan berisi ID numerik
        for recipient_id_str in d["recipients"].split():
            try:
                # Mengubah ID string menjadi integer untuk chat_id
                recipient_chat_id = int(recipient_id_str)
                await context.bot.send_message(
                    chat_id=recipient_chat_id,
                    text=f"📋 **Tugas Baru!**\n\n"
                         f"**ID Tugas:** #{task_id}\n"
                         f"**Judul:** {d['judul']}\n"
                         f"**Deadline:** {d['deadline'].strftime('%Y-%m-%d %H:%M')}\n"
                         f"**Keterangan:** {d['keterangan']}\n\n"
                         f"Silakan kerjakan. Balas dengan `/done{task_id}` jika selesai.",
                    parse_mode="Markdown" # Menggunakan Markdown untuk formatting teks
                )
                logger.info(f"Notification sent for task #{task_id} to {recipient_chat_id}")
            except Exception as e:
                logger.warning(f"Gagal kirim notifikasi tugas #{task_id} ke {recipient_id_str}: {e}")
                # Anda bisa memilih untuk memberi tahu pembuat tugas jika ada penerima yang gagal
        
        await update.message.reply_text(f"✅ Tugas berhasil dibuat dengan ID #{task_id} dan notifikasi dikirimkan.")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan alur ConversationHandler."""
    user_id = update.effective_user.id
    if user_id in task_data:
        del task_data[user_id] # Hapus data tugas sementara
    await update.message.reply_text("❌ Pembuatan tugas dibatalkan.")
    return ConversationHandler.END

# Start & test
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menanggapi perintah /start."""
    await update.message.reply_text("Halo! Selamat datang di Task Manager Bot. Gunakan /addtask untuk membuat tugas baru.")
    if is_owner(update.effective_user.id):
        await update.message.reply_text("Sebagai pemilik, Anda juga bisa menggunakan: /adduser, /removeuser, /listuser.")
    elif is_user(update.effective_user.id):
        await update.message.reply_text("Anda terdaftar sebagai pengguna bot ini.")
    else:
        await update.message.reply_text("Anda belum terdaftar. Mohon hubungi pemilik bot untuk didaftarkan.")


# Main app
def main():
    """Fungsi utama untuk menjalankan bot."""
    # Pastikan DATABASE_URL telah diatur sebelum menginisialisasi DB
    if not DATABASE_URL:
        logger.critical("DATABASE_URL environment variable is not set. Exiting.")
        exit(1) # Keluar jika tidak ada DATABASE_URL
        
    init_db() # Inisialisasi database saat bot dimulai
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ConversationHandler untuk alur pembuatan tugas
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            JUDUL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_judul)],
            PENERIMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_penerima)],
            DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_deadline)],
            KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_keterangan)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Menambahkan semua handler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler) # Handler untuk alur pembuatan tugas
    app.add_handler(CommandHandler("adduser", add_user))
    app.add_handler(CommandHandler("removeuser", remove_user))
    app.add_handler(CommandHandler("listuser", list_user))

    logger.info("Bot started polling...")
    app.run_polling() # Menjalankan bot dalam mode polling

if __name__ == "__main__":
    main()
