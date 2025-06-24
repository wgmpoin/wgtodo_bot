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

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 🔒 Ambil Token dari Environment Variables (Server/Local)
try:
    BOT_TOKEN = os.environ['BOT_TOKEN']  # Wajib ada di server
    OWNER_ID = int(os.environ['OWNER_ID'])
except KeyError as e:
    logger.error(f"⛔ Error: Variabel env tidak ditemukan - {e}")
    exit(1)  # Berhenti jika token tidak ada

# 🛠️ Inisialisasi Database
def init_db():
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                 id INTEGER PRIMARY KEY,
                 creator_id INTEGER,
                 title TEXT,
                 recipients TEXT,
                 deadline TEXT,
                 note TEXT)''')
    conn.commit()
    conn.close()

# ✅ Cek Hak Akses User
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_user(user_id: int) -> bool:
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute('SELECT 1 FROM users WHERE user_id=?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None or is_owner(user_id)

# 🎛️ Handler Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🤖 Bot siap! Gunakan /help untuk petunjuk.')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 **Perintah**:
/start - Mulai bot
/help - Bantuan ini
/addtask - Buat tugas baru (hanya user terdaftar)
"""
    await update.message.reply_text(help_text)

# ... (Tambahkan handler lainnya sesuai kebutuhan)

def main():
    init_db()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # ➕ Daftarkan Command
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    
    # ... (Tambahkan handler lainnya)
    
    logger.info("🚀 Bot berjalan...")
    app.run_polling()

if __name__ == '__main__':
    main()
