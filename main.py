import os
import sys
import asyncio
import getpass
import logging
import requests
import discord
from discord.ext import commands

# =========================
# Logging
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
        code = input("📱 أدخل رمز 2FA من التطبيق: ").strip()
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
                tok = f.read().strip()
                return tok or None
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
# Bot (user account)
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


def fmt_author(m: discord.Message) -> str:
    try:
        name = m.author.name
        disc = getattr(m.author, "discriminator", "0")
        if disc and disc != "0":
            name = f"{name}#{disc}"
        return f"{name} [{m.author.id}]"
    except Exception:
        return "unknown"


def fmt_message(m: discord.Message) -> str:
    try:
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = "?"

    lines = [f"[{ts}] {fmt_author(m)}: {m.content or '[بدون نص]'}"]

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
            lines.append(f"    ✨ تفاعلات: {rs}")
    except Exception:
        pass

    try:
        if m.reference:
            lines.append(f"    ↩️ رد على: {m.reference.message_id}")
    except Exception:
        pass

    return "\n".join(lines)


async def safe_fetch_messages(channel, limit=None, max_retries=5):
    messages = []
    attempt = 0
    while True:
        try:
            async for msg in channel.history(limit=limit, oldest_first=True):
                messages.append(msg)
            return messages
        except discord.Forbidden:
            return []
        except discord.HTTPException as e:
            if e.status == 429:
                attempt += 1
                if attempt > max_retries:
                    return messages
                wait = 2.5 * attempt
                log.warning(f"⏳ Rate limit في #{channel.name}. انتظار {wait:.1f}s")
                await asyncio.sleep(wait)
                continue
            log.error(f"HTTP {e.status} في #{channel.name}: {e.text or e}")
            return messages
        except Exception as e:
            log.error(f"خطأ في #{channel.name}: {type(e).__name__}: {e}")
            return messages


async def scan_guild(guild: discord.Guild, limit=None):
    print(f"\n{'=' * 70}")
    print(f"🏰 السيرفر: {guild.name}  |  ID: {guild.id}")
    print(f"📚 عدد الرومات النصية: {len(guild.text_channels)}")
    print(f"{'=' * 70}\n")

    accessible = []
    blocked = []

    for ch in guild.text_channels:
        try:
            async for _ in ch.history(limit=1):
                break
            accessible.append(ch)
        except discord.Forbidden:
            blocked.append(ch)
        except Exception:
            blocked.append(ch)

    print(f"✅ قابلة للقراءة: {len(accessible)}  |  🚫 محجوبة: {len(blocked)}")

    if blocked:
        print("\n--- الرومات المحجوبة ---")
        for ch in blocked:
            print(f"  🚫 #{ch.name}  |  ID: {ch.id}")

    print("\n📥 جاري سحب الرسائل...\n")

    all_msgs = []
    for idx, ch in enumerate(accessible, 1):
        print(f"\n{'─' * 70}")
        print(f"📂 ({idx}/{len(accessible)}) #{ch.name}  |  ID: {ch.id}")
        print(f"{'─' * 70}")

        msgs = await safe_fetch_messages(ch, limit=limit)
        if not msgs:
            print("  (لا رسائل أو لا صلاحية)")
            continue

        for m in msgs:
            print(fmt_message(m))
        all_msgs.extend(msgs)
        log.info(f"{len(msgs)} رسالة من #{ch.name}")
        await asyncio.sleep(0.5)

    if all_msgs:
        all_msgs.sort(key=lambda m: m.created_at)
        print(f"\n\n{'=' * 70}")
        print(f"📊 الإجمالي: {len(all_msgs)} رسالة")
        try:
            with open("dump.txt", "w", encoding="utf-8") as f:
                for m in all_msgs:
                    f.write(fmt_message(m) + "\n\n")
            log.info("✅ حفظ نسخة في dump.txt")
        except Exception as e:
            log.warning(f"تعذر حفظ dump.txt: {e}")
    else:
        print("\n⚠️ ما فيه رسائل تم سحبها.")


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
            log.error(f"❌ خطأ في جلب السيرفر: {e}")
            await bot.close()
            return

    try:
        await scan_guild(guild)
    except Exception as e:
        log.error(f"❌ خطأ أثناء الفحص: {type(e).__name__}: {e}")
    finally:
        print("\n🏁 انتهى الفحص.")
        await bot.close()


# =========================
# Main
# =========================
def main():
    token = os.getenv("DISCORD_TOKEN") or load_cached_token()

    if not token:
        email, password = prompt_login()
        token, err = api_login(email, password)
        if err:
            if err == "CAPTCHA":
                print("\n❌ Discord طلب كابتشا. جرّب مرة ثانية.")
                print("   أو: افتح ديسكورد في المتصفح → F12 → Application →")
                print("   Local Storage → https://discord.com → انسخ قيمة token،")
                print("   وحطها في Secrets باسم DISCORD_TOKEN.")
            else:
                print(f"\n❌ فشل تسجيل الدخول: {err}")
            sys.exit(1)
        save_token(token)
        print("\n✅ تم تسجيل الدخول بنجاح")

    try:
        bot.run(token)
    except discord.LoginFailure:
        print("\n❌ التوكن غير صالح. حذف الكاش وإعادة المحاولة...")
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️ تم الإيقاف")
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()