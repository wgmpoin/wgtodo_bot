import logging
import os
import psycopg2
from datetime import datetime
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

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
JUDUL, PENERIMA, DEADLINE, KETERANGAN = range(4)
task_data = {}

class DatabaseManager:
    """Handles all database operations with connection pooling and error handling"""
    
    @staticmethod
    def get_connection():
        """Establish database connection with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = psycopg2.connect(
                    DATABASE_URL,
                    sslmode="require",
                    connect_timeout=5,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10
                )
                logger.info("Database connection established")
                return conn
            except psycopg2.OperationalError as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)

    @staticmethod
    def init_db():
        """Initialize database tables"""
        try:
            with DatabaseManager.get_connection() as conn:
                with conn.cursor() as cur:
                    # Create tables if not exists
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            registered_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            creator_id BIGINT REFERENCES users(user_id),
                            title TEXT NOT NULL,
                            recipients TEXT NOT NULL,
                            deadline TIMESTAMP NOT NULL,
                            note TEXT,
                            status TEXT DEFAULT 'ongoing',
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    conn.commit()
                    logger.info("Database tables initialized")
        except Exception as e:
            logger.critical(f"Database initialization failed: {e}")
            raise

# Authorization
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

async def is_user(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    try:
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
                return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"User check failed for {user_id}: {e}")
        return False

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    welcome_msg = (
        f"Halo {user.mention_markdown()}! 👋\n"
        "Saya adalah Task Manager Bot.\n\n"
        "🔹 Gunakan /addtask untuk membuat tugas baru\n"
        "🔹 /listtasks untuk melihat tugas aktif"
    )
    
    if is_owner(user.id):
        welcome_msg += "\n\n👑 Anda adalah pemilik bot ini. Fitur tambahan:\n"
        welcome_msg += "/adduser - Tambah pengguna baru\n"
        welcome_msg += "/removeuser - Hapus pengguna\n"
        welcome_msg += "/listusers - Lihat daftar pengguna"
    
    await update.message.reply_markdown(welcome_msg)

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add new user (owner only)"""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Hanya owner yang bisa menambah pengguna")
        return

    if not context.args:
        await update.message.reply_text("Contoh: /adduser 123456789")
        return

    try:
        user_id = int(context.args[0])
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (user_id,)
                )
                conn.commit()
        
        await update.message.reply_text(f"✅ User {user_id} berhasil ditambahkan")
        logger.info(f"User {user_id} added by {update.effective_user.id}")
    except ValueError:
        await update.message.reply_text("⚠️ ID harus berupa angka")
    except Exception as e:
        logger.error(f"Add user failed: {e}")
        await update.message.reply_text("❌ Gagal menambahkan user")

# [Tambahkan handler lainnya di sini...]

def main():
    """Main application setup"""
    try:
        # Initialize database
        DatabaseManager.init_db()

        # Build bot application
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # Add command handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("adduser", add_user))
        # [Tambahkan handler lainnya di sini...]

        # Add conversation handler for task creation
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("addtask", start_add_task)],
            states={
                JUDUL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_judul)],
                PENERIMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_penerima)],
                DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_deadline)],
                KETERANGAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_keterangan)]
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            conversation_timeout=300  # 5 menit timeout
        )
        app.add_handler(conv_handler)

        # Start the bot
        logger.info("Starting bot in polling mode...")
        app.run_polling(
            poll_interval=1,
            timeout=20,
            allowed_updates=Update.ALL_TYPES
        )
    except Exception as e:
        logger.critical(f"Application failed: {e}")
    finally:
        logger.info("Application stopped")

if __name__ == "__main__":
    main()
