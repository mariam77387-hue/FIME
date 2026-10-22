import os
import asyncio
import logging
import secrets
import threading
from typing import Dict, Optional, List

from flask import Flask, request
import discordoauth2
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ================== الإعدادات ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI")

# ✅ قراءة المنفذ من متغير البيئة PORT (الذي توفره الاستضافة)
# إذا لم يوجد، يتم استخدام 26683 كقيمة افتراضية
WEB_PORT = int(os.environ.get("PORT", os.environ.get("WEB_PORT", 26683)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ================== OAuth2 Client ==================
oauth_client = discordoauth2.Client(
    client_id=DISCORD_CLIENT_ID,
    client_secret=DISCORD_CLIENT_SECRET,
    redirect_uri=DISCORD_REDIRECT_URI,
)

pending_auth: Dict[str, int] = {}
auth_codes: Dict[int, str] = {}

# ================== خادم Flask ==================
flask_app = Flask(__name__)

@flask_app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        logger.warning(f"OAuth2 error: {error}")
        return "تم إلغاء التفويض. يمكنك العودة إلى تيليجرام والمحاولة مرة أخرى.", 400
    if not code or not state:
        return "طلب غير صالح: رمز التفويض أو الحالة مفقود.", 400

    chat_id = pending_auth.pop(state, None)
    if not chat_id:
        return "انتهت صلاحية الجلسة. يرجى البدء من جديد من تيليجرام باستخدام /auth.", 400

    auth_codes[chat_id] = code
    logger.info(f"تم استلام كود التفويض للمحادثة {chat_id}")
    return "✅ تم التفويض بنجاح! يمكنك العودة إلى تيليجرام وإرسال أي رسالة ليتم عرض سيرفراتك.", 200

@flask_app.route("/health")
def health():
    """نقطة نهاية بسيطة للتحقق من أن الخادم يعمل (مفيدة لبعض الاستضافات)."""
    return "OK", 200

def run_flask():
    """تشغيل خادم Flask على المنفذ المحدد."""
    logger.info(f"خادم Flask يستمع على المنفذ {WEB_PORT}")
    flask_app.run(host="0.0.0.0", port=WEB_PORT, use_reloader=False)

# ================== دوال Discord API ==================
async def exchange_code_for_token(code: str) -> Optional[str]:
    """تبديل كود التفويض بـ access_token."""
    loop = asyncio.get_running_loop()
    def _exchange():
        access = oauth_client.exchange_code(code)
        return access.token
    try:
        return await loop.run_in_executor(None, _exchange)
    except Exception as e:
        logger.error(f"فشل تبديل الكود: {e}", exc_info=True)
        return None

async def fetch_guilds(access_token: str) -> List[dict]:
    """جلب قائمة السيرفرات مباشرة من Discord API الرسمي."""
    loop = asyncio.get_running_loop()
    def _fetch():
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    return await loop.run_in_executor(None, _fetch)

def build_servers_text(guilds: List[dict]) -> str:
    if not guilds:
        return "❌ لم يتم العثور على أي سيرفرات."
    lines = []
    for idx, g in enumerate(guilds, 1):
        name = g.get("name", "سيرفر غير معروف")
        gid = g.get("id", "")
        lines.append(f"{idx}. **{name}** (ID: `{gid}`)")
    return "\n\n".join(lines)

# ================== معالجات تيليجرام ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "هذا البوت يعرض لك قائمة سيرفرات ديسكورد التي أنت عضو فيها.\n"
        "للبقاء آمناً، لا يطلب منك البوت أي كلمة مرور، بل يستخدم نظام التفويض الرسمي من ديسكورد (OAuth2).\n\n"
        "🔹 أرسل الأمر /auth للحصول على رابط التفويض.\n"
        "🔹 بعد الضغط على الرابط والموافقة، ارجع هنا وأرسل أي رسالة."
    )

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = secrets.token_urlsafe(16)
    pending_auth[state] = chat_id

    auth_url = oauth_client.generate_uri(scope=["identify", "guilds"], state=state)
    keyboard = [[InlineKeyboardButton("🔗 تفويض الآن", url=auth_url)]]
    await update.message.reply_text(
        "اضغط على الزر أدناه لتفويض التطبيق بقراءة قائمة سيرفراتك.\n"
        "هذا آمن تماماً؛ التطبيق لا يرى رسائلك أو كلمة المرور الخاصة بك.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    code = auth_codes.pop(chat_id, None)

    if not code:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على تفويض نشط.\n"
            "الرجاء إرسال الأمر /auth أولاً للحصول على رابط التفويض."
        )
        return

    await update.message.reply_text("⏳ جاري جلب سيرفراتك من Discord...")

    access_token = await exchange_code_for_token(code)
    if not access_token:
        await update.message.reply_text("❌ فشل التفويض. يرجى المحاولة مرة أخرى باستخدام /auth.")
        return

    try:
        guilds = await fetch_guilds(access_token)
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            await update.message.reply_text("❌ انتهت صلاحية التفويض. يرجى استخدام /auth مرة أخرى.")
        else:
            logger.error(f"HTTP error fetching guilds: {e}")
            await update.message.reply_text("❌ حدث خطأ أثناء جلب السيرفرات. حاول لاحقاً.")
        return
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ غير متوقع. حاول لاحقاً.")
        return

    if not guilds:
        await update.message.reply_text("لم يتم العثور على أي سيرفرات.")
        return

    text_result = f"📊 إجمالي السيرفرات: **{len(guilds)}**\n\n" + build_servers_text(guilds)

    if len(text_result) > 4000:
        chunks = [text_result[i:i+4000] for i in range(0, len(text_result), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await update.message.reply_text(text_result, parse_mode="Markdown")

# ================== التشغيل الرئيسي ==================
def main():
    if not all([TELEGRAM_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI]):
        logger.error("تأكد من تعيين جميع المتغيرات البيئية المطلوبة: "
                     "TELEGRAM_BOT_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI")
        return

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("auth", auth_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("بوت تيليجرام يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()