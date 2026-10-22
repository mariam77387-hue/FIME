import os
import asyncio
import logging
from typing import List, Dict, Optional

import discord
from discord.ext import commands
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# مكتبة جلب السيرفرات
import discord_sensor_api as dsapi

# ================== الإعدادات ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DISCORD_USER_TOKEN = os.environ.get("DISCORD_USER_TOKEN")  # اختياري

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================== عميل ديسكورد (سيلف بوت) لإنشاء الروابط ==================
discord_client: Optional[commands.Bot] = None

async def start_discord_selfbot():
    """تشغيل عميل ديسكورد في الخلفية إذا وُجد توكن مستخدم."""
    global discord_client
    if not DISCORD_USER_TOKEN:
        logger.info("لم يتم توفير DISCORD_USER_TOKEN، سيتم تعطيل إنشاء روابط الدعوة.")
        return

    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True

    discord_client = commands.Bot(
        command_prefix="!",
        self_bot=True,
        intents=intents,
        help_command=None,
    )

    @discord_client.event
    async def on_ready():
        logger.info(f"تم تسجيل الدخول كـ selfbot: {discord_client.user}")

    try:
        await discord_client.start(DISCORD_USER_TOKEN)
    except Exception as e:
        logger.error(f"فشل تشغيل selfbot: {e}")
        discord_client = None

# ================== دوال مساعدة ==================

async def create_invite_for_guild(guild_id: int) -> Optional[str]:
    """
    إنشاء رابط دعوة دائم لأول قناة نصية في السيرفر.
    يعيد الرابط أو None إذا فشل.
    """
    if not discord_client or not discord_client.is_ready():
        return None

    guild = discord_client.get_guild(guild_id)
    if not guild:
        # قد لا يكون الحساب عضوًا في السيرفر
        return None

    # البحث عن أول قناة نصية يملك فيها الحساب صلاحية إنشاء دعوة
    for channel in guild.text_channels:
        try:
            # التحقق من الصلاحيات
            perms = channel.permissions_for(guild.me)
            if perms.create_instant_invite:
                invite = await channel.create_invite(
                    max_age=0,  # لا ينتهي
                    max_uses=0,  # عدد استخدامات غير محدود
                    reason="تم إنشاؤه بواسطة بوت تيليجرام",
                )
                return invite.url
        except Exception as e:
            logger.warning(f"فشل إنشاء دعوة في {guild.name} / {channel.name}: {e}")
            continue
    return None


def fetch_user_servers(user_id: str) -> List[Dict]:
    """
    جلب قائمة السيرفرات المشترك بها المستخدم عبر discord-sensor-api.
    تعيد قائمة من القواميس تحتوي على id و name.
    """
    try:
        # الدالة تعيد قائمة (list) من السيرفرات
        servers = dsapi.get_user_servers(user_id)
        if not servers:
            return []
        # قد تكون الاستجابة List[dict] أو تحتوي على مفتاح 'servers'
        if isinstance(servers, dict) and "servers" in servers:
            servers = servers["servers"]
        # تطبيع البيانات
        normalized = []
        for s in servers:
            if isinstance(s, dict):
                normalized.append({
                    "id": str(s.get("id") or s.get("guild_id") or ""),
                    "name": s.get("name") or s.get("guild_name") or "سيرفر غير معروف",
                })
        return normalized
    except Exception as e:
        logger.error(f"خطأ في جلب سيرفرات المستخدم {user_id}: {e}")
        return []


def resolve_user_id(input_str: str) -> Optional[str]:
    """
    إذا كان المدخل يحتوي على أرقام فقط، نعتبره ID.
    وإلا نفترض أنه اسم مستخدم، لكن discord-sensor-api يتطلب ID.
    لذلك سنعيد None إذا لم يكن أرقامًا.
    """
    cleaned = input_str.strip()
    if cleaned.isdigit():
        return cleaned
    # يمكن إضافة محاولة تحويل اسم المستخدم إلى ID لاحقًا إذا توفرت API.
    return None


def build_servers_text(servers: List[Dict], invites: Dict[str, str]) -> str:
    """تنسيق قائمة السيرفرات مع الروابط إن وجدت."""
    if not servers:
        return "❌ لم يتم العثور على أي سيرفرات مشترك بها هذا المستخدم."

    lines = []
    for idx, s in enumerate(servers, start=1):
        name = s["name"]
        sid = s["id"]
        invite = invites.get(sid)
        if invite:
            lines.append(f"{idx}. **{name}**\n   🔗 {invite}")
        else:
            lines.append(f"{idx}. **{name}** (ID: `{sid}`)")

    return "\n\n".join(lines)

# ================== معالجات تيليجرام ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أرسل لي **ID** أو **اسم مستخدم** ديسكورد (يفضل ID).\n"
        "مثال: `123456789012345678`\n\n"
        "سأعرض لك السيرفرات المشترك بها، مع إمكانية اختيار العدد أو عرض الكل."
    )


async def handle_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = resolve_user_id(text)

    if not user_id:
        await update.message.reply_text(
            "⚠️ عذرًا، حاليًا أدعم فقط **ID** الرقمي (وليس اسم المستخدم).\n"
            "يمكنك الحصول على ID المستخدم من خلال تفعيل وضع المطور في ديسكورد."
        )
        return

    # حفظ ID في سياق المحادثة
    context.user_data["target_user_id"] = user_id

    keyboard = [
        [
            InlineKeyboardButton("5 سيرفرات", callback_data="limit_5"),
            InlineKeyboardButton("10 سيرفرات", callback_data="limit_10"),
        ],
        [
            InlineKeyboardButton("20 سيرفر", callback_data="limit_20"),
            InlineKeyboardButton("50 سيرفر", callback_data="limit_50"),
        ],
        [InlineKeyboardButton("🌐 الجميع (كل السيرفرات)", callback_data="limit_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🔍 تم استلام المستخدم: `{user_id}`\nاختر عدد السيرفرات التي تريد عرضها:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = context.user_data.get("target_user_id")
    if not user_id:
        await query.edit_message_text("⚠️ انتهت الجلسة. أرسل ID المستخدم مرة أخرى.")
        return

    data = query.data
    if data.startswith("limit_"):
        limit_str = data.split("_")[1]
        limit = None if limit_str == "all" else int(limit_str)
    else:
        limit = None

    # إعلام المستخدم ببدء الجلب
    await query.edit_message_text("⏳ جاري جلب السيرفرات...")

    # جلب السيرفرات في الخلفية لتجنب حظر التيليجرام
    loop = asyncio.get_running_loop()
    servers = await loop.run_in_executor(None, fetch_user_servers, user_id)

    if not servers:
        await query.edit_message_text(
            "❌ لم يتم العثور على سيرفرات لهذا المستخدم، أو أن الخدمة غير متاحة حاليًا."
        )
        return

    # تطبيق الحد المطلوب
    if limit is not None:
        servers = servers[:limit]

    # محاولة إنشاء روابط الدعوة إن أمكن
    invites: Dict[str, str] = {}
    if discord_client and discord_client.is_ready():
        await query.edit_message_text("🔗 جاري محاولة إنشاء روابط الدعوة...")
        for s in servers:
            try:
                gid = int(s["id"])
            except ValueError:
                continue
            invite_url = await create_invite_for_guild(gid)
            if invite_url:
                invites[s["id"]] = invite_url

    # تنسيق النتيجة
    text_result = build_servers_text(servers, invites)

    # إذا كانت الرسالة طويلة جدًا، نقسمها
    if len(text_result) > 4000:
        parts = [text_result[i:i+4000] for i in range(0, len(text_result), 4000)]
        for part in parts:
            await query.message.reply_text(part, parse_mode="Markdown")
        await query.delete_message()
    else:
        await query.edit_message_text(text_result, parse_mode="Markdown")

# ================== التشغيل الرئيسي ==================

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN غير موجود في المتغيرات البيئية.")
        return

    # بدء selfbot في الخلفية (اختياري)
    if DISCORD_USER_TOKEN:
        asyncio.get_event_loop().create_task(start_discord_selfbot())

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_input))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("البوت يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()