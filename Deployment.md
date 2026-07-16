# تعليمات الرفع على Render 🚀

لرفع البوت بنجاح على منصة Render، اتبع الخطوات التالية:

### 1. إعداد GitHub
- قم بإنشاء مستودع (Repository) جديد على GitHub.
- ارفع الملفات التالية فقط:
  - `bot.py` (الملف الرئيسي)
  - `requirements.txt` (المكتبات)
  - `runtime.txt` (إصدار بايثون)

### 2. إعداد Render
- سجل الدخول إلى [Render.com](https://render.com).
- اضغط على **New +** ثم اختر **Background Worker**.
- اربط حساب GitHub الخاص بك واختر المستودع.

### 3. إعدادات الخدمة (Service Settings)
- **Name**: `codemaster-ai-bot`
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python bot.py`

### 4. المتغيرات البيئية (Environment Variables)
اضغط على زر **Advanced** ثم **Add Environment Variable**:
- `TELEGRAM_TOKEN`: توكن البوت الخاص بك من BotFather.
- `GEMINI_API_KEY`: (اختياري) مفتاح Gemini AI.

### ملاحظات هامة:
- منصة Render في الخطة المجانية قد تمسح قاعدة البيانات `codemaster.db` عند إعادة التشغيل. للحل الدائم، يفضل استخدام قاعدة بيانات خارجية مثل MongoDB أو PostgreSQL، ولكن للبدء والتجربة، SQLite تعمل بشكل جيد.
- تأكد من اختيار **Background Worker** وليس Web Service، لأن بوتات التلجرام تعمل بنظام Polling ولا تحتاج لفتح منفذ HTTP.
