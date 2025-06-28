import os
import asyncio
import logging
from typing import Dict, Any
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request
from supabase import create_client

# ================= INITIAL CONFIG =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables with validation
REQUIRED_ENV = {
    'BOT_TOKEN': os.getenv("BOT_TOKEN"),
    'WEBHOOK_URL': os.getenv("WEBHOOK_URL"),
    'SUPABASE_URL': os.getenv("SUPABASE_URL"),
    'SUPABASE_KEY': os.getenv("SUPABASE_KEY")
}

if not all(REQUIRED_ENV.values()):
    missing = [k for k, v in REQUIRED_ENV.items() if not v]
    raise RuntimeError(f"Missing environment variables: {missing}")

# ================= COMPATIBLE SUPABASE CLIENT =================
try:
    # Special configuration for Render's environment
    supabase = create_client(
        REQUIRED_ENV['SUPABASE_URL'],
        REQUIRED_ENV['SUPABASE_KEY'],
        options={
            'auto_refresh_token': False,
            'persist_session': False,
            'headers': {
                'Authorization': f"Bearer {REQUIRED_ENV['SUPABASE_KEY']}",
                'apikey': REQUIRED_ENV['SUPABASE_KEY']
            }
        }
    )
    logger.info("✅ Supabase client initialized successfully")
except Exception as e:
    logger.error(f"❌ Supabase initialization failed: {e}")
    raise

# ================= BOT APPLICATION =================
app = Application.builder().token(REQUIRED_ENV['BOT_TOKEN']).build()
flask_app = Flask(__name__)

# ================= COMMAND HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with Supabase integration"""
    try:
        user = update.effective_user
        user_data = {
            'id': user.id,
            'name': user.full_name,
            'username': user.username
        }
        
        # Upsert user data to Supabase
        response = supabase.table('users').upsert(user_data).execute()
        if hasattr(response, 'error') and response.error:
            raise Exception(response.error)
            
        await update.message.reply_text(
            f"👋 Hello {user.mention_markdown()}!\n"
            "🚀 Bot is ready!\n"
            "Type /add [task] to create a new task"
        )
    except Exception as e:
        logger.error(f"User registration error: {e}")
        await update.message.reply_text("⚠️ Service temporary unavailable")

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command with task storage"""
    try:
        task_text = ' '.join(context.args)
        if not task_text:
            await update.message.reply_text("Please specify a task after /add")
            return
            
        task_data = {
            'user_id': update.effective_user.id,
            'description': task_text,
            'completed': False
        }
        
        # Insert task to Supabase
        response = supabase.table('tasks').insert(task_data).execute()
        if hasattr(response, 'error') and response.error:
            raise Exception(response.error)
            
        await update.message.reply_text(f"✅ Task added: {task_text}")
    except Exception as e:
        logger.error(f"Task creation error: {e}")
        await update.message.reply_text("⚠️ Failed to add task")

# Register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add_task))

# ================= WEBHOOK ENDPOINTS =================
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    """Telegram webhook handler"""
    try:
        update = Update.de_json(request.get_json(), app.bot)
        asyncio.create_task(app.process_update(update))
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return "Error", 500

@flask_app.route("/")
def health_check():
    """Render health check endpoint"""
    return "🤖 Bot is running", 200

# ================= INITIALIZATION =================
async def setup():
    """Initialize webhook and services"""
    try:
        await app.initialize()
        webhook_url = f"{REQUIRED_ENV['WEBHOOK_URL'].rstrip('/')}/webhook"
        await app.bot.set_webhook(webhook_url)
        logger.info(f"🌐 Webhook configured: {webhook_url}")
        
        # Test Supabase connection
        test = supabase.table('users').select('*', count='exact').limit(1).execute()
        logger.info(f"🔌 Supabase connection test: {test.count} records found")
    except Exception as e:
        logger.critical(f"Setup failed: {e}")
        raise

if __name__ == "__main__":
    # Configure event loop for Render
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Run initialization
        loop.run_until_complete(setup())
        
        # Start Flask server
        flask_app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", 8080)),
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        logger.critical(f"Application failed: {e}")
    finally:
        loop.close()
