import logging
import sqlite3
import os
from datetime import datetime
import re
import requests
import json
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. Configuration (from config.py) ---
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", None)
DB_PATH = "codemaster.db"
LOG_FILE = "bot_logs.log"
DEBUG = True

# --- 2. Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    filename=LOG_FILE
)
logger = logging.getLogger(__name__)

# --- 3. Database Class (from bot/db/database.py) ---
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
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    user_id INTEGER,
                    topic TEXT,
                    level TEXT,
                    status TEXT, -- 'started', 'completed'
                    score INTEGER,
                    PRIMARY KEY (user_id, topic, level)
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

    def add_achievement(self, user_id, achievement_name):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM achievements WHERE user_id = ? AND achievement_name = ?", (user_id, achievement_name))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO achievements (user_id, achievement_name, unlocked_at) VALUES (?, ?, ?)",
                    (user_id, achievement_name, datetime.now().isoformat())
                )
                conn.commit()
                return True
        return False

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

# --- 4. User Manager Class (from bot/core/user_manager.py) ---
class UserManager:
    def __init__(self, db: Database):
        self.db = db

    def register_user(self, user):
        self.db.add_user(user.id, user.username, user.full_name)

    def get_profile(self, user_id):
        user_data = self.db.get_user(user_id)
        if not user_data:
            return None
        
        profile = {
            "id": user_data[0],
            "username": user_data[1],
            "full_name": user_data[2],
            "xp": user_data[3],
            "level": user_data[4],
            "joined_at": user_data[5],
            "streak": user_data[6],
            "completed_lessons": user_data[8],
            "completed_challenges": user_data[9],
            "fixed_errors": user_data[10]
        }
        return profile

    def award_xp(self, user_id, amount, reason=""):
        new_level = self.db.update_xp(user_id, amount)
        self.check_achievements(user_id)
        return new_level

    def check_achievements(self, user_id):
        profile = self.get_profile(user_id)
        unlocked = []
        
        if profile["completed_lessons"] >= 1:
            if self.db.add_achievement(user_id, "أول تمرين"): unlocked.append("أول تمرين")
        
        if profile["completed_lessons"] >= 10:
            if self.db.add_achievement(user_id, "أول 10 تمارين"): unlocked.append("أول 10 تمارين")
            
        if profile["fixed_errors"] >= 5:
            if self.db.add_achievement(user_id, "مكتشف الأخطاء"): unlocked.append("مكتشف الأخطاء")
            
        return unlocked

# --- 5. Lesson Data (from bot/data/lessons.py) ---
LESSONS = {
    "Variables": {
        "Beginner": {
            "title": "المتغيرات (Variables)",
            "content": "المتغير هو مكان في الذاكرة لتخزين البيانات. في Python، لا نحتاج لتحديد نوع المتغير مسبقاً.",
            "example": "x = 5\nname = 'CodeMaster'",
            "exercise": "قم بإنشاء متغير باسم age وقيمته 25",
            "solution": "age = 25"
        },
        "Intermediate": {
            "title": "المتغيرات المتقدمة",
            "content": "يمكننا تعيين قيم متعددة لمتغيرات متعددة في سطر واحد.",
            "example": "x, y, z = 1, 2, 3",
            "exercise": "قم بتعيين القيمة 10 لـ a و 20 لـ b في سطر واحد",
            "solution": "a, b = 10, 20"
        }
    },
    "Loops": {
        "Beginner": {
            "title": "الحلقات التكرارية (Loops)",
            "content": "تستخدم for لتكرار كود معين لعدد محدد من المرات أو عبر عناصر قائمة.",
            "example": "for i in range(5):\n    print(i)",
            "exercise": "اكتب حلقة تطبع الأرقام من 0 إلى 2",
            "solution": "for i in range(3):\n    print(i)"
        }
    }
}

CHALLENGES = [
    {
        "id": 1,
        "title": "تحدي الجمع",
        "description": "اكتب برنامجاً يجمع رقمين x و y ويطبع الناتج.",
        "difficulty": "Easy",
        "xp": 20
    },
    {
        "id": 2,
        "title": "تحدي القوائم",
        "description": "أنشئ قائمة تحتوي على 3 ألوان واطبع اللون الثاني.",
        "difficulty": "Medium",
        "xp": 40
    }
]

DAILY_TASKS = [
    "قم بكتابة دالة تحسب مساحة المربع.",
    "اشرح الفرق بين List و Tuple في سطر واحد.",
    "استخدم دالة input لاستقبال اسم المستخدم."
]

# --- 6. Lesson Manager Class (from bot/core/lesson_manager.py) ---
class LessonManager:
    def __init__(self):
        self.lessons = LESSONS
        self.challenges = CHALLENGES
        self.daily_tasks = DAILY_TASKS

    def get_topics(self):
        return list(self.lessons.keys())

    def get_lesson(self, topic, level):
        if topic in self.lessons and level in self.lessons[topic]:
            return self.lessons[topic][level]
        return None

    def get_random_challenge(self):
        return random.choice(self.challenges)

    def get_daily_task(self):
        return random.choice(self.daily_tasks)

    def check_exercise_solution(self, topic, level, user_solution):
        lesson = self.get_lesson(topic, level)
        if not lesson:
            return False
        
        clean_user = user_solution.strip().replace(" ", "")
        clean_solution = lesson["solution"].strip().replace(" ", "")
        
        return clean_user == clean_solution

# --- 7. Error Analyzer Class (from bot/core/error_analyzer.py) ---
class ErrorAnalyzer:
    def __init__(self):
        self.error_patterns = {
            "SyntaxError": {
                "reason": "هناك خطأ في قواعد كتابة الكود، مثل نسيان نقطتين (:) أو قوس.",
                "example_bad": "if True\n    print(\'Hello\')",
                "example_good": "if True:\n    print(\'Hello\')",
                "tips": "تأكد دائماً من إغلاق الأقواس ووضع النقطتين بعد if, for, while, def."
            },
            "NameError": {
                "reason": "تحاول استخدام متغير أو دالة غير معرفة.",
                "example_bad": "print(x) # x is not defined",
                "example_good": "x = 10\nprint(x)",
                "tips": "تأكد من كتابة اسم المتغير بشكل صحيح ومن تعريفه قبل استخدامه."
            },
            "IndexError": {
                "reason": "تحاول الوصول إلى عنصر في قائمة باستخدام فهرس (index) غير موجود.",
                "example_bad": "my_list = [1, 2]\nprint(my_list[5])",
                "example_good": "my_list = [1, 2]\nprint(my_list[1])",
                "tips": "تذكر أن الفهرس يبدأ من 0 وينتهي عند (طول القائمة - 1)."
            },
            "KeyError": {
                "reason": "تحاول الوصول إلى مفتاح (key) غير موجود في القاموس (dictionary).",
                "example_bad": "my_dict = {\'a\': 1}\nprint(my_dict[\'b\'])",
                "example_good": "print(my_dict.get(\'b\', \'Not Found\'))",
                "tips": "استخدم دالة .get() لتجنب هذا الخطأ عند عدم التأكد من وجود المفتاح."
            },
            "TypeError": {
                "reason": "تحاول إجراء عملية على أنواع بيانات غير متوافقة.",
                "example_bad": "print(\'Age: \' + 25)",
                "example_good": "print(\'Age: \' + str(25))",
                "tips": "تأكد من تحويل أنواع البيانات باستخدام str(), int(), float() عند الحاجة."
            }
        }

    def analyze_traceback(self, traceback_text):
        for error_type in self.error_patterns:
            if error_type in traceback_text:
                analysis = self.error_patterns[error_type]
                return {
                    "type": error_type,
                    "reason": analysis["reason"],
                    "bad": analysis["example_bad"],
                    "good": analysis["example_good"],
                    "tips": analysis["tips"]
                }
        
        return {
            "type": "خطأ غير معروف",
            "reason": "لم أتمكن من تحديد السبب بدقة من خلال النظام المحلي.",
            "bad": "N/A",
            "good": "N/A",
            "tips": "حاول إرسال الكود بالكامل أو استخدم Gemini AI لتحليل أعمق."
        }

    def analyze_code(self, code):
        issues = []
        score = 100
        
        if "print " in code:
            issues.append("في Python 3، يجب استخدام الأقواس مع print(). مثال: print(\'hello\')")
            score -= 20
        
        if re.search(r"if.*[^:]\\n", code):
            issues.append("نسيت وضع النقطتين (:) بعد جملة if.")
            score -= 15

        if len(issues) == 0:
            return "الكود يبدو نظيفاً! تقييم: 100/100", 100
        
        report = "تقرير تحليل الكود:\n" + "\n".join([f"- {i}" for i in issues])
        return report, max(0, score)

# --- 8. Gemini AI Layer (from bot/core/ai_layer.py) ---
class GeminiAI:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={self.api_key}"

    def is_available(self):
        return self.api_key is not None and len(self.api_key) > 10

    def generate_response(self, prompt):
        if not self.is_available():
            return None
        
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        try:
            response = requests.post(self.api_url, headers=headers, data=json.dumps(data))
            if response.status_code == 200:
                result = response.json()
                return result["candidates"][0]["content"]["parts"][0]["text"]
            return f"Error: {response.status_code}"
        except Exception as e:
            return f"Exception: {str(e)}"

    def analyze_error_deeply(self, error_text):
        prompt = f"حلل خطأ البرمجة التالي في لغة بايثون واشرحه باللغة العربية بأسلوب تعليمي مع مثال صحيح:\n{error_text}"
        return self.generate_response(prompt)

    def generate_new_challenge(self, topic):
        prompt = f"أنشئ تحدي برمجي جديد في بايثون حول موضوع {topic}. التحدي يجب أن يكون باللغة العربية ويتضمن الوصف والمخرجات المتوقعة."
        return self.generate_response(prompt)

# --- 9. Command Handlers (from bot/handlers/command_handlers.py) ---
class Handlers:
    def __init__(self, user_manager: UserManager, lesson_manager: LessonManager, error_analyzer: ErrorAnalyzer, ai: GeminiAI):
        self.user_manager = user_manager
        self.lesson_manager = lesson_manager
        self.error_analyzer = error_analyzer
        self.ai = ai

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.user_manager.register_user(user)
        
        welcome_text = (
            f"مرحباً بك {user.first_name} في CodeMaster AI! 🤖\n\n"
            "أنا مدربك الشخصي لتعلم البرمجة بلغة Python ومحلل الأخطاء الذكي.\n\n"
            "استخدم القائمة أدناه أو الأوامر لبدء رحلتك:"
        )
        
        keyboard = [
            ["/learn 📚", "/profile 👤"],
            ["/challenge 🏆", "/daily 📅"],
            ["/help ❓", "/leaderboard 📊"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "الأوامر المتاحة:\n"
            "/start - بدء البوت\n"
            "/learn - اختيار موضوع للتعلم\n"
            "/profile - عرض ملفك الشخصي وإحصائياتك\n"
            "/challenge - الحصول على تحدي برمجي\n"
            "/daily - المهمة اليومية\n"
            "/leaderboard - لوحة الصدارة\n"
            "/error - تحليل خطأ برمجي (أرسل الخطأ بعد الأمر)\n\n"
            "يمكنك أيضاً إرسال كود بايثون مباشرة وسأقوم بتحليله لك!"
        )
        await update.message.reply_text(help_text)

    async def profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        profile = self.user_manager.get_profile(user_id)
        
        if not profile:
            await update.message.reply_text("لم يتم العثور على ملفك الشخصي. أرسل /start أولاً.")
            return

        stats_text = (
            f"👤 ملف المستخدم: {profile[\'full_name



























































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































    .get_connection() as conn:
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

    def add_achievement(self, user_id, achievement_name):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM achievements WHERE user_id = ? AND achievement_name = ?", (user_id, achievement_name))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO achievements (user_id, achievement_name, unlocked_at) VALUES (?, ?, ?)",
                    (user_id, achievement_name, datetime.now().isoformat())
                )
                conn.commit()
                return True
        return False

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

# --- 4. User Manager Class (from bot/core/user_manager.py) ---
class UserManager:
    def __init__(self, db: Database):
        self.db = db

    def register_user(self, user):
        self.db.add_user(user.id, user.username, user.full_name)

    def get_profile(self, user_id):
        user_data = self.db.get_user(user_id)
        if not user_data:
            return None
        
        profile = {
            "id": user_data[0],
            "username": user_data[1],
            "full_name": user_data[2],
            "xp": user_data[3],
            "level": user_data[4],
            "joined_at": user_data[5],
            "streak": user_data[6],
            "completed_lessons": user_data[8],
            "completed_challenges": user_data[9],
            "fixed_errors": user_data[10]
        }
        return profile

    def award_xp(self, user_id, amount, reason=""):
        new_level = self.db.update_xp(user_id, amount)
        self.check_achievements(user_id)
        return new_level

    def check_achievements(self, user_id):
        profile = self.get_profile(user_id)
        unlocked = []
        
        if profile["completed_lessons"] >= 1:
            if self.db.add_achievement(user_id, "أول تمرين"): unlocked.append("أول تمرين")
        
        if profile["completed_lessons"] >= 10:
            if self.db.add_achievement(user_id, "أول 10 تمارين"): unlocked.append("أول 10 تمارين")
            
        if profile["fixed_errors"] >= 5:
            if self.db.add_achievement(user_id, "مكتشف الأخطاء"): unlocked.append("مكتشف الأخطاء")
            
        return unlocked

# --- 5. Lesson Data (from bot/data/lessons.py) ---
LESSONS = {
    "Variables": {
        "Beginner": {
            "title": "المتغيرات (Variables)",
            "content": "المتغير هو مكان في الذاكرة لتخزين البيانات. في Python، لا نحتاج لتحديد نوع المتغير مسبقاً.",
            "example": "x = 5\nname = 'CodeMaster'",
            "exercise": "قم بإنشاء متغير باسم age وقيمته 25",
            "solution": "age = 25"
        },
        "Intermediate": {
            "title": "المتغيرات المتقدمة",
            "content": "يمكننا تعيين قيم متعددة لمتغيرات متعددة في سطر واحد.",
            "example": "x, y, z = 1, 2, 3",
            "exercise": "قم بتعيين القيمة 10 لـ a و 20 لـ b في سطر واحد",
            "solution": "a, b = 10, 20"
        }
    },
    "Loops": {
        "Beginner": {
            "title": "الحلقات التكرارية (Loops)",
            "content": "تستخدم for لتكرار كود معين لعدد محدد من المرات أو عبر عناصر قائمة.",
            "example": "for i in range(5):\n    print(i)",
            "exercise": "اكتب حلقة تطبع الأرقام من 0 إلى 2",
            "solution": "for i in range(3):\n    print(i)"
        }
    }
}

CHALLENGES = [
    {
        "id": 1,
        "title": "تحدي الجمع",
        "description": "اكتب برنامجاً يجمع رقمين x و y ويطبع الناتج.",
        "difficulty": "Easy",
        "xp": 20
    },
    {
        "id": 2,
        "title": "تحدي القوائم",
        "description": "أنشئ قائمة تحتوي على 3 ألوان واطبع اللون الثاني.",
        "difficulty": "Medium",
        "xp": 40
    }
]

DAILY_TASKS = [
    "قم بكتابة دالة تحسب مساحة المربع.",
    "اشرح الفرق بين List و Tuple في سطر واحد.",
    "استخدم دالة input لاستقبال اسم المستخدم."
]

# --- 6. Lesson Manager Class (from bot/core/lesson_manager.py) ---
class LessonManager:
    def __init__(self):
        self.lessons = LESSONS
        self.challenges = CHALLENGES
        self.daily_tasks = DAILY_TASKS

    def get_topics(self):
        return list(self.lessons.keys())

    def get_lesson(self, topic, level):
        if topic in self.lessons and level in self.lessons[topic]:
            return self.lessons[topic][level]
        return None

    def get_random_challenge(self):
        return random.choice(self.challenges)

    def get_daily_task(self):
        return random.choice(self.daily_tasks)

    def check_exercise_solution(self, topic, level, user_solution):
        lesson = self.get_lesson(topic, level)
        if not lesson:
            return False
        
        clean_user = user_solution.strip().replace(" ", "")
        clean_solution = lesson["solution"].strip().replace(" ", "")
        
        return clean_user == clean_solution

# --- 7. Error Analyzer Class (from bot/core/error_analyzer.py) ---
class ErrorAnalyzer:
    def __init__(self):
        self.error_patterns = {
            "SyntaxError": {
                "reason": "هناك خطأ في قواعد كتابة الكود، مثل نسيان نقطتين (:) أو قوس.",
                "example_bad": "if True\n    print(\'Hello\')",
                "example_good": "if True:\n    print(\'Hello\')",
                "tips": "تأكد دائماً من إغلاق الأقواس ووضع النقطتين بعد if, for, while, def."
            },
            "NameError": {
                "reason": "تحاول استخدام متغير أو دالة غير معرفة.",
                "example_bad": "print(x) # x is not defined",
                "example_good": "x = 10\nprint(x)",
                "tips": "تأكد من كتابة اسم المتغير بشكل صحيح ومن تعريفه قبل استخدامه."
            },
            "IndexError": {
                "reason": "تحاول الوصول إلى عنصر في قائمة باستخدام فهرس (index) غير موجود.",
                "example_bad": "my_list = [1, 2]\nprint(my_list[5])",
                "example_good": "my_list = [1, 2]\nprint(my_list[1])",
                "tips": "تذكر أن الفهرس يبدأ من 0 وينتهي عند (طول القائمة - 1)."
            },
            "KeyError": {
                "reason": "تحاول الوصول إلى مفتاح (key) غير موجود في القاموس (dictionary).",
                "example_bad": "my_dict = {\'a\': 1}\nprint(my_dict[\'b\'])",
                "example_good": "print(my_dict.get(\'b\', \'Not Found\'))",
                "tips": "استخدم دالة .get() لتجنب هذا الخطأ عند عدم التأكد من وجود المفتاح."
            },
            "TypeError": {
                "reason": "تحاول إجراء عملية على أنواع بيانات غير متوافقة.",
                "example_bad": "print(\'Age: \' + 25)",
                "example_good": "print(\'Age: \' + str(25))",
                "tips": "تأكد من تحويل أنواع البيانات باستخدام str(), int(), float() عند الحاجة."
            }
        }

    def analyze_traceback(self, traceback_text):
        for error_type in self.error_patterns:
            if error_type in traceback_text:
                analysis = self.error_patterns[error_type]
                return {
                    "type": error_type,
                    "reason": analysis["reason"],
                    "bad": analysis["example_bad"],
                    "good": analysis["example_good"],
                    "tips": analysis["tips"]
                }
        
        return {
            "type": "خطأ غير معروف",
            "reason": "لم أتمكن من تحديد السبب بدقة من خلال النظام المحلي.",
            "bad": "N/A",
            "good": "N/A",
            "tips": "حاول إرسال الكود بالكامل أو استخدم Gemini AI لتحليل أعمق."
        }

    def analyze_code(self, code):
        issues = []
        score = 100
        
        if "print " in code:
            issues.append("في Python 3، يجب استخدام الأقواس مع print(). مثال: print(\'hello\')")
            score -= 20
        
        if re.search(r"if.*[^:]\\n", code):
            issues.append("نسيت وضع النقطتين (:) بعد جملة if.")
            score -= 15

        if len(issues) == 0:
            return "الكود يبدو نظيفاً! تقييم: 100/100", 100
        
        report = "تقرير تحليل الكود:\n" + "\n".join([f"- {i}" for i in issues])
        return report, max(0, score)

# --- 8. Gemini AI Layer (from bot/core/ai_layer.py) ---
class GeminiAI:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={self.api_key}"

    def is_available(self):
        return self.api_key is not None and len(self.api_key) > 10

    def generate_response(self, prompt):
        if not self.is_available():
            return None
        
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        try:
            response = requests.post(self.api_url, headers=headers, data=json.dumps(data))
            if response.status_code == 200:
                result = response.json()
                return result["candidates"][0]["content"]["parts"][0]["text"]
            return f"Error: {response.status_code}"
        except Exception as e:
            return f"Exception: {str(e)}"

    def analyze_error_deeply(self, error_text):
        prompt = f"حلل خطأ البرمجة التالي في لغة بايثون واشرحه باللغة العربية بأسلوب تعليمي مع مثال صحيح:\n{error_text}"
        return self.generate_response(prompt)

    def generate_new_challenge(self, topic):
        prompt = f"أنشئ تحدي برمجي جديد في بايثون حول موضوع {topic}. التحدي يجب أن يكون باللغة العربية ويتضمن الوصف والمخرجات المتوقعة."
        return self.generate_response(prompt)

# --- 9. Command Handlers (from bot/handlers/command_handlers.py) ---
class Handlers:
    def __init__(self, user_manager: UserManager, lesson_manager: LessonManager, error_analyzer: ErrorAnalyzer, ai: GeminiAI):
        self.user_manager = user_manager
        self.lesson_manager = lesson_manager
        self.error_analyzer = error_analyzer
        self.ai = ai

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.user_manager.register_user(user)
        
        welcome_text = (
            f"مرحباً بك {user.first_name} في CodeMaster AI! 🤖\n\n"
            "أنا مدربك الشخصي لتعلم البرمجة بلغة Python ومحلل الأخطاء الذكي.\n\n"
            "استخدم القائمة أدناه أو الأوامر لبدء رحلتك:"
        )
        
        keyboard = [
            ["/learn 📚", "/profile 👤"],
            ["/challenge 🏆", "/daily 📅"],
            ["/help ❓", "/leaderboard 📊"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "الأوامر المتاحة:\n"
            "/start - بدء البوت\n"
            "/learn - اختيار موضوع للتعلم\n"
            "/profile - عرض ملفك الشخصي وإحصائياتك\n"
            "/challenge - الحصول على تحدي برمجي\n"
            "/daily - المهمة اليومية\n"
            "/leaderboard - لوحة الصدارة\n"
            "/error - تحليل خطأ برمجي (أرسل الخطأ بعد الأمر)\n\n"
            "يمكنك أيضاً إرسال كود بايثون مباشرة وسأقوم بتحليله لك!"
        )
        await update.message.reply_text(help_text)

    async def profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        profile = self.user_manager.get_profile(user_id)
        
        if not profile:
            await update.message.reply_text("لم يتم العثور على ملفك الشخصي. أرسل /start أولاً.")
            return

        stats_text = (
            f"👤 ملف المستخدم: {profile[\'full_name\']}\n"
            f"🏆 المستوى: {profile[\'level\']}\n"
            f"✨ XP: {profile[\'xp\']}\n"
            f"📚 الدروس المكتملة: {profile[\'completed_lessons\']}\n"
            f"🎯 التحديات المنجزة: {profile[\'completed_challenges\']}\n"
            f"🛠 الأخطاء المصححة: {profile[\'fixed_errors\']}\n"
            f"🔥 الأيام المتتالية: {profile[\'streak\']}"
        )
        await update.message.reply_text(stats_text)

    async def learn(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        topics = self.lesson_manager.get_topics()
        topics_text = "اختر موضوعاً للتعلم:\n" + "\n".join([f"- {t}" for t in topics])
        await update.message.reply_text(topics_text + "\n\nمثال: استخدم /lesson Variables Beginner")

    async def lesson(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 2:
            await update.message.reply_text("يرجى استخدام الصيغة: /lesson [الموضوع] [المستوى]\nمثال: /lesson Variables Beginner")
            return
        
        topic, level = context.args[0], context.args[1]
        lesson = self.lesson_manager.get_lesson(topic, level)
        
        if lesson:
            response = (
                f"📖 {lesson[\'title\']}\n\n"
                f"{lesson[\'content\']}\n\n"
                f"💻 مثال:\n`{lesson[\'example\']}`\n\n"
                f"📝 تمرين:\n{lesson[\'exercise\']}\n\n"
                "أرسل حلك للكود وسأقوم بتقييمه!"
            )
            await update.message.reply_text(response, parse_mode=\'Markdown\')
        else:
            await update.message.reply_text("عذراً، هذا الدرس غير متوفر حالياً.")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        error_text = " ".join(context.args)
        if not error_text:
            await update.message.reply_text("يرجى إرسال نص الخطأ بعد أمر /error")
            return

        if self.ai.is_available():
            analysis = self.ai.analyze_error_deeply(error_text)
            await update.message.reply_text(f"🔍 تحليل الذكاء الاصطناعي:\n\n{analysis}")
        else:
            analysis = self.error_analyzer.analyze_traceback(error_text)
            response = (
                f"🔍 نوع الخطأ: {analysis[\'type\']}\n"
                f"💡 السبب: {analysis[\'reason\']}\n\n"
                f"❌ مثال خاطئ:\n`{analysis[\'bad\']}`\n\n"
                f"✅ مثال صحيح:\n`{analysis[\'good\']}`\n\n"
                f"📌 نصيحة: {analysis[\'tips\']}"
            )
            await update.message.reply_text(response, parse_mode=\'Markdown\')
        
        self.user_manager.db.log_error(update.effective_user.id, "Traceback", error_text[:100])

    async def leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        top_users = self.user_manager.db.get_leaderboard()
        leaderboard_text = "📊 لوحة الصدارة (أفضل 10):\n\n"
        for i, user in enumerate(top_users, 1):
            leaderboard_text += f"{i}. {user[1]} - Level {user[3]} ({user[2]} XP)\n"
        await update.message.reply_text(leaderboard_text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        code = update.message.text
        if "(" in code or "=" in code or "print" in code:
            report, score = self.error_analyzer.analyze_code(code)
            await update.message.reply_text(report)
            if score == 100:
                self.user_manager.award_xp(update.effective_user.id, 10)
                await update.message.reply_text("رائع! حصلت على 10 XP.")
        else:
            await update.message.reply_text("أرسل أمراً أو كود بايثون للتحليل. استخدم /help لرؤية الأوامر.")

# --- 10. Main Function ---
def main():
    db = Database()
    user_manager = UserManager(db)
    lesson_manager = LessonManager()
    error_analyzer = ErrorAnalyzer()
    ai = GeminiAI(GEMINI_API_KEY)
    
    handlers = Handlers(user_manager, lesson_manager, error_analyzer, ai)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("profile", handlers.profile))
    application.add_handler(CommandHandler("learn", handlers.learn))
    application.add_handler(CommandHandler("lesson", handlers.lesson))
    application.add_handler(CommandHandler("error", handlers.error_handler))
    application.add_handler(CommandHandler("leaderboard", handlers.leaderboard))
    
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handlers.handle_message))

    print("CodeMaster AI Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.error(f"Critical error: {e}")
        print(f"حدث خطأ فادح: {e}")
