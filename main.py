import os
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
from supabase import create_client
from typing import Dict, Any

# ================= CONFIGURATION =================
# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
CONFIG = {
    'TOKEN': os.getenv("BOT_TOKEN"),
    'WEBHOOK_URL': os.getenv("WEBHOOK_URL").rstrip('/'),
    'SUPABASE_URL': os.getenv("SUPABASE_URL"),
    'SUPABASE_KEY': os.getenv("SUPABASE_KEY"),
    'PORT': int(os.getenv("PORT", 8080))
}

# Validate config
if not all(CONFIG.values()):
    missing = [k for k, v in CONFIG.items() if not v]
    raise RuntimeError(f"Missing environment variables: {missing}")

# ================= SUPABASE SETUP =================
try:
    # Special configuration for Render compatibility
    supabase = create_client(
        CONFIG['SUPABASE_URL'],
        CONFIG['SUPABASE_KEY'],
        options={
            'auto_refresh_token': False,
            'persist_session': False
        }
    )
    logger.info("✅ Supabase client initialized")
except Exception as e:
    logger.error(f"❌ Supabase init failed: {e}")
    raise

# ================= TELEGRAM BOT =================
app = Application.builder().token(CONFIG['TOKEN']).build()
flask_app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    try:
        user = update.effective_user
        data = {
            'id': user.id,
            'name': user.full_name,
            'username': user.username
        }
        
        # Upsert user data
        supabase.table('users').upsert(data).execute()
        
        await update.message.reply_text(
            f"👋 Hello {user.mention_markdown()}!\n"
            "Type /add [task] to create a new task"
        )
    except Exception as e:
        logger.error(f"User registration failed: {e}")
        await update.message.reply_text("⚠️ Service temporarily unavailable")

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command"""
    try:
        task_text = ' '.join(context.args)
        if not task_text:
            await update.message.reply_text("Please specify a task")
            return
            
        task_data = {
            'user_id': update.effective_user.id,
            'task': task_text,
            'completed': False
        }
        
        supabase.table('tasks').insert(task_data).execute()
        await update.message.reply_text(f"✅ Added: {task_text}")
    except Exception as e:
        logger.error(f"Task creation failed: {e}")
        await update.message.reply_text("⚠️ Failed to add task")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add_task))

# ================= WEBHOOK SETUP =================
@flask_app.route("/webhook", methods=["POST"])
def webhook() -> tuple[str, int]:
    """Handle Telegram webhook"""
    try:
        update = Update.de_json(request.get_json(), app.bot)
        asyncio.create_task(app.process_update(update))
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}")
        return "Error", 500

@flask_app.route("/")
def health_check() -> tuple[str, int]:
    """Health check endpoint for Render"""
    return "Bot is running", 200

# ================= INITIALIZATION =================
async def setup_webhook():
    """Initialize webhook"""
    try:
        await app.initialize()
        webhook_url = f"{CONFIG['WEBHOOK_URL']}/webhook"
        await app.bot.set_webhook(webhook_url)
        logger.info(f"Webhook configured: {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook setup failed: {e}")
        raise

if __name__ == "__main__":
    # Initialize
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Run setup
        loop.run_until_complete(setup_webhook())
        
        # Start Flask server
        flask_app.run(
            host="0.0.0.0",
            port=CONFIG['PORT'],
            debug=False
        )
    except Exception as e:
        logger.critical(f"Application failed: {e}")
    finally:
        loop.close()
