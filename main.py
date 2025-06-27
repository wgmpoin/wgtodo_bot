import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

# Config
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not all([TOKEN, WEBHOOK_URL]):
    raise RuntimeError("Missing required environment variables!")

# Initialize
app = Application.builder().token(TOKEN).build()
flask_app = Flask(__name__)

# [Rest of your code... tetap sama seperti sebelumnya]
