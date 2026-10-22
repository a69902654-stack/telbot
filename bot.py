import logging
import sqlite3
import random
from datetime import datetime
from telegram import Update, ChatMemberUpdated, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ⚠️ توکن رو اینجا بذار ====================
TOKEN = "8643617533:AAGkK9XklChU4loDyJvPp575LtQFvCX6Tsc"
# ============================================================

DB_PATH = "bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS muted_users (
            chat_id INTEGER,
            user_id INTEGER,
            muted_by INTEGER,
            muted_at TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_gif TEXT
        )
    """)
    conn.commit()
    conn.close()

def is_muted(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM muted_users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = c.fetchone()
    conn.close()
    return r is not None

def mute_user(chat_id, user_id, muted_by):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO muted_users (chat_id, user_id, muted_by, muted_at) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, muted_by, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def unmute_user(chat_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM muted_users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()

def get_chat_gif(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT welcome_gif FROM chat_settings WHERE chat_id=?", (chat_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

def set_chat_gif(chat_id, gif_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO chat_settings (chat_id, welcome_gif) VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET welcome_gif=excluded.welcome_gif
    """, (chat_id, gif_id))
    conn.commit()
    conn.close()

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
    gif_id = get_chat_gif(chat_id)
    DEFAULT_GIF = "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif"
    try:
        await context.bot.send_animation(
            chat_id=chat_id,
            animation=gif_id if gif_id else DEFAULT_GIF,
            caption=welcome,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطای گیف: {e}")
        try:
            await context.bot.send_message(chat_id=chat_id, text=welcome, parse_mode="Markdown")
        except Exception as e2:
            logger.error(f"خطای متن: {e2}")

async def set_welcome_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.animation:
        await msg.reply_text("❌ روی یه گیف ریپلای کن و بنویس /setgif")
        return
    member = await context.bot.get_chat_member(msg.chat_id, msg.from_user.id)
    if member.status not in ("administrator", "creator"):
        await msg.reply_text("⛔ فقط ادمین‌ها")
        return
    set_chat_gif(msg.chat_id, msg.reply_to_message.animation.file_id)
    await msg.reply_text("✅ گیف خوش‌آمد تنظیم شد!")

async def handle_sik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    text = msg.text.strip().lower()
    if text not in ("سیک", "رفع سیک", "رفع‌سیک"): return
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("❌ باید روی پیام کاربر ریپلای کنی")
        return
    admin_member = await context.bot.get_chat_member(msg.chat_id, msg.from_user.id)
    if admin_member.status not in ("administrator", "creator"): return
    target = msg.reply_to_message.from_user
    if target.is_bot:
        await msg.reply_text("🤖 بات رو نمی‌شه سکوت کرد")
        return
    target_member = await context.bot.get_chat_member(msg.chat_id, target.id)
    if target_member.status in ("administrator", "creator"):
        await msg.reply_text("⛔ ادمین رو نمی‌شه سکوت کرد")
        return
    mention = f"[{target.first_name}](tg://user?id={target.id})"
    if text == "سیک":
        try:
            await context.bot.restrict_chat_member(
                chat_id=msg.chat_id,
                user_id=target.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            mute_user(msg.chat_id, target.id, msg.from_user.id)
            await msg.reply_text(
                f"🔇 {mention} سکوت شد!\n👮 توسط {msg.from_user.first_name}\nبرای آزادسازی: روی پیامش ریپلای کن و بنویس «رفع سیک»",
                parse_mode="Markdown"
            )
        except Exception as e:
            await msg.reply_text(f"❌ خطا: {e}\n(بات باید ادمین با دسترسی Ban users باشه)")
    else:
        try:
            await context.bot.restrict_chat_member(
                chat_id=msg.chat_id,
                user_id=target.id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
            unmute_user(msg.chat_id, target.id)
            await msg.reply_text(f"🔊 {mention} آزاد شد!", parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ خطا: {e}")

async def delete_muted_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user: return
    if is_muted(msg.chat_id, msg.from_user.id):
        try:
            await msg.delete()
        except Exception as e:
            logger.error(f"خطای حذف: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **راهنما**\n\n"
        "• خوش‌آمد خودکار با گیف برای اعضای جدید\n"
        "• `سیک` → روی پیام کاربر ریپلای کن تا ساکت بشه\n"
        "• `رفع سیک` → روی پیام کاربر سکوت‌شده ریپلای کن تا آزاد بشه\n"
        "• `/setgif` → روی یه گیف ریپلای کن تا گیف خوش‌آمد عوض بشه\n",
        parse_mode="Markdown"
    )

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setgif", set_welcome_gif))
    app.add_handler(ChatMemberHandler(greet_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        delete_muted_messages
    ), group=0)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
        handle_sik
    ), group=1)

    print("🤖 بات روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()