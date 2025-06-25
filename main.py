import os
import logging
import threading
import psycopg2
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
REMINDER_INTERVAL_SECONDS = 3600  # Cek reminder setiap 1 jam

# Tambahkan baris ini untuk DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")

# Validasi variabel penting
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable must be set! Exiting.")
    exit(1)
if not DATABASE_URL:
    logger.critical("DATABASE_URL environment variable must be set! Exiting.")
    exit(1)
if OWNER_ID == 0:
    logger.warning("OWNER_ID not set, admin commands will be disabled.")

# ======================================
# DATABASE FUNCTIONS
# ======================================

def get_db_connection():
    """Membuka koneksi database."""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None

def init_db():
    """Menginisialisasi tabel database jika belum ada."""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    reminder_text TEXT NOT NULL,
                    remind_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            logger.info("Database table 'reminders' checked/created successfully.")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
        finally:
            cur.close()
            conn.close()
    else:
        logger.error("Could not get database connection for initialization.")

# Panggil inisialisasi DB saat aplikasi dimulai
init_db()

# ======================================
# BOT STATES
# ======================================
SET_REMINDER_TEXT, SET_REMINDER_TIME = range(2)

# ======================================
# BOT COMMAND HANDLERS
# ======================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menanggapi perintah /start."""
    user = update.effective_user
    await update.message.reply_html(
        f"Halo {user.mention_html()}! Saya adalah bot pengingat. "
        "Anda bisa menyuruh saya untuk mengingatkan Anda tentang sesuatu."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menanggapi perintah /help."""
    help_text = (
        "Berikut adalah perintah yang bisa Anda gunakan:\n"
        "/start - Memulai bot\n"
        "/help - Menampilkan pesan bantuan ini\n"
        "/setreminder - Menyetel pengingat baru\n"
        "/myreminders - Menampilkan semua pengingat Anda\n"
        "/cancel - Membatalkan proses penyetelan pengingat"
    )
    await update.message.reply_text(help_text)

async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Memulai alur penyetelan pengingat."""
    await update.message.reply_text(
        "Oke, saya akan membantu Anda menyetel pengingat. "
        "Apa yang ingin Anda ingatkan? (Misal: 'beli susu', 'rapat jam 10 pagi')"
    )
    return SET_REMINDER_TEXT

async def receive_reminder_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima teks pengingat dari pengguna."""
    context.user_data['reminder_text'] = update.message.text
    await update.message.reply_text(
        f"Baik, saya akan mengingatkan Anda tentang '{context.user_data['reminder_text']}'.\n"
        "Kapan Anda ingin diingatkan? (Misal: 'besok jam 9 pagi', '2 jam lagi', '2025-12-31 23:59')"
    )
    return SET_REMINDER_TIME

async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima waktu pengingat dan menyimpannya ke database."""
    user_input_time = update.message.text
    reminder_text = context.user_data.get('reminder_text')
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not reminder_text:
        await update.message.reply_text(
            "Maaf, saya tidak menemukan teks pengingat. "
            "Silakan mulai lagi dengan /setreminder."
        )
        return ConversationHandler.END

    try:
        # Coba parse waktu relatif (misal: "2 jam lagi", "besok")
        remind_at = parse_relative_time(user_input_time)
        if not remind_at:
            # Coba parse waktu absolut (misal: "2025-12-31 23:59", "besok jam 9 pagi")
            remind_at = parse_absolute_time(user_input_time)

        if not remind_at:
            await update.message.reply_text(
                "Maaf, saya tidak bisa memahami waktu yang Anda berikan. "
                "Coba format seperti 'besok jam 9 pagi' atau '2 jam lagi' atau '2025-12-31 23:59'."
            )
            return SET_REMINDER_TIME # Tetap di state ini untuk mencoba lagi

        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO reminders (user_id, chat_id, reminder_text, remind_at) VALUES (%s, %s, %s, %s)",
                    (user_id, chat_id, reminder_text, remind_at)
                )
                conn.commit()
                await update.message.reply_text(
                    f"Pengingat disetel untuk '{reminder_text}' pada "
                    f"{remind_at.strftime('%Y-%m-%d %H:%M:%S %Z%z')}."
                )
            except Exception as e:
                logger.error(f"Error saving reminder to DB: {e}")
                await update.message.reply_text(
                    "Maaf, ada masalah saat menyimpan pengingat Anda."
                )
            finally:
                cur.close()
                conn.close()
        else:
            await update.message.reply_text(
                "Maaf, saya tidak bisa terhubung ke database saat ini."
            )

    except Exception as e:
        logger.error(f"Error parsing time or saving reminder: {e}")
        await update.message.reply_text(
            "Terjadi kesalahan. Pastikan format waktu Anda benar."
        )

    context.user_data.clear() # Bersihkan data pengguna setelah selesai
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Membatalkan alur penyetelan pengingat."""
    await update.message.reply_text(
        "Proses penyetelan pengingat dibatalkan."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def my_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menampilkan semua pengingat yang akan datang untuk pengguna."""
    user_id = update.effective_user.id
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            now = datetime.now(remind_at.tzinfo if remind_at else None) # Gunakan timezone dari remind_at jika ada, atau UTC
            cur.execute(
                "SELECT id, reminder_text, remind_at FROM reminders WHERE user_id = %s AND remind_at > %s ORDER BY remind_at ASC",
                (user_id, now)
            )
            reminders = cur.fetchall()

            if reminders:
                response_text = "Pengingat Anda yang akan datang:\n"
                for r_id, text, remind_at in reminders:
                    response_text += (
                        f"- ID: {r_id}, '{text}' pada "
                        f"{remind_at.strftime('%Y-%m-%d %H:%M:%S %Z%z')}\n"
                    )
                await update.message.reply_text(response_text)
            else:
                await update.message.reply_text("Anda tidak memiliki pengingat yang akan datang.")
        except Exception as e:
            logger.error(f"Error fetching reminders from DB: {e}")
            await update.message.reply_text(
                "Maaf, ada masalah saat mengambil pengingat Anda."
            )
        finally:
            cur.close()
            conn.close()
    else:
        await update.message.reply_text(
            "Maaf, saya tidak bisa terhubung ke database saat ini."
        )

# ======================================
# TIME PARSING UTILITIES
# ======================================

def parse_relative_time(text: str) -> datetime | None:
    """Mencoba mengurai waktu relatif seperti '2 jam lagi', '30 menit lagi'."""
    now = datetime.now()
    text_lower = text.lower()

    if "menit lagi" in text_lower:
        try:
            minutes = int(text_lower.split(" ")[0])
            return now + timedelta(minutes=minutes)
        except ValueError:
            pass
    elif "jam lagi" in text_lower:
        try:
            hours = int(text_lower.split(" ")[0])
            return now + timedelta(hours=hours)
        except ValueError:
            pass
    elif "hari lagi" in text_lower:
        try:
            days = int(text_lower.split(" ")[0])
            return now + timedelta(days=days)
        except ValueError:
            pass
    elif "besok" in text_lower:
        # Jika hanya 'besok', set ke besok jam 9 pagi sebagai default
        if "jam" not in text_lower:
            return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        # Jika ada 'besok jam X', parse jamnya
        try:
            parts = text_lower.split("jam")
            hour_str = parts[1].strip().split(" ")[0]
            hour = int(hour_str)
            return (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        except (ValueError, IndexError):
            pass
    return None

def parse_absolute_time(text: str) -> datetime | None:
    """Mencoba mengurai waktu absolut seperti '2025-12-31 23:59', 'besok jam 9 pagi'."""
    now = datetime.now()
    text_lower = text.lower()

    # Format YYYY-MM-DD HH:MM
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    # Format YYYY-MM-DD HH:MM:SS
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    # Format 'besok jam X pagi/sore'
    if "besok jam" in text_lower:
        try:
            parts = text_lower.split("jam")
            time_part = parts[1].strip()
            hour = int(time_part.split(" ")[0])
            if "pagi" in time_part and hour == 12: # Handle 12 pagi (midnight)
                hour = 0
            elif "sore" in time_part and hour < 12: # Handle PM
                hour += 12
            
            # Jika user tidak menyebutkan 'pagi' atau 'sore', asumsikan AM/PM berdasarkan waktu saat ini
            # Jika waktu yang diinput sudah lewat hari ini, asumsikan besok
            target_time = (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
            return target_time
        except (ValueError, IndexError):
            pass
    
    # Format 'jam X pagi/sore' (untuk hari ini atau besok)
    if "jam" in text_lower:
        try:
            parts = text_lower.split("jam")
            time_part = parts[1].strip()
            hour = int(time_part.split(" ")[0])
            
            is_am = "pagi" in time_part
            is_pm = "sore" in time_part or "malam" in time_part

            if is_pm and hour < 12:
                hour += 12
            elif is_am and hour == 12: # 12 pagi adalah tengah malam
                hour = 0
            
            target_datetime = now.replace(hour=hour, minute=0, second=0, microsecond=0)

            # Jika waktu yang diinput sudah lewat hari ini, setel untuk besok
            if target_datetime <= now and not (is_am or is_pm): # Jika tidak ada AM/PM, dan waktu sudah lewat
                # Coba asumsikan PM jika waktu sekarang AM dan waktu input AM
                if now.hour < 12 and hour < 12:
                    target_datetime = now.replace(hour=hour + 12, minute=0, second=0, microsecond=0)
                    if target_datetime <= now: # Jika masih lewat, berarti besok
                        target_datetime += timedelta(days=1)
                else: # Jika waktu sekarang sudah PM atau waktu input sudah PM, langsung besok
                    target_datetime += timedelta(days=1)
            elif target_datetime <= now and (is_am or is_pm): # Jika ada AM/PM, dan waktu sudah lewat
                target_datetime += timedelta(days=1) # Langsung setel untuk besok
            
            return target_datetime
        except (ValueError, IndexError):
            pass

    return None


# ======================================
# REMINDER CHECKER
# ======================================

async def check_reminders(application: ApplicationBuilder) -> None:
    """Fungsi yang berjalan secara periodik untuk memeriksa pengingat."""
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            now = datetime.now()
            # Ambil pengingat yang sudah waktunya dan belum dikirim
            cur.execute(
                "SELECT id, user_id, chat_id, reminder_text, remind_at FROM reminders WHERE remind_at <= %s ORDER BY remind_at ASC",
                (now,)
            )
            reminders_to_send = cur.fetchall()

            bot = application.bot
            for r_id, user_id, chat_id, text, remind_at in reminders_to_send:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 Pengingat: {text}"
                    )
                    # Hapus pengingat setelah dikirim
                    cur.execute("DELETE FROM reminders WHERE id = %s", (r_id,))
                    conn.commit()
                    logger.info(f"Reminder ID {r_id} sent and deleted.")
                except Exception as send_e:
                    logger.error(f"Error sending reminder ID {r_id}: {send_e}")
                    # Jika gagal kirim, mungkin bot tidak punya akses ke chat, hapus saja
                    cur.execute("DELETE FROM reminders WHERE id = %s", (r_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"Error checking reminders: {e}")
        finally:
            cur.close()
            conn.close()
    else:
        logger.error("Could not get database connection for reminder check.")

def start_reminder_checker(application: ApplicationBuilder) -> None:
    """Memulai thread untuk memeriksa pengingat secara periodik."""
    def run_checker():
        while True:
            application.create_task(check_reminders(application))
            threading.Event().wait(REMINDER_INTERVAL_SECONDS) # Tunggu sebelum cek lagi

    checker_thread = threading.Thread(target=run_checker, daemon=True)
    checker_thread.start()
    logger.info("Reminder checker thread started.")

# ======================================
# FLASK WEBHOOK
# ======================================
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    """Menangani update dari Telegram melalui webhook."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# ======================================
# MAIN FUNCTION
# ======================================
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    global application

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation Handler untuk /setreminder
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("setreminder", set_reminder)],
        states={
            SET_REMINDER_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_text)],
            SET_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myreminders", my_reminders))
    application.add_handler(conv_handler) # Tambahkan conversation handler

    # Start the reminder checker in a separate thread
    start_reminder_checker(application)

    # Set webhook untuk Render
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Pastikan ini disetel di Render
    if WEBHOOK_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
        logger.info(f"Webhook set to {WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        logger.warning("WEBHOOK_URL not set. Bot will run in polling mode (not recommended for Render).")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
