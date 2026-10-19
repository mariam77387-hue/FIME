import os
import sys
import asyncio
import getpass
import logging
import requests
import discord
from discord.ext import commands

# =========================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

CACHE_FILE = ".discord_session_token"
API_BASE = "https://discord.com/api/v9"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# =========================
# Login
# =========================
def safe_getpass(prompt: str) -> str:
    try:
        return getpass.getpass(prompt)
    except Exception:
        print(prompt.replace("(لن تظهر)", "(سيظهر)"))
        return input()


def prompt_login():
    print("\n" + "=" * 60)
    print("🔐 تسجيل الدخول إلى حساب ديسكورد")
    print("=" * 60)
    email = input("📧 البريد/الهاتف: ").strip()
    password = safe_getpass("🔑 كلمة المرور (لن تظهر): ")
    return email, password


def api_login(email: str, password: str):
    url = f"{API_BASE}/auth/login"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
    }
    payload = {
        "email": email,
        "password": password,
        "undelete": False,
        "captcha_key": None,
        "login_source": None,
        "gift_code_sku_id": None,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as e:
        return None, f"فشل الاتصال: {e}"

    try:
        data = r.json()
    except Exception:
        return None, f"رد غير متوقع (HTTP {r.status_code})"

    if "token" in data:
        return data["token"], None

    if data.get("mfa"):
        print("\n🔒 الحساب عليه تحقق بخطوتين (2FA)")
        code = input("📱 أدخل رمز 2FA: ").strip()
        try:
            r2 = requests.post(
                f"{API_BASE}/auth/mfa/totp",
                json={
                    "code": code,
                    "ticket": data["ticket"],
                    "login_source": None,
                    "gift_code_sku_id": None,
                },
                headers=headers,
                timeout=30,
            )
            d2 = r2.json()
        except Exception as e:
            return None, f"MFA فشل: {e}"
        if "token" in d2:
            return d2["token"], None
        return None, f"MFA مرفوض: {d2}"

    if "captcha_key" in data:
        return None, "CAPTCHA"

    return None, f"رد غير معروف: {data}"


def load_cached_token():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                t = f.read().strip()
                return t or None
        except Exception:
            return None
    return None


def save_token(token: str):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(token)
    except Exception as e:
        log.warning(f"تعذر حفظ التوكن: {e}")


# =========================
# Bot
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(
    command_prefix="!x",
    self_bot=True,
    intents=intents,
    help_command=None,
)


def fmt_author(m) -> str:
    try:
        a = m.author
        name = getattr(a, "name", "unknown")
        disc = getattr(a, "discriminator", "0")
        if disc and disc != "0":
            name = f"{name}#{disc}"
        return f"{name} [{a.id}]"
    except Exception:
        return "unknown"


def fmt_message(m) -> str:
    try:
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = "?"
    lines = [f"[{ts}] {fmt_author(m)}: {getattr(m, 'content', '') or '[بدون نص]'}"]
    try:
        for att in m.attachments:
            lines.append(f"    📎 {att.filename} — {att.url}")
    except Exception:
        pass
    try:
        for i, emb in enumerate(m.embeds, 1):
            t = emb.title or "(بدون عنوان)"
            d = (emb.description or "")[:200]
            lines.append(f"    🧩 Embed {i}: {t} — {d}")
    except Exception:
        pass
    try:
        if m.reactions:
            rs = ", ".join(f"{r.emoji}×{r.count}" for r in m.reactions)
            lines.append(f"    ✨ {rs}")
    except Exception:
        pass
    try:
        if m.reference:
            lines.append(f"    ↩️ رد على: {m.reference.message_id}")
    except Exception:
        pass
    return "\n".join(lines)


# =========================
# قنّاص الرسائل الخاص
# =========================
class PrivateHunter:
    def __init__(self):
        self.captured = {}          # message_id -> message
        self.blocked_channels = []  # رومات ما قدرنا نقرأها
        self.read_channels = []     # رومات قرأناها
        self.referenced_ids = set() # IDs لرسائل ردت عليها رسائل متاحة

    def add(self, m):
        if m.id not in self.captured:
            self.captured[m.id] = m
            try:
                if m.reference and m.reference.message_id:
                    self.referenced_ids.add(m.reference.message_id)
            except Exception:
                pass

    async def direct_pull(self, channel, limit=None):
        """سحب مباشر — الأقوى: يستخدم صلاحيات حسابك."""
        grabbed = 0
        attempt = 0
        while True:
            try:
                async for msg in channel.history(limit=limit, oldest_first=True):
                    self.add(msg)
                    grabbed += 1
                return grabbed
            except discord.Forbidden:
                return -1  # محجوب
            except discord.HTTPException as e:
                if e.status == 429:
                    attempt += 1
                    if attempt > 6:
                        return grabbed
                    wait = 2.5 * attempt
                    log.warning(f"⏳ rate limit #{channel.name}, انتظار {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                return grabbed
            except Exception:
                return grabbed

    async def try_reference_pull(self, channel):
        """محاولة اختطاف رسائل مرجعية داخل روم محجوب."""
        got = 0
        for mid in list(self.referenced_ids):
            try:
                m = await channel.fetch_message(mid)
                self.add(m)
                got += 1
            except discord.NotFound:
                continue
            except discord.Forbidden:
                break  # ما في أمل
            except Exception:
                continue
        return got

    async def try_search_pull(self, guild, channel):
        """محاولة البحث داخل الروم (بعض الحسابات تمر هنا)."""
        got = 0
        try:
            async for msg in channel.history(limit=None, oldest_first=True):
                self.add(msg)
                got += 1
        except Exception:
            pass
        return got

    async def scan_channel(self, channel, guild):
        print(f"\n{'─' * 70}")
        print(f"📂 #{channel.name}  |  ID: {channel.id}")
        print(f"{'─' * 70}")

        # 1) محاولة سحب مباشر
        n = await self.direct_pull(channel)
        if n >= 0:
            print(f"  ✅ قراءة مباشرة: {n} رسالة")
            self.read_channels.append(channel)
            await asyncio.sleep(0.4)
            return n

        # 2) محاولة سحب مرجعي
        print("  🔒 مباشر محجوب — تجربة صيد مرجعي...")
        r = await self.try_reference_pull(channel)
        if r > 0:
            print(f"  🎯 اختطاف مرجعي: {r} رسالة")
            self.read_channels.append(channel)
            await asyncio.sleep(0.4)
            return r

        # 3) محاولة أخيرة عبر fetch_message عشوائي
        try:
            await channel.fetch_message(channel.last_message_id or 0)
        except Exception:
            pass

        print("  ⛔ محجوب تماماً (Discord يرفض)")
        self.blocked_channels.append(channel)
        await asyncio.sleep(0.3)
        return 0


# =========================
# الفحص
# =========================
async def scan_guild(guild, hunter: PrivateHunter):
    print(f"\n{'=' * 70}")
    print(f"🏰 {guild.name}  |  ID: {guild.id}")
    print(f"📚 رومات نصية: {len(guild.text_channels)}")
    print(f"{'=' * 70}")

    # المرحلة 1: نجمع كل ما نقدر من كل روم
    for ch in guild.text_channels:
        try:
            await hunter.scan_channel(ch, guild)
        except Exception as e:
            log.error(f"خطأ في #{getattr(ch, 'name', '?')}: {type(e).__name__}: {e}")

    # المرحلة 2: صيد مرجعي متعدد الجولات
    if hunter.referenced_ids:
        print(f"\n🎯 مرحلة الصيد المرجعي: {len(hunter.referenced_ids)} مرجع")
        for ch in guild.text_channels:
            if ch in hunter.read_channels:
                continue
            try:
                await hunter.try_reference_pull(ch)
                await asyncio.sleep(0.3)
            except Exception:
                continue

    # المرحلة 3: ترتيب وطباعة
    msgs = list(hunter.captured.values())
    msgs.sort(key=lambda m: m.created_at)

    print(f"\n\n{'=' * 70}")
    print(f"📊 النتيجة النهائية")
    print(f"  ✅ رومات مقروءة: {len(hunter.read_channels)}")
    print(f"  🚫 رومات محجوبة: {len(hunter.blocked_channels)}")
    print(f"  💬 رسائل ملتقطة: {len(msgs)}")
    print(f"{'=' * 70}\n")

    # كشف الرومات الخاصة (اللي محجوبة عن @everyone)
    private_ids = set()
    for ch in guild.text_channels:
        try:
            ow = ch.overwrites_for(guild.default_role)
            if ow.view_channel is False:
                private_ids.add(ch.id)
        except Exception:
            pass

    print("--- الرومات الخاصة ---")
    for ch in guild.text_channels:
        if ch.id in private_ids:
            status = "✅ مقروء" if ch in hunter.read_channels else "🚫 محجوب"
            print(f"  🔒 #{ch.name} [{ch.id}] — {status}")

    print("\n--- الرسائل (مرتبة زمنياً) ---\n")
    for m in msgs:
        try:
            ch_name = getattr(m.channel, "name", "?")
            ch_private = "🔒" if getattr(m.channel, "id", 0) in private_ids else "🌐"
            print(f"{ch_private} #{ch_name}")
            print(fmt_message(m))
            print()
        except Exception:
            continue

    # حفظ
    try:
        with open("dump.txt", "w", encoding="utf-8") as f:
            for m in msgs:
                try:
                    f.write(f"#{getattr(m.channel, 'name', '?')}\n")
                    f.write(fmt_message(m) + "\n\n")
                except Exception:
                    continue
        log.info("✅ حفظ dump.txt")
    except Exception as e:
        log.warning(f"تعذر حفظ dump.txt: {e}")


# =========================
# on_ready
# =========================
@bot.event
async def on_ready():
    print(f"\n✅ تم الدخول كـ {bot.user}")

    try:
        gid = int(input("\n🔢 آيدي السيرفر: ").strip())
    except (ValueError, TypeError):
        log.error("❌ آيدي غير صالح")
        await bot.close()
        return

    guild = bot.get_guild(gid)
    if guild is None:
        try:
            guild = await bot.fetch_guild(gid)
        except discord.NotFound:
            log.error("❌ السيرفر غير موجود أو ما أنت عضو فيه")
            await bot.close()
            return
        except Exception as e:
            log.error(f"❌ {e}")
            await bot.close()
            return

    hunter = PrivateHunter()
    try:
        await scan_guild(guild, hunter)
    except Exception as e:
        log.error(f"❌ خطأ في الفحص: {type(e).__name__}: {e}")
    finally:
        print("\n🏁 انتهى الفحص.")
        await bot.close()


# =========================
def main():
    token = os.getenv("DISCORD_TOKEN") or load_cached_token()

    if not token:
        email, password = prompt_login()
        token, err = api_login(email, password)
        if err:
            if err == "CAPTCHA":
                print("\n❌ Discord طلب كابتشا.")
                print("   حل يدوي: افتح ديسكورد → F12 → Application → Local Storage →")
                print("   https://discord.com → انسخ token → حطه في Secrets باسم DISCORD_TOKEN")
            else:
                print(f"\n❌ فشل: {err}")
            sys.exit(1)
        save_token(token)
        print("\n✅ تم تسجيل الدخول")

    try:
        bot.run(token)
    except discord.LoginFailure:
        print("\n❌ توكن غير صالح، حذف الكاش...")
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️ إيقاف")
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()