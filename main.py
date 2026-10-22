import os
import asyncio
import logging
import secrets
from typing import Dict, Optional, List

from flask import Flask, request, redirect
import threading

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import discordoauth2

# ================== الإعدادات ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI")
WEB_PORT = int(os.environ.get("WEB_PORT", 8080))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ================== OAuth2 Client ==================
# المكتبة treeben77/discord-oauth2.py
oauth_client = discordoauth2.Client(
    client_id=DISCORD_CLIENT_ID,
    client_secret=DISCORD_CLIENT_SECRET,
    redirect_uri=DISCORD_REDIRECT_URI,
)

# تخزين مؤقت لربط رمز التفويض بمعرف محادثة تيليجرام
# المفتاح: state (عشوائي)، القيمة: chat_id
pending_auth: Dict[str, int] = {}

# ================== خادم Flask لاستقبال OAuth2 ==================
flask_app = Flask(__name__)

@flask_app.route("/callback")
def callback():
    """يستقبل رمز التفويض من Discord بعد موافقة المستخدم."""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return "تم إلغاء التفويض. يمكنك العودة إلى تيليجرام.", 400
    if not code or not state:
        return "طلب غير صالح.", 400

    chat_id = pending_auth.pop(state, None)
    if not chat_id:
        return "انتهت صلاحية الجلسة. يرجى البدء من جديد من تيليجرام.", 400

    # حفظ الكود مؤقتًا لكي يستخدمه البوت (سنستخدم قاموسًا عامًا)
    auth_codes[chat_id] = code
    return "تم التفويض بنجاح! ✅ يمكنك العودة إلى تيليجرام وإرسال أي رسالة للمتابعة.", 200

# قاموس عام لتخزين الأكواد مؤقتًا
auth_codes: Dict[int, str] = {}

def run_flask():
    flask_app.run(host="0.0.0.0", port=WEB_PORT)

# ================== دوال مساعدة ==================
async def fetch_guilds_async(access_token: str) -> List[dict]:
    """جلب السيرفرات باستخدام access_token عبر Discord API الرسمي."""
    # نستخدم مكتبة discordoauth2 لجلب السيرفرات
    # ملاحظة: المكتبة قد تكون متزامنة، لذلك نغلفها في executor
    loop = asyncio.get_running_loop()
    def _fetch():
        # access = oauth_client.fetch_access_token(code)  # لا نحتاج هذا هنا، لأن access_token جاهز
        # لكن discordoauth2 يوفر كائن AccessToken
        # الأسهل: نستخدم requests مباشرة إذا كانت المكتبة معقدة
        import requests
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get("https://discord.com/api/v10/users/@me/guilds", headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    return await loop.run_in_executor(None, _fetch)

async def exchange_code_for_token(code: str) -> Optional[str]:
    """تبديل رمز التفويض بـ access_token."""
    loop = asyncio.get_running_loop()
    def _exchange():
        access = oauth_client.exchange_code(code)
        return access.token
    try:
        return await loop.run_in_executor(None, _exchange)
    except Exception as e:
        logger.error(f"فشل تبديل الكود: {e}")
        return None

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
        "👋 أهلاً!\n"
        "لجلب قائمة سيرفراتك على ديسكورد، أرسل لي أي رسالة (أو /auth) وسأعطيك رابط تفويض آمن.\n"
        "بعد الضغط على الرابط والموافقة، سأعرض لك كل سيرفراتك مباشرة من Discord API الرسمي."
    )

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = secrets.token_urlsafe(16)
    pending_auth[state] = chat_id

    # توليد رابط التفويض
    auth_url = oauth_client.generate_uri(
        scope=["identify", "guilds"],
        state=state,
    )
    keyboard = [[InlineKeyboardButton("🔗 تفويض الآن", url=auth_url)]]
    await update.message.reply_text(
        "اضغط على الزر أدناه لتفويض التطبيق بقراءة قائمة سيرفراتك.\n"
        "هذا آمن ولن يرى التطبيق رسائلك أو كلمة المرور.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    code = auth_codes.pop(chat_id, None)

    if not code:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على تفويض. الرجاء إرسال /auth أولاً للحصول على رابط التفويض."
        )
        return

    await update.message.reply_text("⏳ جاري جلب سيرفراتك من Discord...")

    access_token = await exchange_code_for_token(code)
    if not access_token:
        await update.message.reply_text("❌ فشل التفويض. يرجى المحاولة مرة أخرى عبر /auth.")
        return

    try:
        guilds = await fetch_guilds_async(access_token)
    except Exception as e:
        logger.error(f"فشل جلب السيرفرات: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء جلب السيرفرات. حاول لاحقًا.")
        return

    if not guilds:
        await update.message.reply_text("لم يتم العثور على سيرفرات.")
        return

    text_result = f"📊 إجمالي السيرفرات: **{len(guilds)}**\n\n" + build_servers_text(guilds)

    # تقسيم الرسائل الطويلة
    if len(text_result) > 4000:
        chunks = [text_result[i:i+4000] for i in range(0, len(text_result), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await update.message.reply_text(text_result, parse_mode="Markdown")

# ================== التشغيل الرئيسي ==================
def main():
    if not all([TELEGRAM_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI]):
        logger.error("تأكد من تعيين جميع المتغيرات البيئية المطلوبة.")
        return

    # تشغيل Flask في خيط منفصل
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"خادم Flask يعمل على المنفذ {WEB_PORT}")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("auth", auth_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("بوت تيليجرام يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()