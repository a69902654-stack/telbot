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
YOUR_TELEGRAM_ID = int(os.environ.get("YOUR_TELEGRAM_ID", "0"))

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

def pick_model():
    try:
        models = client_ai.models.list()
        return "openai/gpt-oss-20b" if any(m.id == "openai/gpt-oss-20b" for m in models.data) else "qwen/qwen3.8-27b"
    except Exception as e:
        logger.error(f"model pick failed: {e}")
        return "openai/gpt-oss-20b"

# ==================== ۰. تست پینگ ====================
async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 پونگ! ربات کاملاً سالمه و به دیتابیس و تلگرام دسترسی داره.")

# ==================== ۱. خوش‌آمدگویی گروه ====================
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

# ==================== ۲. سیستم سیک ====================
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

# ==================== ۳. اخطار ====================
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

# ==================== ۴. معرفی ====================
async def introduce_jarvis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    intro_text = (
        "🤖 **سلام! من جارویس هستم.**\n\n"
        "من دستیار هوشمند و برنامه‌نویسی‌شده توسط **امیرارسلان** هستم. 🧑‍💻\n"
        "وظیفه من مدیریت گروه، سرگرمی و کمک به شماست.\n\n"
        "🔹 **کارهایی که می‌تونم انجام بدم:**\n"
        "• خوش‌آمدگویی با گیف به اعضای جدید 🎉\n"
        "• سکوت و رفع سکوت کاربران (سیک / رفع سیک) 🔇\n"
        "• اخطار دادن به کاربران خاطی (اخطار) ⚠️\n"
        "• حذف لینک‌های تبلیغاتی (ضد لینک) 🚫\n"
        "• ثبت و مشاهده آگهی‌ها (در چت خصوصی) 📢\n"
        "• درخواست ساخت ربات 🤖\n"
        "• ساخت پرامپت حرفه‌ای ✍️\n"
        "• چت با هوش مصنوعی 🧠\n\n"
        "🌐 **سایت اصلی من:**\n"
        "[amirds.ir](https://amirds.ir)\n\n"
        "🎵 **سایت موزیک من:**\n"
        "[openmusic.amirds.ir](https://openmusic.amirds.ir/)\n\n"
        "برای صدا زدن من، فقط کلمه **جارویس** رو بنویس. 😉"
    )
    try:
        await msg.reply_animation(animation=GIF_INTRO, caption=intro_text, parse_mode="Markdown")
    except:
        await msg.reply_text(intro_text, parse_mode="Markdown", disable_web_page_preview=True)

# ==================== ۵. آمار ====================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT SUM(count) FROM message_stats WHERE chat_id=?", (msg.chat_id,))
    total_msgs = c.fetchone()[0] or 0
    conn.close()
    await msg.reply_text(f"📊 **آمار گروه:**\n\nتعداد کل پیام‌ها: {total_msgs}", parse_mode="Markdown")

# ==================== ۶. ضد لینک ====================
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

# ==================== ۷. هوش مصنوعی ====================
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
        if not user_text:
            await msg.reply_text("بله؟ چه سوالی داری؟")
            return
        creator_keywords = ["سازنده", "کی ساختت", "کی ساخته", "صاحبت", "مالک", "توسعه دهنده", "توسعه‌دهنده", "برنامه نویس", "برنامه‌نویس", "کی درستت کرده"]
        if any(keyword in user_text for keyword in creator_keywords):
            await msg.reply_text(
                "🧑‍💻 من توسط **امیرارسلان** ساخته شدم!\n"
                "ایشون یک برنامه‌نویس و توسعه‌دهنده وب هستن.\n\n"
                "🌐 می‌تونید به سایتشون سر بزنید:\n"
                "amirds.ir\n\n"
                "🎵 و یا از سایت موزیکشون استفاده کنید:\n"
                "openmusic.amirds.ir",
                parse_mode="Markdown"
            )
            return
        target_model = pick_model()
        system_prompt = (
            "تو یک دستیار هوشمند، بامزه و خوش‌برخورد به نام جارویس هستی. "
            "قانون مهم: تحت هیچ شرایطی در مکالمات عادی، خودت به سازنده‌ات (امیرارسلان) اشاره نکن. "
            "جواب‌هایت باید کوتاه، مفید، دوستانه و همراه با ایموجی باشد."
        )
        chat_completion = client_ai.chat.completions.create(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            model=target_model,
        )
        await msg.reply_text(chat_completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"خطای هوش مصنوعی: {e}")
        await msg.reply_text("❌ متاسفانه الان نمی‌تونم به هوش مصنوعی وصل بشم.")

# ==================== ۸. چت خصوصی ====================
async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    welcome_text = f"سلام {user.first_name}! 👋\nمن جارویس هستم، دستیار هوشمند شما.\n\n👇 یکی از گزینه‌های زیر رو انتخاب کن:"
    keyboard = [
        [InlineKeyboardButton("🤖 چت با هوش مصنوعی", callback_data="priv_ai")],
        [InlineKeyboardButton("📝 بیوگرافی من", callback_data="priv_bio")],
        [InlineKeyboardButton("📢 ثبت آگهی جدید", callback_data="priv_add_ad")],
        [InlineKeyboardButton("📋 مشاهده آگهی‌ها", callback_data="priv_view_ads")],
        [InlineKeyboardButton("✍️ ساخت پرامپت حرفه‌ای", callback_data="priv_prompt")],
        [InlineKeyboardButton("🤖 درخواست ساخت ربات", callback_data="priv_req_bot")],
        [InlineKeyboardButton("🌐 سایت من", url="https://amirds.ir"), InlineKeyboardButton("🎵 سایت موزیک", url="https://openmusic.amirds.ir/")],
        [InlineKeyboardButton("📜 راهنمای دستورات", callback_data="priv_help")],
    ]
    try:
        await update.message.reply_animation(animation=GIF_INTRO, caption=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
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
    elif data == "priv_req_bot":
        await context.bot.send_message(chat_id=user_id, text="🤖 اسم رباتی که می‌خوای رو بنویس:")
        context.user_data.clear(); context.user_data['req_bot_step'] = 'name'
    elif data == "priv_back":
        welcome_text = "👇 یکی از گزینه‌های زیر رو انتخاب کن:"
        keyboard = [
            [InlineKeyboardButton("🤖 چت با هوش مصنوعی", callback_data="priv_ai")],
            [InlineKeyboardButton("📝 بیوگرافی من", callback_data="priv_bio")],
            [InlineKeyboardButton("📢 ثبت آگهی جدید", callback_data="priv_add_ad")],
            [InlineKeyboardButton("📋 مشاهده آگهی‌ها", callback_data="priv_view_ads")],
            [InlineKeyboardButton("✍️ ساخت پرامپت حرفه‌ای", callback_data="priv_prompt")],
            [InlineKeyboardButton("🤖 درخواست ساخت ربات", callback_data="priv_req_bot")],
            [InlineKeyboardButton("🌐 سایت من", url="https://amirds.ir"), InlineKeyboardButton("🎵 سایت موزیک", url="https://openmusic.amirds.ir/")],
            [InlineKeyboardButton("📜 راهنمای دستورات", callback_data="priv_help")],
        ]
        await context.bot.send_message(chat_id=user_id, text=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "priv_help":
        await context.bot.send_message(chat_id=user_id, text="📜 راهنما:\n• چت با هوش مصنوعی\n• بیوگرافی\n• ثبت و مشاهده آگهی\n• ساخت پرامپت\n• درخواست ربات\nبرای بازگشت /start")

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
    elif context.user_data.get('waiting_for_prompt'):
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            target_model = pick_model()
            res = client_ai.chat.completions.create(messages=[{"role": "system", "content": "تو یک مهندس پرامپت حرفه‌ای هستی. درخواست کاربر را به یک پرامپت دقیق و ساختاریافته تبدیل کن."}, {"role": "user", "content": msg.text}], model=target_model)
            await msg.reply_text(f"✨ **پرامپت شما:**\n\n{res.choices[0].message.content}")
        except Exception as e:
            logger.error(f"prompt error: {e}")
            await msg.reply_text("❌ خطا در ساخت پرامپت.")
        context.user_data.clear()
    elif context.user_data.get('req_bot_step') == 'name':
        context.user_data['req_bot_name'] = msg.text; context.user_data['req_bot_step'] = 'desc'
        await msg.reply_text("📝 توضیحات و امکانات ربات رو بنویس:")
    elif context.user_data.get('req_bot_step') == 'desc':
        bot_name = context.user_data.get('req_bot_name'); bot_desc = msg.text
        user_link = f"https://t.me/{msg.from_user.username}" if msg.from_user.username else "ندارد"
        admin_text = f"📥 درخواست ربات:\n👤 {msg.from_user.first_name}\n🔗 {user_link}\n🆔 {user_id}\n\n🤖 {bot_name}\n📝 {bot_desc}"
        try:
            await context.bot.send_message(chat_id=YOUR_TELEGRAM_ID, text=admin_text)
            await msg.reply_text("✅ درخواست برای سازنده ارسال شد!")
        except:
            await msg.reply_text("❌ خطا در ارسال درخواست.")
        context.user_data.clear()
    elif context.user_data.get('ai_mode'):
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            target_model = pick_model()
            res = client_ai.chat.completions.create(messages=[{"role": "system", "content": "تو یک دستیار هوشمند به نام جارویس هستی."}, {"role": "user", "content": msg.text}], model=target_model)
            await msg.reply_text(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"ai private error: {e}")
            await msg.reply_text("❌ خطا در اتصال به هوش مصنوعی.")

# ==================== ۹. مدیریت پیام‌های گروه ====================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 راهنمای جارویس\n\n👑 ادمین‌ها:\n• سیک / رفع سیک (ریپلای)\n• اخطار (ریپلای)\n• ضد لینک روشن / خاموش\n\n🎮 کاربران:\n• معرفی | آمار | راهنما\n\n🧠 چت با هوش مصنوعی:\nکلمه جارویس رو بنویس یا ریپلای کن!")

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    text = msg.text.strip().lower() if msg.text else ""
    if msg.from_user and msg.chat.type in ("group", "supergroup"):
        update_stats(msg.chat_id, msg.from_user.id)
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
    elif text == "بازی": await context.bot.send_message(chat_id=msg.chat_id, text="🎲 به زودی!")
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
