import os
import logging
import sqlite3
import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from groq import Groq
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters, CallbackQueryHandler
)

# ==================== ⚠️ تنظیمات ====================
TOKEN = os.environ.get("TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
YOUR_TELEGRAM_ID = 7014212980  # آیدی عددی شما

if not TOKEN:
    raise ValueError("TOKEN environment variable not set!")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable not set!")
# ===================================================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

client_ai = Groq(api_key=GROQ_API_KEY)
DB_PATH = "jarvis.db"

# ==================== گیف‌ها ====================
GIF_WARN = "https://media.giphy.com/media/l41Yf04O28F8Z0jBK/giphy.gif"
GIF_MUTE = "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif"
GIF_INTRO = "https://media.giphy.com/media/26tn33aiTi1jkl6H6/giphy.gif"
GIF_HELP = "https://media.giphy.com/media/l0HlvtIPzPdt2usKs/giphy.gif" 
GIF_SECRETARY = "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif"

# ==================== Health Check (برای Render) ====================
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
    c.execute("""CREATE TABLE IF NOT EXISTS group_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_name TEXT, text TEXT)""")
    conn.commit()
    conn.close()

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

# ==================== ۰. تست‌ها ====================
async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 پونگ! ربات کاملاً سالمه.")

async def test_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    await msg.reply_text("⏳ در حال تست هوش مصنوعی...")
    try:
        # مدل جدید و پیشنهادی خود Groq
        target_model = "openai/gpt-oss-20b"
        res = client_ai.chat.completions.create(
            messages=[{"role": "user", "content": "فقط بنویس: تست موفق بود!"}],
            model=target_model,
        )
        await msg.reply_text(f"✅ اتصال به Groq موفقیت‌آمیز بود!\n\nمدل: `{target_model}`\nپاسخ: {res.choices[0].message.content}", parse_mode="Markdown")
    except Exception as e:
        await msg.reply_text(f"❌ اتصال به Groq شکست خورد:\n\n`{str(e)}`", parse_mode="Markdown")

# ==================== ۱. خوش‌آمدگویی ====================
async def greet_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    was = result.old_chat_member.status in ("left", "kicked")
    now = result.new_chat_member.status in ("member", "administrator", "creator")
    if not (was and now): return
    user = result.new_chat_member.user
    if user.is_bot: return
    chat_id = result.chat.id
    mention = f"[{user.first_name}](tg://user?id={user.id})"
    welcome = f"🎉 سلام {mention} عزیز!\nبه **{result.chat.title}** خوش اومدی 💚"
    gif_id = get_setting(chat_id, "welcome_gif")
    DEFAULT_GIF = "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif"
    try:
        await context.bot.send_animation(chat_id=chat_id, animation=gif_id if gif_id else DEFAULT_GIF, caption=welcome, parse_mode="Markdown")
    except:
        await context.bot.send_message(chat_id=chat_id, text=welcome, parse_mode="Markdown")

# ==================== ۲. سیک و اخطار ====================
async def handle_sik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    text = msg.text.strip().lower()
    if text not in ("سیک", "رفع سیک", "رفع‌سیک"): return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("❌ باید روی پیام کاربر ریپلای کنی")
        return
    if not await is_admin(msg.chat_id, msg.from_user.id, context): return
    target = msg.reply_to_message.from_user
    if target.is_bot: return
    if text == "سیک":
        await context.bot.restrict_chat_member(chat_id=msg.chat_id, user_id=target.id, permissions=ChatPermissions(can_send_messages=False))
        mute_user(msg.chat_id, target.id, msg.from_user.id)
        await msg.reply_animation(animation=GIF_MUTE, caption=f"🔇 {target.first_name} سکوت شد!\n👮 توسط {msg.from_user.first_name}\nبرای رفع: «رفع سیک»", parse_mode="Markdown")
    else:
        await context.bot.restrict_chat_member(chat_id=msg.chat_id, user_id=target.id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_other_messages=True, can_add_web_page_previews=True))
        unmute_user(msg.chat_id, target.id)
        await msg.reply_text(f"🔊 {target.first_name} آزاد شد! می‌تونه چت کنه.", parse_mode="Markdown")

async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("❌ روی پیام کاربر ریپلای کن و بنویس اخطار")
        return
    if not await is_admin(msg.chat_id, msg.from_user.id, context): return
    target = msg.reply_to_message.from_user
    count = add_warn(msg.chat_id, target.id)
    if count >= 3:
        await context.bot.restrict_chat_member(chat_id=msg.chat_id, user_id=target.id, permissions=ChatPermissions(can_send_messages=False))
        mute_user(msg.chat_id, target.id, msg.from_user.id)
        reset_warns(msg.chat_id, target.id)
        await msg.reply_animation(animation=GIF_WARN, caption=f"🚫 {target.first_name} به دلیل ۳ اخطار سکوت شد!", parse_mode="Markdown")
    else:
        await msg.reply_animation(animation=GIF_WARN, caption=f"⚠️ {target.first_name} اخطار گرفت! ({count}/3)", parse_mode="Markdown")

# ==================== ۳. تحلیل گروه و پیام ====================
async def analyze_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ این دستور فقط توی گروه‌ها کار می‌کنه.")
        return

    text_parts = msg.text.strip().split()
    limit = 100
    if len(text_parts) > 2 and text_parts[2].isdigit():
        limit = int(text_parts[2])
    
    await msg.reply_text(f"⏳ در حال تحلیل {limit} پیام اخیر گروه... (چند لحظه صبر کنید)")
    
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT user_name, text FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (msg.chat_id, limit))
        rows = c.fetchall()
        conn.close()
        
        if not rows:
            await msg.reply_text("❌ هنوز پیامی برای تحلیل ذخیره نشده. (ربات از زمان روشن شدنش پیام‌ها رو ذخیره می‌کنه).")
            return
            
        messages = [f"{row[0]}: {row[1]}" for row in rows]
        messages.reverse() 
        chat_text = "\n".join(messages)

        prompt = (
            f"تو یک تحلیل‌گر حرفه‌ای گروه‌های تلگرامی هستی. من {len(rows)} پیام آخر این گروه را به تو می‌دهم. "
            "لطفاً یک گزارش کامل، جذاب و خوانا به زبان فارسی با ایموجی‌های مناسب تهیه کن. "
            "در گزارش به این موارد اشاره کن:\n"
            "۱. موضوع اصلی صحبت‌ها چی بوده؟\n"
            "۲. فعال‌ترین کاربران چه کسانی بودند؟\n"
            "۳. آیا مکالمات مفید و سازنده بوده یا نه؟\n"
            "۴. یک جمع‌بندی کوتاه و پیشنهاد برای بهتر شدن گروه.\n\n"
            f"پیام‌ها:\n{chat_text}"
        )

        # مدل جدید و پیشنهادی خود Groq
        target_model = "openai/gpt-oss-20b"
        res = client_ai.chat.completions.create(
            messages=[{"role": "system", "content": "تو یک تحلیل‌گر هوشمند و دقیق هستی."}, {"role": "user", "content": prompt}],
            model=target_model,
        )
        
        analysis = res.choices[0].message.content
        await msg.reply_text(f"📊 **تحلیل گروه (بر اساس {len(rows)} پیام):**\n\n{analysis}", parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await msg.reply_text(f"❌ خطا در تحلیل گروه:\n`{str(e)}`", parse_mode="Markdown")

# ==================== ۴. معرفی و راهنما ====================
async def introduce_jarvis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    intro_text = (
        "🤖 **سلام! من جارویس هستم.**\n\n"
        "من دستیار هوشمند و برنامه‌نویسی‌شده توسط **امیرارسلان** هستم. 🧑‍💻\n"
        "وظیفه من مدیریت گروه، سرگرمی و کمک به شماست.\n\n"
        "🌐 **سایت اصلی من:**\n[amirds.ir](https://amirds.ir)\n"
        "🎵 **سایت موزیک من:**\n[openmusic.amirds.ir](https://openmusic.amirds.ir/)\n\n"
        "برای صدا زدن من، فقط کلمه **جارویس** رو بنویس. 😉"
    )
    try:
        await msg.reply_animation(animation=GIF_INTRO, caption=intro_text, parse_mode="Markdown")
    except:
        await msg.reply_text(intro_text, parse_mode="Markdown", disable_web_page_preview=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    help_text = (
        "✨ **راهنمای جامع جارویس** ✨\n\n"
        "👑 **دستورات مدیریتی (ویژه ادمین‌ها):**\n"
        "• `سیک` (ریپلای) ← سکوت کاربر\n"
        "• `رفع سیک` (ریپلای) ← آزاد کردن کاربر\n"
        "• `اخطار` (ریپلای) ← ثبت اخطار (۳ اخطار = سکوت)\n"
        "• `ضد لینک روشن / خاموش` ← مدیریت لینک‌ها\n\n"
        "🎮 **دستورات عمومی گروه:**\n"
        "• `معرفی` ← آشنایی با من\n"
        "• `آمار` ← تعداد کل پیام‌های گروه\n"
        "• `راهنما` ← همین پیام\n"
        "• `جارویس تحلیل` ← بررسی هوشمند ۱۰۰ پیام اخیر گروه\n"
        "• `جارویس تحلیل 50` ← تحلیل ۵۰ پیام (یا هر عددی که می‌خوای)\n\n"
        "🧠 **چت و تحلیل هوش مصنوعی:**\n"
        "• کلمه `جارویس` رو بنویس یا روی پیام من ریپلای کن.\n"
        "• اگر روی یه پیام ریپلای کنی و بنویسی `جارویس این چی میگه`، من اون پیام رو تحلیل می‌کنم!\n\n"
        "💼 **چت خصوصی (منشی هوشمند):**\n"
        "اگر توی چت خصوصی پیام بدی، می‌تونی پیامت رو به مدیر برسونی یا با من چت کنی."
    )
    try:
        await msg.reply_animation(animation=GIF_HELP, caption=help_text, parse_mode="Markdown")
    except:
        await msg.reply_text(help_text, parse_mode="Markdown")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT SUM(count) FROM message_stats WHERE chat_id=?", (msg.chat_id,))
    total_msgs = c.fetchone()[0] or 0
    conn.close()
    await msg.reply_text(f"📊 **آمار گروه:**\n\nتعداد کل پیام‌ها: {total_msgs}", parse_mode="Markdown")

# ==================== ۵. ضد لینک و هوش مصنوعی ====================
async def check_anti_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    if get_setting(msg.chat_id, "anti_link") == 1:
        if re.search(r'(https?://|t\.me/|rubika\.ir/)', msg.text):
            if not await is_admin(msg.chat_id, msg.from_user.id, context):
                try:
                    await msg.delete()
                    await msg.reply_text(f"⚠️ {msg.from_user.first_name}، ارسال لینک مجاز نیست!", parse_mode="Markdown")
                except: pass

user_cooldowns = {}

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    is_reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id
    if not is_reply_to_bot and "جارویس" not in msg.text.lower(): return
    user_id = msg.from_user.id
    current_time = asyncio.get_event_loop().time()
    if user_id in user_cooldowns and current_time - user_cooldowns[user_id] < 5:
        remaining = int(5 - (current_time - user_cooldowns[user_id]))
        await msg.reply_text(f"⏳ لطفاً {remaining} ثانیه صبر کنید.")
        return
    user_cooldowns[user_id] = current_time
    try:
        await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
        
        user_text = msg.text.replace("جارویس", "").strip().lower()
        if not user_text and not is_reply_to_bot and not msg.reply_to_message:
            await msg.reply_text("بله؟ چه سوالی داری؟")
            return
            
        creator_keywords = ["سازنده", "کی ساختت", "کی ساخته", "صاحبت", "مالک", "توسعه دهنده", "توسعه‌دهنده", "برنامه نویس", "برنامه‌نویس", "کی درستت کرده"]
        if any(keyword in user_text for keyword in creator_keywords):
            await msg.reply_text(
                "🧑‍💻 من توسط **امیرارسلان** ساخته شدم!\nایشون یک برنامه‌نویس و توسعه‌دهنده وب هستن.\n\n"
                "🌐 سایتشون:\namirds.ir\n\n🎵 سایت موزیک:\nopenmusic.amirds.ir", parse_mode="Markdown")
            return

        # مدل جدید و پیشنهادی خود Groq
        target_model = "openai/gpt-oss-20b"
        system_prompt = (
            "تو یک دستیار هوشمند، بامزه و خوش‌برخورد به نام جارویس هستی. "
            "قانون مهم: تحت هیچ شرایطی خودت را به سازنده‌ات نسبت نده. "
            "جواب‌هایت باید کوتاه، مفید و دوستانه باشد."
        )
        
        # 🚨 قابلیت تحلیل پیام ریپلای شده
        messages_payload = []
        if msg.reply_to_message and (not is_reply_to_bot or "چی میگه" in user_text or "منظورش" in user_text):
            replied_text = msg.reply_to_message.text or msg.reply_to_message.caption
            if replied_text:
                analysis_prompt = f"کاربر روی این پیام ریپلای کرده و پرسیده: '{user_text}'.\n\nمتن پیام اصلی:\n\"{replied_text}\"\n\nلطفاً تحلیل کن که این پیام چه معنایی داره و به سوال کاربر جواب بده."
                messages_payload = [{"role": "system", "content": system_prompt}, {"role": "user", "content": analysis_prompt}]
            else:
                messages_payload = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]
        else:
            messages_payload = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]

        chat_completion = client_ai.chat.completions.create(
            messages=messages_payload,
            model=target_model,
        )
        await msg.reply_text(chat_completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"خطای هوش مصنوعی در گروه: {e}")
        error_text = str(e)
        await msg.reply_text(f"❌ خطای هوش مصنوعی:\n\n`{error_text}`", parse_mode="Markdown")

# ==================== ۶. چت خصوصی (منشی) ====================
async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    welcome_text = f"سلام {user.first_name}! 👋\nمن جارویس هستم، دستیار هوشمند و منشی امیرارسلان.\n\n👇 یکی از گزینه‌های زیر رو انتخاب کن:"
    keyboard = [
        [InlineKeyboardButton("🤖 چت با هوش مصنوعی", callback_data="priv_ai")],
        [InlineKeyboardButton("📩 ارسال پیام به امیر", callback_data="priv_msg_admin")],
        [InlineKeyboardButton("📝 بیوگرافی من", callback_data="priv_bio")],
        [InlineKeyboardButton("📢 ثبت آگهی", callback_data="priv_add_ad")],
        [InlineKeyboardButton("📋 مشاهده آگهی‌ها", callback_data="priv_view_ads")],
        [InlineKeyboardButton("✍️ ساخت پرامپت", callback_data="priv_prompt")],
        [InlineKeyboardButton("🌐 سایت من", url="https://amirds.ir"), InlineKeyboardButton("🎵 سایت موزیک", url="https://openmusic.amirds.ir/")],
        [InlineKeyboardButton("📜 راهنما", callback_data="priv_help")],
    ]
    try:
        await update.message.reply_animation(animation=GIF_SECRETARY, caption=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def private_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    try: await query.answer()
    except: pass
    data = query.data
    user_id = query.from_user.id
    try: await query.message.delete()
    except: pass
    if data == "priv_ai":
        await context.bot.send_message(chat_id=user_id, text="🧠 حالت چت با هوش مصنوعی فعال شد.\n\nسوالت رو بپرس. (برای خروج /start)")
        context.user_data.clear(); context.user_data['ai_mode'] = True
    elif data == "priv_msg_admin":
        await context.bot.send_message(chat_id=user_id, text="📩 هر پیامی که الان بفرستی، مستقیماً برای امیرارسلان فرستاده میشه.\n(برای انصراف /start رو بزن)")
        context.user_data.clear(); context.user_data['waiting_to_msg_admin'] = True
    elif data == "priv_bio":
        bio = get_bio(user_id)
        if bio: await context.bot.send_message(chat_id=user_id, text=f"📝 بیوگرافی شما:\n{bio}\n\nمتن جدید رو بفرست.")
        else: await context.bot.send_message(chat_id=user_id, text="📝 متن بیوگرافی رو بفرست:")
        context.user_data.clear(); context.user_data['waiting_for_bio'] = True
    elif data == "priv_add_ad":
        keyboard = [[InlineKeyboardButton(cat, callback_data=f"adcat_{cat}")] for cat in get_all_categories()] + [[InlineKeyboardButton("🔙 بازگشت", callback_data="priv_back")]]
        await context.bot.send_message(chat_id=user_id, text="📢 مرحله ۱ از ۳:\nدسته‌بندی آگهی رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data.clear(); context.user_data['ad_step'] = 'category'
    elif data.startswith("adcat_"):
        category = data.replace("adcat_", "")
        context.user_data['ad_category'] = category; context.user_data['ad_step'] = 'title'
        await context.bot.send_message(chat_id=user_id, text=f"✅ {category}\n\n📢 مرحله ۲ از ۳:\nعنوان آگهی رو بنویس:")
    elif data == "priv_view_ads":
        keyboard = [[InlineKeyboardButton(cat, callback_data=f"viewcat_{cat}")] for cat in get_all_categories()] + [[InlineKeyboardButton("🔙 بازگشت", callback_data="priv_back")]]
        await context.bot.send_message(chat_id=user_id, text="📋 دسته‌بندی رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("viewcat_"):
        category = data.replace("viewcat_", ""); ads = get_ads_by_category(category)
        if not ads: await context.bot.send_message(chat_id=user_id, text=f"📭 آگهی‌ای در {category} نیست.")
        else:
            text = f"📋 آگهی‌های {category}:\n\n" + "".join([f"🔹 {t}\n👤 {u}\n📅 {d}\n📝 {desc}\n➖➖➖\n" for _, u, t, desc, d in ads])
            await context.bot.send_message(chat_id=user_id, text=text)
    elif data == "priv_prompt":
        await context.bot.send_message(chat_id=user_id, text="✍️ توضیح بده چه پرامپتی می‌خوای؟")
        context.user_data.clear(); context.user_data['waiting_for_prompt'] = True
    elif data == "priv_back":
        welcome_text = "👇 یکی از گزینه‌های زیر رو انتخاب کن:"
        keyboard = [
            [InlineKeyboardButton("🤖 چت با هوش مصنوعی", callback_data="priv_ai")],
            [InlineKeyboardButton("📩 ارسال پیام به امیر", callback_data="priv_msg_admin")],
            [InlineKeyboardButton("📝 بیوگرافی من", callback_data="priv_bio")],
            [InlineKeyboardButton("📢 ثبت آگهی", callback_data="priv_add_ad")],
            [InlineKeyboardButton("📋 مشاهده آگهی‌ها", callback_data="priv_view_ads")],
            [InlineKeyboardButton("✍️ ساخت پرامپت", callback_data="priv_prompt")],
            [InlineKeyboardButton("🌐 سایت من", url="https://amirds.ir"), InlineKeyboardButton("🎵 سایت موزیک", url="https://openmusic.amirds.ir/")],
            [InlineKeyboardButton("📜 راهنما", callback_data="priv_help")],
        ]
        await context.bot.send_message(chat_id=user_id, text=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "priv_help":
        await context.bot.send_message(chat_id=user_id, text="📜 راهنما:\n• چت با هوش مصنوعی\n• ارسال پیام به امیر\n• ثبت آگهی\n• ساخت پرامپت\nبرای بازگشت /start")

async def private_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message; user_id = msg.from_user.id
    if context.user_data.get('ad_step') == 'title':
        context.user_data['ad_title'] = msg.text; context.user_data['ad_step'] = 'description'
        await msg.reply_text("📢 مرحله ۳ از ۳:\nتوضیحات آگهی رو بنویس:")
    elif context.user_data.get('ad_step') == 'description':
        add_ad(user_id, context.user_data.get('ad_category'), context.user_data.get('ad_title'), msg.text)
        context.user_data.clear(); await msg.reply_text("✅ آگهی ثبت شد!")
    elif context.user_data.get('waiting_for_bio'):
        set_bio_db(user_id, msg.text); context.user_data.clear(); await msg.reply_text("✅ بیوگرافی ذخیره شد!")
    elif context.user_data.get('waiting_to_msg_admin'):
        try:
            admin_text = f"📩 **پیام جدید از طرف ربات:**\n\n👤 نام: {msg.from_user.first_name}\n🆔 آیدی: `{user_id}`\n🔗 یوزرنیم: @{msg.from_user.username if msg.from_user.username else 'ندارد'}\n\n💬 متن پیام:\n{msg.text}"
            await context.bot.send_message(chat_id=YOUR_TELEGRAM_ID, text=admin_text, parse_mode="Markdown")
            await msg.reply_text("✅ پیام شما با موفقیت برای امیرارسلان ارسال شد. به زودی پاسخ میده!")
        except Exception as e:
            logger.error(f"Error sending to admin: {e}")
            await msg.reply_text("❌ متاسفانه در ارسال پیام مشکلی پیش آمد.")
        context.user_data.clear()
    elif context.user_data.get('waiting_for_prompt'):
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            res = client_ai.chat.completions.create(
                messages=[{"role": "system", "content": "تو یک مهندس پرامپت حرفه‌ای هستی. درخواست کاربر را به یک پرامپت دقیق و ساختاریافته تبدیل کن."}, {"role": "user", "content": msg.text}], 
                model="openai/gpt-oss-20b"
            )
            await msg.reply_text(f"✨ **پرامپت شما:**\n\n{res.choices[0].message.content}")
        except Exception as e:
            logger.error(f"prompt error: {e}")
            await msg.reply_text("❌ خطا در ساخت پرامپت.")
        context.user_data.clear()
    elif context.user_data.get('ai_mode'):
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            res = client_ai.chat.completions.create(
                messages=[{"role": "system", "content": "تو یک دستیار هوشمند به نام جارویس هستی."}, {"role": "user", "content": msg.text}], 
                model="openai/gpt-oss-20b"
            )
            await msg.reply_text(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"ai private error: {e}")
            await msg.reply_text(f"❌ خطای هوش مصنوعی:\n\n`{str(e)}`", parse_mode="Markdown")
    else:
        await msg.reply_text("سلام! برای استفاده از منوی ربات دستور /start رو بزن. اگرم می‌خوای پیامت به امیر برسه، دکمه «ارسال پیام به امیر» رو بزن.")

# ==================== ۷. مدیریت پیام‌های گروه ====================
async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    text = msg.text.strip().lower() if msg.text else ""
    
    if msg.from_user and msg.chat.type in ("group", "supergroup"):
        update_stats(msg.chat_id, msg.from_user.id)
        
        if msg.text and not msg.text.startswith("/") and msg.from_user:
            try:
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("INSERT INTO group_messages (chat_id, user_name, text) VALUES (?, ?, ?)", 
                          (msg.chat_id, msg.from_user.first_name, msg.text))
                
                c.execute("""DELETE FROM group_messages WHERE chat_id=? AND id NOT IN 
                             (SELECT id FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT 100)""", (msg.chat_id, msg.chat_id))
                conn.commit(); conn.close()
            except Exception as e:
                logger.error(f"DB save error: {e}")

    if msg.from_user and is_muted(msg.chat_id, msg.from_user.id):
        try: await msg.delete()
        except: pass
        return
    await check_anti_link(update, context)
    
    if text in ("سیک", "رفع سیک", "رفع‌سیک"): await handle_sik(update, context)
    elif text == "اخطار": await warn_user(update, context)
    elif text == "معرفی": await introduce_jarvis(update, context)
    elif text == "آمار": await stats(update, context)
    elif text == "راهنما": await help_command(update, context)
    elif text.startswith("جارویس تحلیل"): await analyze_group(update, context)
    elif text.startswith("ضد لینک روشن"):
        if await is_admin(msg.chat_id, msg.from_user.id, context):
            set_setting(msg.chat_id, "anti_link", 1)
            await msg.reply_text("✅ روشن شد.")
    elif text.startswith("ضد لینک خاموش"):
        if await is_admin(msg.chat_id, msg.from_user.id, context):
            set_setting(msg.chat_id, "anti_link", 0)
            await msg.reply_text("✅ خاموش شد.")
    else:
        await ai_chat(update, context)

# ==================== main ====================
def main():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .build()
    )

    app.add_handler(CommandHandler("start", start_private, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("testai", test_ai_command))
    app.add_handler(CallbackQueryHandler(private_callbacks, pattern="^(priv_|adcat_|viewcat_)"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_messages))
    app.add_handler(CommandHandler("start", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(ChatMemberHandler(greet_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, main_message_handler))

    print("🤖 جارویس روشن شد...")
    
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
