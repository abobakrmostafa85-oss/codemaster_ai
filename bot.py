import logging
import sqlite3
import os
from datetime import datetime
import re
import requests
import json
import random
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. Configuration ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", None)
DB_PATH = "codemaster.db"
LOG_FILE = "bot_logs.log"

# --- 2. Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    filename=LOG_FILE
)
logger = logging.getLogger(__name__)

# --- 3. Database Class ---
class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    joined_at TEXT,
                    streak INTEGER DEFAULT 0,
                    last_active TEXT,
                    completed_lessons INTEGER DEFAULT 0,
                    completed_challenges INTEGER DEFAULT 0,
                    fixed_errors INTEGER DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    achievement_name TEXT,
                    unlocked_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            conn.commit()

    def get_user(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return cursor.fetchone()

    def add_user(self, user_id, username, full_name):
        if not self.get_user(user_id):
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (user_id, username, full_name, joined_at, last_active) VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, full_name, datetime.now().isoformat(), datetime.now().isoformat())
                )
                conn.commit()

    def update_xp(self, user_id, xp_gain):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (xp_gain, user_id))
            cursor.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
            current_xp = cursor.fetchone()[0]
            new_level = (current_xp // 100) + 1
            cursor.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, user_id))
            conn.commit()
            return new_level

    def log_error(self, user_id, error_type, error_message):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO error_logs (user_id, error_type, error_message, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, error_type, error_message, datetime.now().isoformat())
            )
            cursor.execute("UPDATE users SET fixed_errors = fixed_errors + 1 WHERE user_id = ?", (user_id,))
            conn.commit()

    def get_leaderboard(self, limit=10):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username, full_name, xp, level FROM users ORDER BY xp DESC LIMIT ?", (limit,))
            return cursor.fetchall()

# --- 4. Logic Classes ---
class UserManager:
    def __init__(self, db: Database):
        self.db = db

    def register_user(self, user):
        self.db.add_user(user.id, user.username, user.full_name)

    def get_profile(self, user_id):
        user_data = self.db.get_user(user_id)
        if not user_data: return None
        return {
            "id": user_data[0], "username": user_data[1], "full_name": user_data[2],
            "xp": user_data[3], "level": user_data[4], "joined_at": user_data[5],
            "streak": user_data[6], "completed_lessons": user_data[8],
            "completed_challenges": user_data[9], "fixed_errors": user_data[10]
        }

class ErrorAnalyzer:
    def __init__(self):
        self.patterns = {
            "SyntaxError": {"reason": "خطأ في قواعد الكود.", "tips": "تأكد من النقطتين والأقواس."},
            "NameError": {"reason": "متغير غير معرف.", "tips": "تأكد من تعريف المتغير قبل استخدامه."},
            "TypeError": {"reason": "أنواع بيانات غير متوافقة.", "tips": "استخدم str() أو int() للتحويل."}
        }

    def analyze(self, text):
        for err, data in self.patterns.items():
            if err in text: return f"🔍 النوع: {err}\n💡 السبب: {data['reason']}\n📌 نصيحة: {data['tips']}"
        return "لم أتمكن من تحديد الخطأ بدقة، جرب إرسال الكود بالكامل."

class GeminiAI:
    def __init__(self, key):
        self.key = key
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={key}"

    def ask(self, prompt):
        if not self.key or len(self.key) < 10: return None
        try:
            res = requests.post(self.url, json={"contents": [{"parts": [{"text": prompt}]}]})
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        except: return None

# --- 5. Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Database()
    UserManager(db).register_user(update.effective_user)
    keyboard = [['/learn', '/profile'], ['/help', '/leaderboard']]
    await update.message.reply_text("مرحباً بك في CodeMaster AI! 🤖", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Database()
    p = UserManager(db).get_profile(update.effective_user.id)
    if not p: return
    msg = f"👤 الملف: {p['full_name']}\n🏆 المستوى: {p['level']}\n✨ XP: {p['xp']}\n🛠 الأخطاء المصححة: {p['fixed_errors']}"
    await update.message.reply_text(msg)

async def error_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err_text = " ".join(context.args)
    if not err_text:
        await update.message.reply_text("أرسل الخطأ بعد الأمر.")
        return
    
    ai = GeminiAI(GEMINI_API_KEY)
    res = ai.ask(f"اشرح هذا الخطأ في بايثون بالعربي: {err_text}")
    if res:
        await update.message.reply_text(f"🔍 تحليل الذكاء الاصطناعي:\n\n{res}")
    else:
        await update.message.reply_text(ErrorAnalyzer().analyze(err_text))

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = Database().get_leaderboard()
    text = "📊 لوحة الصدارة:\n\n"
    for i, u in enumerate(users, 1):
        text += f"{i}. {u[1]} - Level {u[3]} ({u[2]} XP)\n"
    await update.message.reply_text(text)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start, /profile, /learn, /leaderboard, /error [text]")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    if any(x in code for x in ["=", "print", "def", "if"]):
        await update.message.reply_text("تم استلام الكود، جاري التحليل...")
        Database().update_xp(update.effective_user.id, 5)
    else:
        await update.message.reply_text("أرسل أمراً أو كوداً للتحليل.")

# --- 6. Main ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("error", error_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    print("Bot is running...")
    app.run_polling()

