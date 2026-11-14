import os

# ==================== ⚠️ تنظیمات (از Environment Variables) ====================
TOKEN = os.environ.get("TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
YOUR_TELEGRAM_ID = int(os.environ.get("YOUR_TELEGRAM_ID", "0"))

if not TOKEN:
    raise ValueError("TOKEN environment variable not set!")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable not set!")
# ============================================================================

import logging
import sqlite3
import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from groq import Groq
from telegram import Update, ChatMemberUpdated, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters, CallbackQueryHandler
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

client_ai = Groq(api_key=GROQ_API_KEY)
DB_PATH = "jarvis.db"

# ==================== گیف‌ها ====================
GIF_WARN = "https://media.giphy.com/media/l41Yf04O28F8Z0jBK/giphy.gif"
GIF_MUTE = "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif"
GIF_INTRO = "https://media.giphy.com/media/26tn33aiTi1jkl6H6/giphy.gif"

# ==================== Health Check (برای Render Web Service) ====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Jarvis is alive!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"✅ Health server روی پورت {port} بالا اومد.")
    server.serve_forever()

# ==================== دیتابیس ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS muted_users (chat_id INTEGER, user_id INTEGER, muted_by INTEGER, muted_at TEXT, PRIMARY KEY (chat_id, user_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS chat_settings (chat_id INTEGER PRIMARY KEY, welcome_gif TEXT, anti_link INTEGER, rules TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS warns (chat_id INTEGER, user_id INTEGER, count INTEGER, PRIMARY KEY (chat_id, user_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bios (user_id INTEGER PRIMARY KEY, bio_text TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS message_stats (chat_id INTEGER, user_id INTEGER, count INTEGER, PRIMARY KEY (chat_id, user_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ads (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, category TEXT, title TEXT, description TEXT, date TEXT)""")
    conn.commit()
    conn.close()

# ... (همه توابع دیتابیس مثل قبل: add_ad, get_ads_by_category, get_setting, set_setting, ...)
# (کد رو دست نزن — همون نسخه قبلی که داشتی)

def add_ad(user_id, category, title, description):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d")
    c.execute("INSERT INTO ads (user_id, category, title, description, date) VALUES (?, ?, ?, ?, ?)", (user_id, category, title, description, date))
    conn.commit(); conn.close()

def get_ads_by_category(category):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, user_id, title, description, date FROM ads WHERE category=? ORDER BY id DESC LIMIT 10", (category,))
    r = c.fetchall(); conn.close(); return r

def get_all_categories():
    return ["🌐 طراحی سایت", "🔧 تعمیرات", "🛒 خرید و فروش", "🎨 گرافیک و طراحی", "📱 موبایل و اپلیکیشن", "📚 آموزش"]

def get_setting(chat_id, column):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"SELECT {column} FROM chat_settings WHERE chat_id=?", (chat_id,))
    r = c.fetchone(); conn.close(); return r[0] if r else None

def set_setting(chat_id, column, value):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(f"INSERT INTO chat_settings (chat_id, {column}) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET {column}=excluded.{column}", (chat_id, value))
    conn.commit(); conn.close()

def is_muted(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT 1 FROM muted_users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = c.fetchone(); conn.close(); return r is not None

def mute_user(chat_id, user_id, muted_by):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO muted_users (chat_id, user_id, muted_by, muted_at) VALUES (?, ?, ?, ?)", (chat_id, user_id, muted_by, datetime.now().isoformat()))
    conn.commit(); conn.close()

def unmute_user(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("DELETE FROM muted_users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit(); conn.close()

def add_warn(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = c.fetchone(); count = r[0] + 1 if r else 1
    c.execute("INSERT OR REPLACE INTO warns (chat_id, user_id, count) VALUES (?, ?, ?)", (chat_id, user_id, count))
    conn.commit(); conn.close(); return count

def reset_warns(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit(); conn.close()

def update_stats(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT count FROM message_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = c.fetchone(); count = r[0] + 1 if r else 1
    c.execute("INSERT OR REPLACE INTO message_stats (chat_id, user_id, count) VALUES (?, ?, ?)", (chat_id, user_id, count))
    conn.commit(); conn.close()

def get_bio(user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT bio_text FROM bios WHERE user_id=?", (user_id,))
    r = c.fetchone(); conn.close(); return r[0] if r else None

def set_bio_db(user_id, text):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bios (user_id, bio_text) VALUES (?, ?)", (user_id, text))
    conn.commit(); conn.close()

# ==================== توابع کمکی ====================
async def is_admin(chat_id, user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

def pick_model():
    try:
        models = client_ai.models.list()
        return "openai/gpt-oss-20b" if any(m.id == "openai/gpt-oss-20b" for m in models.data) else "qwen/qwen3.8-27b"
    except Exception as e:
        logger.error(f"model pick failed: {e}")
        return "openai/gpt-oss-20b"

# ... (همه handlerها و توابع ai_chat, start_private, private_callbacks, private_messages, main_message_handler رو از نسخه قبلی که برات فرستادم کپی کن)

# ==================== main ====================
def main():
    init_db()

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_private, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(private_callbacks, pattern="^(priv_|adcat_|viewcat_)"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_messages))
    app.add_handler(CommandHandler("start", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(ChatMemberHandler(greet_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, main_message_handler))

    print("🤖 جارویس روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
