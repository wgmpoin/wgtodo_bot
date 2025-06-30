import os
from datetime import datetime, timedelta
import pytz # Pastikan Anda punya pip install pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)
from supabase import create_client
from flask import Flask, request

# --- Konfigurasi ---
TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- Inisialisasi ---
bot_app = Application.builder().token(TOKEN).build()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
flask_app = Flask(__name__)

# --- Helper Functions ---

# Fungsi untuk mendapatkan user ID dan chat ID dari username
async def get_user_info_by_username(username):
    response = supabase.table("users").select("id, chat_id").eq("username", username).single().execute()
    if response.data:
        return response.data['id'], response.data['chat_id']
    return None, None

# Fungsi untuk mendapatkan username dari user ID
async def get_username_by_id(user_id):
    response = supabase.table("users").select("username").eq("id", user_id).single().execute()
    if response.data:
        return response.data['username']
    return None

# Fungsi untuk mendapatkan nama lengkap dari user ID
async def get_fullname_by_id(user_id):
    response = supabase.table("users").select("name").eq("id", user_id).single().execute()
    if response.data:
        return response.data['name']
    return None

# --- Command Handlers ---

# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    user_name = user.full_name
    user_username = user.username # Ambil username
    chat_id = update.effective_chat.id # Ambil chat_id

    # Simpan/update user di tabel 'users'
    supabase.table("users").upsert({
        "id": user_id,
        "name": user_name,
        "username": user_username,
        "chat_id": chat_id # Pastikan chat_id tersimpan
    }).execute()

    await update.message.reply_text(f"✅ Halo {user.first_name}! Bot siap pakai. Anda bisa beri dan kelola tugas.")

# Command: /add [@penerima] [YYYY-MM-DD] [task_text]
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text(
                "Contoh: /add @username_penerima 2025-12-31 Beli bahan presentasi"
            )
            return

        # Parsing argumen
        assignee_username_raw = args[0]
        if not assignee_username_raw.startswith('@'):
            await update.message.reply_text("Format penerima salah. Gunakan @username.")
            return
        assignee_username = assignee_username_raw[1:] # Hapus '@'

        deadline_str = args[1]
        try:
            # Menggunakan pytz untuk membuat datetime object aware of timezone (UTC disarankan)
            # Anda mungkin perlu mengadaptasi ini jika deadline diinput dalam zona waktu lain
            deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
            # Set to end of day in UTC for simplicity if no time is provided
            deadline = deadline.replace(hour=23, minute=59, second=59, microsecond=0)
            deadline = pytz.utc.localize(deadline)
        except ValueError:
            await update.message.reply_text("Format tanggal salah. Gunakan YYYY-MM-DD.")
            return

        task_text = " ".join(args[2:])
        if not task_text:
            await update.message.reply_text("Teks tugas tidak boleh kosong.")
            return

        # Dapatkan ID dan Chat ID penerima
        assignee_id, assignee_chat_id = await get_user_info_by_username(assignee_username)

        if not assignee_id:
            await update.message.reply_text(
                f"❌ Pengguna @{assignee_username} tidak ditemukan atau belum pernah `/start` bot ini."
            )
            return

        # Simpan ke tabel 'tasks'
        response = supabase.table("tasks").insert({
            "task_text": task_text,
            "deadline": deadline.isoformat(), # Simpan dalam format ISO 8601
            "assigned_by": update.effective_user.id,
            "assigned_to": assignee_id,
            "status": "pending"
        }).execute()

        if response.data:
            task_id = response.data[0]['id'] # Ambil ID tugas yang baru dibuat
            await update.message.reply_text(
                f"✅ Tugas berhasil ditambahkan!\n"
                f"Untuk @{assignee_username}:\n"
                f"📝 {task_text}\n"
                f"🗓️ Deadline: {deadline.strftime('%d-%m-%Y')}"
            )
            
            # Kirim notifikasi ke penerima tugas
            if assignee_chat_id:
                assigner_name = update.effective_user.full_name
                notif_message = (
                    f"🔔 Anda mendapatkan tugas baru dari *{assigner_name}*:\n\n"
                    f"📝 *{task_text}*\n"
                    f"🗓️ Deadline: _{deadline.strftime('%d-%m-%Y')}_\n\n"
                    f"Ketik /list_my untuk melihat daftar tugas Anda."
                )
                await context.bot.send_message(chat_id=assignee_chat_id, text=notif_message, parse_mode='Markdown')
            
        else:
            await update.message.reply_text("❌ Gagal menyimpan tugas ke database. Coba lagi nanti.")

    except Exception as e:
        print(f"Error in add_task: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat menambahkan tugas. Pastikan format benar.")

# Command: /list_my (Untuk penerima tugas)
async def list_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Ambil tugas yang di-assign ke user ini dan statusnya 'pending'
    response = supabase.table("tasks") \
        .select("id, task_text, deadline, assigned_by") \
        .eq("assigned_to", user_id) \
        .eq("status", "pending") \
        .order("deadline", desc=False) \
        .execute()

    tasks = response.data
    if not tasks:
        await update.message.reply_text("Anda tidak memiliki tugas yang pending saat ini. 🎉")
        return

    message = "📝 *Daftar Tugas Anda (Pending)*:\n\n"
    for task in tasks:
        task_id = task['id']
        task_text = task['task_text']
        deadline = datetime.fromisoformat(task['deadline']).strftime('%d-%m-%Y')
        assigner_username = await get_fullname_by_id(task['assigned_by']) or "Pengguna Tidak Dikenal"

        message += (
            f"• ID: `{str(task_id)[:8]}`\n" # Menampilkan sebagian ID untuk identifikasi
            f"  Dari: {assigner_username}\n"
            f"  Tugas: *{task_text}*\n"
            f"  Deadline: {deadline}\n"
        )
        # Tambahkan tombol 'Selesai'
        keyboard = [
            [InlineKeyboardButton("✅ Selesai", callback_data=f"finish_task_{task_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        message = "" # Reset message untuk setiap tugas
    
    if message: # Kirim sisa message jika ada (kasus 1 tugas)
         await update.message.reply_text(message, parse_mode='Markdown')

# Command: /list_given (Untuk pemberi tugas)
async def list_given_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Ambil tugas yang diberikan oleh user ini
    response = supabase.table("tasks") \
        .select("id, task_text, deadline, assigned_to, status") \
        .eq("assigned_by", user_id) \
        .order("created_at", desc=True) \
        .execute()

    tasks = response.data
    if not tasks:
        await update.message.reply_text("Anda belum memberikan tugas apapun. 🤔")
        return

    message = "📋 *Daftar Tugas yang Anda Berikan*:\n\n"
    for task in tasks:
        task_id = task['id']
        task_text = task['task_text']
        deadline = datetime.fromisoformat(task['deadline']).strftime('%d-%m-%Y')
        assignee_username = await get_fullname_by_id(task['assigned_to']) or "Pengguna Tidak Dikenal"
        status = task['status'].capitalize()

        message += (
            f"• ID: `{str(task_id)[:8]}`\n"
            f"  Untuk: {assignee_username}\n"
            f"  Tugas: *{task_text}*\n"
            f"  Deadline: {deadline}\n"
            f"  Status: `{status}`\n"
        )
        
        keyboard_buttons = []
        if task['status'] == 'pending':
            keyboard_buttons.append(InlineKeyboardButton("❌ Batalkan Tugas", callback_data=f"cancel_task_{task_id}"))
        
        if keyboard_buttons:
            reply_markup = InlineKeyboardMarkup([keyboard_buttons])
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, parse_mode='Markdown')
        
        message = "" # Reset message untuk setiap tugas

# --- Callback Query Handler (Untuk tombol inline) ---
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Wajib untuk menghilangkan status loading di tombol Telegram

    data = query.data
    user_id = query.from_user.id

    try:
        if data.startswith("finish_task_"):
            task_id = data.split("_")[2]

            # Cek apakah user adalah penerima tugas
            task_resp = supabase.table("tasks").select("assigned_to, task_text, assigned_by").eq("id", task_id).single().execute()
            if not task_resp.data or task_resp.data['assigned_to'] != user_id:
                await query.edit_message_text("❌ Anda tidak punya izin untuk menyelesaikan tugas ini.")
                return

            # Update status di database
            update_resp = supabase.table("tasks").update({"status": "finished"}).eq("id", task_id).execute()
            
            if update_resp.data:
                await query.edit_message_text(f"✅ Tugas '{task_resp.data['task_text']}' berhasil ditandai Selesai.")
                # Kirim notifikasi ke pemberi tugas
                assigner_chat_id = (await supabase.table("users").select("chat_id").eq("id", task_resp.data['assigned_by']).single().execute()).data['chat_id']
                if assigner_chat_id:
                    assignee_name = query.from_user.full_name
                    await context.bot.send_message(
                        chat_id=assigner_chat_id,
                        text=f"🎉 Tugas '{task_resp.data['task_text']}' telah diselesaikan oleh *{assignee_name}*!"
                    )
            else:
                await query.edit_message_text("❌ Gagal menandai tugas selesai. Coba lagi.")

        elif data.startswith("cancel_task_"):
            task_id = data.split("_")[2]

            # Cek apakah user adalah pemberi tugas
            task_resp = supabase.table("tasks").select("assigned_by, task_text, assigned_to").eq("id", task_id).single().execute()
            if not task_resp.data or task_resp.data['assigned_by'] != user_id:
                await query.edit_message_text("❌ Anda tidak punya izin untuk membatalkan tugas ini.")
                return

            # Update status di database
            update_resp = supabase.table("tasks").update({"status": "cancelled"}).eq("id", task_id).execute()

            if update_resp.data:
                await query.edit_message_text(f"❌ Tugas '{task_resp.data['task_text']}' berhasil dibatalkan.")
                # Kirim notifikasi ke penerima tugas
                assignee_chat_id = (await supabase.table("users").select("chat_id").eq("id", task_resp.data['assigned_to']).single().execute()).data['chat_id']
                if assignee_chat_id:
                    assigner_name = query.from_user.full_name
                    await context.bot.send_message(
                        chat_id=assignee_chat_id,
                        text=f"🚫 Tugas '{task_resp.data['task_text']}' telah dibatalkan oleh *{assigner_name}*."
                    )
            else:
                await query.edit_message_text("❌ Gagal membatalkan tugas. Coba lagi.")

    except Exception as e:
        print(f"Error in handle_button_click: {e}")
        await query.edit_message_text("❌ Terjadi kesalahan saat memproses aksi Anda.")


# --- Webhook Handler (Untuk Render) ---
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    # Pastikan ini menggunaka bot_app.bot (instance Bot di dalam Application)
    update = Update.de_json(request.get_json(), bot_app.bot)
    bot_app.process_update(update)
    return "OK", 200

# --- Main Execution ---
if __name__ == "__main__":
    # Register command handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("add", add_task))
    bot_app.add_handler(CommandHandler("list_my", list_my_tasks))
    bot_app.add_handler(CommandHandler("list_given", list_given_tasks))

    # Register callback query handler for inline buttons
    bot_app.add_handler(CallbackQueryHandler(handle_button_click))
    
    # Run Flask app (which handles webhooks)
    # Ini akan dijalankan oleh Gunicorn di Render
    # Untuk local testing, Anda bisa uncomment baris di bawah ini dan comment baris flask_app.run()
    # bot_app.run_polling(poll_interval=3) # Contoh untuk mode polling saat testing lokal
    
    # Jalankan Flask app untuk webhook (di Render, Gunicorn yang memanggil ini)
    # Ini adalah titik masuk utama aplikasi web Anda
    flask_app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))
