import os
import logging
import time
import psycopg2
from datetime import datetime, timedelta
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

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database configuration
class DatabaseManager:
    @staticmethod
    def get_connection(max_retries=3, retry_delay=2):
        """Establish database connection with retry logic"""
        for attempt in range(max_retries):
            try:
                conn = psycopg2.connect(
                    os.getenv("DATABASE_URL"),
                    sslmode="require",
                    connect_timeout=5
                )
                logger.info("✅ Database connection established")
                return conn
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(retry_delay)

    @staticmethod
    def init_db():
        """Initialize database tables"""
        commands = (
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                registered_at TIMESTAMP DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                creator_id BIGINT REFERENCES users(user_id),
                title TEXT NOT NULL,
                description TEXT,
                recipients TEXT NOT NULL,
                deadline TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        try:
            with DatabaseManager.get_connection() as conn:
                with conn.cursor() as cur:
                    for command in commands:
                        cur.execute(command)
                    conn.commit()
            logger.info("Database tables initialized")
        except Exception as e:
            logger.critical(f"Database init failed: {e}")
            raise

# Conversation states
(
    TASK_TITLE,
    TASK_DESCRIPTION,
    ASK_RECIPIENTS,
    GET_RECIPIENTS,
    ASK_DEADLINE,
    GET_DEADLINE,
    CONFIRMATION
) = range(7)

# Helper functions
async def register_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    """Register or update user in database"""
    try:
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name
                    """,
                    (user_id, username, first_name, last_name)
                )
                conn.commit()
    except Exception as e:
        logger.error(f"Error registering user {user_id}: {e}")
        raise

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    try:
        await register_user(
            user.id,
            user.username or "N/A",
            user.first_name or "N/A",
            user.last_name or ""
        )
    except Exception as e:
        logger.error(f"User registration failed: {e}")

    await update.message.reply_text(
        "🤖 Task Manager Bot\n\n"
        "Available commands:\n"
        "/addtask - Create new task\n"
        "/listtasks - Show your tasks\n"
        "/done <id> - Mark task as done"
    )

# Task creation handlers
async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start task creation flow"""
    user = update.effective_user
    try:
        await register_user(
            user.id,
            user.username or "N/A",
            user.first_name or "N/A",
            user.last_name or ""
        )
    except Exception as e:
        await update.message.reply_text("❌ Failed to register user")
        return ConversationHandler.END

    context.user_data['task'] = {
        'creator_id': user.id,
        'creator_name': user.full_name
    }

    await update.message.reply_text("📝 Enter task title:")
    return TASK_TITLE

async def get_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task title"""
    title = update.message.text.strip()
    if len(title) > 100:
        await update.message.reply_text("❌ Title too long (max 100 chars)")
        return TASK_TITLE

    context.user_data['task']['title'] = title
    await update.message.reply_text("📄 Enter task description:")
    return TASK_DESCRIPTION

async def get_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task description"""
    context.user_data['task']['description'] = update.message.text.strip()
    await update.message.reply_text(
        "👥 Add recipients?\n\n"
        "Reply:\n"
        "✅ 'yes' - Add other recipients\n"
        "❌ 'no' - Just for yourself"
    )
    return ASK_RECIPIENTS

async def ask_recipients_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle recipients choice"""
    choice = update.message.text.lower().strip()
    if choice == 'no':
        context.user_data['task']['recipients'] = str(update.effective_user.id)
        await update.message.reply_text(
            "⏰ Set deadline?\n\n"
            "Reply:\n"
            "✅ 'yes' - Set custom deadline\n"
            "❌ 'no' - Default (7 days from now)"
        )
        return ASK_DEADLINE
    elif choice == 'yes':
        await update.message.reply_text("🔢 Enter recipient IDs (space separated):")
        return GET_RECIPIENTS
    else:
        await update.message.reply_text("⚠️ Please answer 'yes' or 'no'")
        return ASK_RECIPIENTS

async def get_task_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task recipients"""
    recipients = update.message.text.strip()
    if not all(r.isdigit() for r in recipients.split()):
        await update.message.reply_text("❌ Invalid ID format (numbers only)")
        return GET_RECIPIENTS

    context.user_data['task']['recipients'] = recipients
    await update.message.reply_text(
        "⏰ Set deadline?\n\n"
        "Reply:\n"
        "✅ 'yes' - Set custom deadline\n"
        "❌ 'no' - Default (7 days from now)"
    )
    return ASK_DEADLINE

async def ask_deadline_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deadline choice"""
    choice = update.message.text.lower().strip()
    if choice == 'no':
        context.user_data['task']['deadline'] = datetime.now() + timedelta(days=7)
        return await confirm_task(update, context)
    elif choice == 'yes':
        await update.message.reply_text("📅 Enter deadline (YYYY-MM-DD HH:MM):")
        return GET_DEADLINE
    else:
        await update.message.reply_text("⚠️ Please answer 'yes' or 'no'")
        return ASK_DEADLINE

async def get_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task deadline"""
    try:
        deadline = datetime.strptime(update.message.text.strip(), "%Y-%m-%d %H:%M")
        if deadline < datetime.now():
            await update.message.reply_text("❌ Deadline must be in the future")
            return GET_DEADLINE

        context.user_data['task']['deadline'] = deadline
        return await confirm_task(update, context)
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD HH:MM")
        return GET_DEADLINE

async def confirm_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task confirmation"""
    task = context.user_data['task']
    await update.message.reply_text(
        f"📋 Task Summary:\n\n"
        f"Title: {task['title']}\n"
        f"Description: {task['description']}\n"
        f"Recipients: {task['recipients']}\n"
        f"Deadline: {task['deadline'].strftime('%Y-%m-%d %H:%M')}\n\n"
        "Confirm?\n\n"
        "✅ 'confirm' - Save task\n"
        "❌ 'cancel' - Abort"
    )
    return CONFIRMATION

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save task to database"""
    task = context.user_data['task']
    try:
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (
                        creator_id, title, description, recipients, deadline
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        task['creator_id'],
                        task['title'],
                        task['description'],
                        task['recipients'],
                        task['deadline']
                    )
                )
                task_id = cur.fetchone()[0]
                conn.commit()

        await update.message.reply_text(f"✅ Task saved (ID: {task_id})")
        
        # Notify recipients
        for recipient in task['recipients'].split():
            try:
                await context.bot.send_message(
                    chat_id=int(recipient),
                    text=f"📣 New Task!\n\n"
                         f"Title: {task['title']}\n"
                         f"Deadline: {task['deadline'].strftime('%Y-%m-%d %H:%M')}\n\n"
                         f"Use /done{task_id} when complete"
                )
            except Exception as e:
                logger.error(f"Failed to notify {recipient}: {e}")

    except Exception as e:
        logger.error(f"Error saving task: {e}")
        await update.message.reply_text("❌ Failed to save task")
    finally:
        if 'task' in context.user_data:
            del context.user_data['task']

    return ConversationHandler.END

async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel task creation"""
    if 'task' in context.user_data:
        del context.user_data['task']
    await update.message.reply_text("❌ Task creation cancelled")
    return ConversationHandler.END

def main():
    """Start the bot"""
    # Initialize database with retry
    for attempt in range(3):
        try:
            DatabaseManager.init_db()
            break
        except Exception as e:
            logger.error(f"DB Init Attempt {attempt + 1} failed: {e}")
            if attempt == 2:
                logger.critical("Failed to initialize database")
                return
            time.sleep(5)

    # Create bot application
    application = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    
    # Task creation conversation
    task_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addtask", start_add_task)],
        states={
            TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_title)],
            TASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_description)],
            ASK_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_recipients_choice)],
            GET_RECIPIENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_recipients)],
            ASK_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_deadline_choice)],
            GET_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_deadline)],
            CONFIRMATION: [
                MessageHandler(filters.Regex(r'^confirm$'), save_task),
                MessageHandler(filters.Regex(r'^cancel$'), cancel_task)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_task)],
    )
    application.add_handler(task_conv_handler)

    # Start bot
    logger.info("Bot started")
    application.run_polling()

if __name__ == '__main__':
    main()
