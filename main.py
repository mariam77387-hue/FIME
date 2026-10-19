import os
import asyncio
import discord

# ====== الإعدادات ======
TOKEN = os.getenv("DISCORD_TOKEN")
MESSAGE_LIMIT = 100  # عدد الرسائل لكل روم خاص (تقدر تغيره)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)
scanned = False  # عشان ما يتكرر الفحص لو صار reconnect


def is_private_channel(channel: discord.TextChannel, guild: discord.Guild) -> bool:
    """
    الروم يعتبر خاص إذا صلاحية view_channel للرول @everyone
    متعطلة صراحة (False). إذا ما فيه overwrite، يعتبر عام.
    """
    try:
        overwrites = channel.overwrites_for(guild.default_role)
        return overwrites.view_channel is False
    except Exception:
        return False


async def dump_channel_messages(channel: discord.TextChannel, limit: int = 100):
    """يطبع رسائل الروم الخاص مع الوقت والكاتب والمرفقات."""
    print(f"\n{'='*60}")
    print(f"🔒 الروم الخاص: #{channel.name}  |  ID: {channel.id}")
    print(f"{'='*60}")

    try:
        async for message in channel.history(limit=limit):
            ts = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = message.author.name
            if getattr(message.author, "discriminator", "0") != "0":
                author += f"#{message.author.discriminator}"

            content = message.content or "[بدون نص]"
            print(f"[{ts}] {author}: {content}")

            if message.attachments:
                for att in message.attachments:
                    print(f"    📎 مرفق: {att.url}")

            if message.reactions:
                reactions = ", ".join(f"{r.emoji}×{r.count}" for r in message.reactions)
                print(f"    ✨ تفاعلات: {reactions}")

    except discord.Forbidden:
        print("  ⚠️ ما عندك صلاحية قراءة رسائل هذا الروم.")
    except discord.HTTPException as e:
        print(f"  ⚠️ خطأ HTTP أثناء جلب الرسائل: {e}")
    except Exception as e:
        print(f"  ⚠️ خطأ غير متوقع: {e}")


async def scan_guild(guild_id: int):
    """الدالة الرئيسية: تجيب السيرفر، تصنف الرومات، وتطبع رسائل الخاصة."""
    print(f"\n⏳ جاري جلب السيرفر {guild_id} ...")

    try:
        guild = await client.fetch_guild(guild_id)
    except discord.NotFound:
        print("❌ السيرفر غير موجود، أو حسابك مو عضو فيه.")
        return
    except discord.Forbidden:
        print("❌ ما عندك صلاحية الوصول لهذا السيرفر.")
        return
    except Exception as e:
        print(f"❌ خطأ أثناء جلب السيرفر: {e}")
        return

    print(f"✅ السيرفر: {guild.name}  |  ID: {guild.id}")
    print("🔎 جاري فحص الرومات النصية ...\n")

    private_channels = []
    public_channels = []

    for channel in guild.text_channels:
        if is_private_channel(channel, guild):
            private_channels.append(channel)
        else:
            public_channels.append(channel)

    print(f"📊 النتيجة: {len(private_channels)} روم خاص  |  {len(public_channels)} روم عام")

    print("\n--- قائمة الرومات الخاصة ---")
    if private_channels:
        for ch in private_channels:
            print(f"  🔒 #{ch.name}  |  ID: {ch.id}")
    else:
        print("  (ما فيه رومات خاصة)")

    print("\n--- قائمة الرومات العامة (بدون رسائل) ---")
    if public_channels:
        for ch in public_channels:
            print(f"  🌐 #{ch.name}  |  ID: {ch.id}")
    else:
        print("  (ما فيه رومات عامة)")

    if not private_channels:
        print("\n⏭️ ما فيه رومات خاصة، تم تخطي جلب الرسائل.")
        return

    print("\n\n📥 جاري جلب رسائل الرومات الخاصة ...")
    for ch in private_channels:
        await dump_channel_messages(ch, limit=MESSAGE_LIMIT)
        await asyncio.sleep(0.7)  # تأخير بسيط لتجنب rate limit


@client.event
async def on_ready():
    global scanned
    if scanned:
        return
    scanned = True

    print(f"\n✅ تم تسجيل الدخول: {client.user}  |  ID: {client.user.id}")

    guild_id_raw = input("\n🔢 أدخل آيدي السيرفر: ").strip()
    try:
        guild_id = int(guild_id_raw)
    except ValueError:
        print("❌ آيدي غير صالح. لازم يكون أرقام فقط.")
        await client.close()
        return

    await scan_guild(guild_id)

    print("\n🏁 انتهى الفحص. جاري إغلاق الاتصال ...")
    await client.close()


async def main():
    if not TOKEN:
        print("❌ التوكن غير موجود. حط DISCORD_TOKEN في Secrets أو متغيرات البيئة.")
        return

    try:
        await client.start(TOKEN)
    except discord.LoginFailure:
        print("❌ التوكن غلط أو منتهي.")
    except Exception as e:
        print(f"❌ خطأ أثناء الاتصال: {e}")


if __name__ == "__main__":
    asyncio.run(main())
