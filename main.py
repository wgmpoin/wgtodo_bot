import os
import logging
import threading
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

# ======================================
# INITIAL SETUP
# ======================================
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
OWNER_ID = int(os.getenv("OWNER_ID", 0))

# ======================================
# DATABASE MANAGER
# ======================================
class DatabaseManager:
    @staticmethod
    def get_connection():
        """Get PostgreSQL connection with retry logic"""
        try:
            return psycopg2.connect(DATABASE_URL, sslmode="require")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    @staticmethod
    def init_db():
        """Initialize database tables"""
        try:
            with DatabaseManager.get_connection() as conn:
                with conn.cursor() as cur:
                    # Create users table
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            first_name TEXT,
                            last_name TEXT,
                            registered_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    
                    # Create tasks table
                    cur.execute("""
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
                    """)
                    conn.commit()
        except Exception as e:
            logger.critical(f"Database initialization failed: {e}")
            raise

# ======================================
# CONVERSATION STATES
# ======================================
(
    TASK_TITLE,
    TASK_DESCRIPTION,
    ASK_RECIPIENTS,
    GET_RECIPIENTS,
    ASK_DEADLINE,
    GET_DEADLINE,
    CONFIRMATION
) = range(7)

# ======================================
# BOT HANDLERS
# ======================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    await update.message.reply_text(
        "🤖 Task Manager Bot\n\n"
        "Available commands:\n"
        "/addtask - Create new task\n"
        "/listtasks - Show your tasks\n"
        "/done <id> - Mark task as done"
    )

async def register_user(user_id: int, username: str, first_name: str, last_name: str = ""):
    """Register or update user in database"""
    try:
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name
                """, (user_id, username, first_name, last_name))
                conn.commit()
    except Exception as e:
        logger.error(f"Error registering user {user_id}: {e}")
        raise

# TASK CREATION HANDLERS
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

    await update.message.reply_text(
        "📝 Let's create a new task\n\n"
        "1/7 - Enter task title:"
    )
    return TASK_TITLE

async def get_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task title"""
    title = update.message.text.strip()
    if len(title) > 100:
        await update.message.reply_text("❌ Title too long (max 100 chars)")
        return TASK_TITLE

    context.user_data['task']['title'] = title
    await update.message.reply_text(
        "2/7 - Enter task description:"
    )
    return TASK_DESCRIPTION

async def get_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task description"""
    context.user_data['task']['description'] = update.message.text.strip()
    await update.message.reply_text(
        "3/7 - Add recipients?\n\n"
        "Reply with:\n"
        "✅ 'yes' - To add other recipients\n"
        "❌ 'no' - Just for yourself"
    )
    return ASK_RECIPIENTS

async def ask_recipients_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask if user wants to add recipients"""
    choice = update.message.text.lower().strip()
    if choice == 'no':
        context.user_data['task']['recipients'] = str(update.effective_user.id)
        await update.message.reply_text(
            "4/7 - Set deadline?\n\n"
            "Reply with:\n"
            "✅ 'yes' - To set custom deadline\n"
            "❌ 'no' - Use default (7 days from now)"
        )
        return ASK_DEADLINE
    elif choice == 'yes':
        await update.message.reply_text(
            "4/7 - Enter recipient IDs (space separated):\n"
            "(Get IDs with @userinfobot)"
        )
        return GET_RECIPIENTS
    else:
        await update.message.reply_text("⚠️ Please answer 'yes' or 'no'")
        return ASK_RECIPIENTS

async def get_task_recipients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task recipients"""
    recipients = update.message.text.strip()
    if not all(r.isdigit() for r in recipients.split()):
        await update.message.reply_text("❌ Invalid ID format (must be numbers)")
        return GET_RECIPIENTS

    context.user_data['task']['recipients'] = recipients
    await update.message.reply_text(
        "5/7 - Set deadline?\n\n"
        "Reply with:\n"
        "✅ 'yes' - To set custom deadline\n"
        "❌ 'no' - Use default (7 days from now)"
    )
    return ASK_DEADLINE

async def ask_deadline_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask if user wants to set deadline"""
    choice = update.message.text.lower().strip()
    if choice == 'no':
        context.user_data['task']['deadline'] = datetime.now() + timedelta(days=7)
        await show_task_confirmation(update, context)
        return CONFIRMATION
    elif choice == 'yes':
        await update.message.reply_text(
            "6/7 - Enter deadline (YYYY-MM-DD HH:MM):"
        )
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
        await show_task_confirmation(update, context)
        return CONFIRMATION
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD HH:MM")
        return GET_DEADLINE

async def show_task_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task confirmation"""
    task = context.user_data['task']
    await update.message.reply_text(
        f"7/7 - Confirm task:\n\n"
        f"📌 Title: {task['title']}\n"
        f"📝 Description: {task['description']}\n"
        f"👥 Recipients: {task['recipients']}\n"
        f"⏰ Deadline: {task['deadline'].strftime('%Y-%m-%d %H:%M')}\n\n"
        "Reply with:\n"
        "✅ 'confirm' - To save task\n"
        "❌ 'cancel' - To abort"
    )
    return CONFIRMATION

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save task to database"""
    task = context.user_data['task']
    try:
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO tasks (
                        creator_id, title, description, recipients, deadline
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    task['creator_id'],
                    task['title'],
                    task['description'],
                    task['recipients'],
                    task['deadline']
                ))
                task_id = cur.fetchone()[0]
                conn.commit()

        await update.message.reply_text(f"✅ Task saved (ID: {task_id})")
        
        # Notify recipients
        for recipient in task['recipients'].split():
            try:
                await context.bot.send_message(
                    chat_id=int(recipient),
                    text=f"📣 New task assigned!\n\n"
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

# ======================================
# MAIN APPLICATION
# ======================================
def main():
    """Start the bot"""
    # Initialize database
    try:
        DatabaseManager.init_db()
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        return

    # Create application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

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
