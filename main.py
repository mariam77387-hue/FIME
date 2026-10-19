import os
import sys
import asyncio
import logging
from datetime import datetime

import discord
from discord.ext import commands

# =========================
# الإعدادات
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")          # حط التوكن في Secrets باسم DISCORD_TOKEN
GUILD_ID_ENV = os.getenv("GUILD_ID")        # اختياري: تقدر تحط الآيدي في البيئة بعد
DEFAULT_LIMIT = None                        # None = كل الرسائل. حط رقم لو تبي تسرع
RETRY_ON_429 = 5                            # كم مرة يعيد المحاولة عند rate limit
BASE_DELAY = 2.5                            # ثواني الانتظار الأساسية

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scanner")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, self_bot=False)


# =========================
# أدوات مساعدة
# =========================
def format_author(message: discord.Message) -> str:
    """اسم الكاتب + الآيدي + اللقب إن وجد."""
    author = message.author
    name = getattr(author, "name", "unknown")
    disc = getattr(author, "discriminator", "0")
    if disc and disc != "0":
        name = f"{name}#{disc}"
    display = getattr(author, "display_name", None)
    if display and display != name:
        return f"{name} ({display}) [{author.id}]"
    return f"{name} [{author.id}]"


def format_message(message: discord.Message) -> str:
    """صياغة رسالة واحدة بشكل مقروء."""
    ts = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{ts}] {format_author(message)}: {message.content or '[بدون نص]'}"]

    if message.attachments:
        for att in message.attachments:
            lines.append(f"    📎 مرفق: {att.filename} — {att.url}")

    if message.embeds:
        for i, emb in enumerate(message.embeds, 1):
            title = emb.title or "(بدون عنوان)"
            desc = (emb.description or "")[:200]
            lines.append(f"    🧩 Embed {i}: {title} — {desc}")

    if message.reactions:
        rs = ", ".join(f"{r.emoji}×{r.count}" for r in message.reactions)
        lines.append(f"    ✨ تفاعلات: {rs}")

    if message.reference:
        lines.append(f"    ↩️ رد على رسالة ID: {message.reference.message_id}")

    return "\n".join(lines)


async def safe_history(channel: discord.TextChannel, limit=None):
    """
    جلب تاريخ الروم مع إعادة محاولة عند 429.
    يرجع list من الرسائل مرتبة من الأقدم للأحدث.
    """
    messages = []
    attempt = 0

    while True:
        try:
            async for msg in channel.history(limit=limit, oldest_first=True):
                messages.append(msg)
            return messages

        except discord.Forbidden:
            log.warning(f"🔒 لا توجد صلاحية لقراءة #{channel.name} ({channel.id}) — تم التخطي.")
            return []

        except discord.HTTPException as e:
            if e.status == 429:
                attempt += 1
                if attempt > RETRY_ON_429:
                    log.error(f"⛔ تجاوزت حد إعادة المحاولة في #{channel.name}. تم التخطي.")
                    return messages
                wait = BASE_DELAY * attempt
                log.warning(f"⏳ Rate limit في #{channel.name}. انتظار {wait:.1f}s (محاولة {attempt}).")
                await asyncio.sleep(wait)
                continue
            log.error(f"⚠️ HTTP {e.status} في #{channel.name}: {e.text or e}")
            return messages

        except Exception as e:
            log.error(f"⚠️ خطأ غير متوقع في #{channel.name}: {type(e).__name__}: {e}")
            return messages


async def scan_guild(guild: discord.Guild, limit=None):
    """يفحص كل الرومات النصية ويطبع رسائل اللي يقدر يوصلها."""
    print(f"\n{'='*70}")
    print(f"🏰 السيرفر: {guild.name}  |  ID: {guild.id}")
    print(f"👥 الأعضاء: {guild.member_count or 'غير معروف'}")
    print(f"📚 عدد الرومات النصية: {len(guild.text_channels)}")
    print(f"{'='*70}\n")

    readable = []
    blocked = []

    for ch in guild.text_channels:
        # نحاول نجيب رسالة واحدة بس عشان نعرف هل عندنا صلاحية
        try:
            await ch.history(limit=1).flatten()
            readable.append(ch)
        except discord.Forbidden:
            blocked.append(ch)
        except Exception:
            blocked.append(ch)

    print(f"✅ رومات قابلة للقراءة: {len(readable)}")
    print(f"🚫 رومات محجوبة: {len(blocked)}")
    if blocked:
        print("   (البوت ما يقدر يشوف هذي الرومات — قيد من Discord، مو من الكود)")

    print("\n--- الرومات المحجوبة ---")
    for ch in blocked:
        print(f"  🚫 #{ch.name}  |  ID: {ch.id}")

    print("\n\n📥 جاري سحب الرسائل من الرومات القابلة للقراءة...\n")

    all_messages = []

    for idx, ch in enumerate(readable, 1):
        print(f"\n{'─'*70}")
        print(f"📂 ({idx}/{len(readable)}) #{ch.name}  |  ID: {ch.id}")
        print(f"{'─'*70}")

        msgs = await safe_history(ch, limit=limit)
        if not msgs:
            print("  (لا توجد رسائل أو لا توجد صلاحية)")
            continue

        for m in msgs:
            print(format_message(m))
            all_messages.append(m)

        log.info(f"تم سحب {len(msgs)} رسالة من #{ch.name}")
        await asyncio.sleep(0.6)  # تأخير بسيط بين الرومات

    # ترتيب كل الرسائل زمنياً
    if all_messages:
        all_messages.sort(key=lambda m: m.created_at)
        print(f"\n\n{'='*70}")
        print(f"📊 الإجمالي: {len(all_messages)} رسالة من {len(readable)} روم")
        print(f"⏱️ من {all_messages[0].created_at:%Y-%m-%d %H:%M} إلى {all_messages[-1].created_at:%Y-%m-%d %H:%M}")
        print(f"{'='*70}")

        # حفظ نسخة نصية احتياطية
        try:
            with open("messages_dump.txt", "w", encoding="utf-8") as f:
                for m in all_messages:
                    f.write(format_message(m) + "\n\n")
            log.info("تم حفظ نسخة في messages_dump.txt")
        except Exception as e:
            log.warning(f"تعذر حفظ الملف: {e}")
    else:
        print("\n⚠️ ما فيه رسائل تم سحبها.")


@bot.event
async def on_ready():
    print(f"\n✅ تم تسجيل الدخول كـ {bot.user}  |  ID: {bot.user.id}")

    guild_id_raw = GUILD_ID_ENV
    if not guild_id_raw:
        try:
            guild_id_raw = input("\n🔢 أدخل آيدي السيرفر: ").strip()
        except Exception:
            guild_id_raw = ""

    try:
        guild_id = int(guild_id_raw)
    except (ValueError, TypeError):
        log.error("❌ آيدي السيرفر غير صالح. لازم يكون أرقام فقط.")
        await bot.close()
        return

    try:
        guild = await bot.fetch_guild(guild_id)
    except discord.NotFound:
        log.error("❌ السيرفر غير موجود، أو البوت مو عضو فيه.")
        await bot.close()
        return
    except discord.Forbidden:
        log.error("❌ البوت ما عنده صلاحية الوصول لهذا السيرفر.")
        await bot.close()
        return
    except Exception as e:
        log.error(f"❌ خطأ أثناء جلب السيرفر: {type(e).__name__}: {e}")
        await bot.close()
        return

    try:
        await scan_guild(guild, limit=DEFAULT_LIMIT)
    except Exception as e:
        log.error(f"❌ خطأ أثناء الفحص: {type(e).__name__}: {e}")
    finally:
        print("\n🏁 انتهى الفحص. إغلاق الاتصال...")
        await bot.close()


def main():
    if not TOKEN:
        log.error("❌ التوكن غير موجود. حط DISCORD_TOKEN في Secrets أو متغيرات البيئة.")
        sys.exit(1)

    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.error("❌ التوكن غلط أو منتهي.")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("تم الإيقاف يدوياً.")
    except Exception as e:
        log.error(f"❌ خطأ أثناء التشغيل: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()