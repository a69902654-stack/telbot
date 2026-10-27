import os
import threading
from flask import Flask
import jarvis_bot # ایمپورت کردن فایل اصلی بات

app = Flask(__name__)

# مسیر اصلی وب‌سایت (فقط برای اینکه Render فکر کنه ما سایت داریم)
@app.route('/')
def home():
    return "Jarvis Bot is alive and running!"

def run_bot():
    # اجرای بات در یک ترد جداگانه
    jarvis_bot.main()

if __name__ == "__main__":
    # شروع بات در پس‌زمینه
    threading.Thread(target=run_bot, daemon=True).start()
    
    # شروع وب‌سرور روی پورتی که Render مشخص می‌کنه
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
