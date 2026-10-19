import os
import sys
import types
import time
import asyncio
import logging
import threading
import requests

# =========================
# audioop stub — قبل استيراد discord
# =========================
if "audioop" not in sys.modules:
    try:
        import audioop  # noqa
    except ImportError:
        _m = types.ModuleType("audioop")
        def _stub(*a, **k):
            return b""
        def _stub_ratecv(*a, **k):
            return (b"", None)
        for _n in [
            "add", "adpcm2lin", "alaw2lin", "avg", "bias", "byteswap",
            "cross", "findfactor", "findfit", "findmax", "getsample",
            "lin2adpcm", "lin2alaw", "lin2lin", "lin2ulaw", "max",
            "maxpp", "minmax", "mul", "rms", "tomono", "tostereo", "ulaw2lin",
        ]:
            setattr(_m, _n, _stub)
        setattr(_m, "ratecv", _stub_ratecv)
        sys.modules["audioop"] = _m

import discord
from discord.ext import commands
from flask import Flask

# =========================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

API_BASE = "https://discord.com/api/v9"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
EMAIL = os.getenv("EMAIL", "").strip()
PASSWORD = os.getenv("PASSWORD", "").strip()
TWOFA = os.getenv("TWOFA", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# =========================
# Flask keep-alive
# =========================
app = Flask(__name__)

@app.route("/")
@app.route("/ping")
def ping():
    return "alive", 200

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def self_ping_loop():
    if not RENDER_URL:
        log.warning("⚠️ RENDER_EXTERNAL_URL غير موجود — self-ping معطّل")
        return
    while True:
        try:
            requests.get(f"{RENDER_URL}/ping", timeout=10)
            log.info("💓 self-ping")
        except Exception as e:
            log.warning(f"self-ping فشل: {e}")
        time.sleep(14 * 60)

# =========================
# Login
# =========================
def api_login(email, password, twofa=""):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
    }
    payload = {
        "email": email, "password": password, "undelete": False,
        "captcha_key": None, "login_source": None, "gift_code_sku_id": None,
    }
    try:
        r = requests.post(f"{API_BASE}/auth/login", json=payload, headers=headers, timeout=30)
        data = r.json()
    except Exception as e:
        return None, f"اتصال فشل: {e}"

    if "token" in data:
        return data["token"], None

    if data.get("mfa"):
        if not twofa:
            return None, "2FA مطلوب — حط TWOFA"
        try:
            r2 = requests.post(
                f"{API_BASE}/auth/mfa/totp",
                json={"code": twofa, "ticket": data["ticket"],
                      "login_source": None, "gift_code_sku_id": None},
                headers=headers, timeout=30,
            )
            d2 = r2.json()
            if "token" in d2:
                return d2["token"], None
            return None, f"MFA مرفوض: {d2}"
        except Exception as e:
            return None, f"MFA: {e}"

    if "captcha_key" in data:
        return None, "CAPTCHA — استخدم DISCORD_TOKEN مباشرة"
    return None, f"رد غير معروف: {data}"

# =========================
# Bot
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!x", self_bot=True, intents=intents, help_command=None)

def fmt_author(m):
    try:
        a = m.author
        n = getattr(a, "name", "unknown")
        d = getattr(a, "discriminator", "0")
        return f"{n}#{d} [{a.id}]" if d and d != "0" else f"{n} [{a.id}]"
    except Exception:
        return "unknown"

def fmt_message(m):
    try:
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = "?"
    out = [f"[{ts}] {fmt_author(m)}: {getattr(m, 'content', '') or '[بدون نص]'}"]
    try:
        for a in m.attachments:
            out.append(f"    📎 {a.filename} — {a.url}")
    except Exception:
        pass
    try:
        if m.reactions:
            out.append("    ✨ " + ", ".join(f"{r.emoji}×{r.count}" for r in m.reactions))
    except Exception:
        pass
    return "\n".join(out)

class Hunter:
    def __init__(self):
        self.captured = {}
        self.read = []
        self.blocked = []
        self.refs = set()

    def add(self, m):
        if m.id in self.captured:
            return
        self.captured[m.id] = m
        try:
            if m.reference and m.reference.message_id:
                self.refs.add(m.reference.message_id)
        except Exception:
            pass

    async def pull(self, ch):
        n = 0
        tries = 0
        while True:
            try:
                async for msg in ch.history(limit=None, oldest_first=True):
                    self.add(msg)
                    n += 1
                return n
            except discord.Forbidden:
                return -1
            except discord.HTTPException as e:
                if e.status == 429 and tries < 6:
                    tries += 1
                    await asyncio.sleep(2.5 * tries)
                    continue
                return n
            except Exception:
                return n

    async def ref_pull(self, ch):
        got = 0
        for mid in list(self.refs):
            try:
                m = await ch.fetch_message(mid)
                self.add(m)
                got += 1
            except Exception:
                continue
        return got

async def scan(guild, h):
    print(f"\n{'='*70}\n🏰 {guild.name} | {guild.id}\n{'='*70}")
    for i, ch in enumerate(guild.text_channels, 1):
        print(f"\n📂 ({i}/{len(guild.text_channels)}) #{ch.name} | {ch.id}")
        try:
            n = await h.pull(ch)
            if n >= 0:
                print(f"  ✅ {n}")
                h.read.append(ch)
            else:
                r = await h.ref_pull(ch)
                if r:
                    print(f"  🎯 مرجعي: {r}")
                    h.read.append(ch)
                else:
                    print("  ⛔ محجوب")
                    h.blocked.append(ch)
            await asyncio.sleep(0.4)
        except Exception as e:
            log.error(f"خطأ #{ch.name}: {e}")

    msgs = sorted(h.captured.values(), key=lambda m: m.created_at)
    print(f"\n📊 {len(msgs)} رسالة | {len(h.read)} مقروء | {len(h.blocked)} محجوب\n")
    for m in msgs:
        print(fmt_message(m) + "\n")
    try:
        with open("dump.txt", "w", encoding="utf-8") as f:
            for m in msgs:
                f.write(fmt_message(m) + "\n\n")
    except Exception:
        pass

@bot.event
async def on_ready():
    print(f"✅ {bot.user}")
    if not GUILD_ID:
        log.error("❌ GUILD_ID غير موجود")
        await bot.close()
        return
    try:
        gid = int(GUILD_ID)
    except Exception:
        log.error("❌ GUILD_ID غير صالح")
        await bot.close()
        return
    guild = bot.get_guild(gid)
    if guild is None:
        try:
            guild = await bot.fetch_guild(gid)
        except Exception as e:
            log.error(f"❌ {e}")
            await bot.close()
            return
    h = Hunter()
    try:
        await scan(guild, h)
    except Exception as e:
        log.error(f"❌ {e}")
    finally:
        print("🏁 انتهى")
        await bot.close()

# =========================
def start_bot(token):
    try:
        bot.run(token)
    except Exception as e:
        log.error(f"❌ البوت: {type(e).__name__}: {e}")

def main():
    token = TOKEN
    if not token and EMAIL and PASSWORD:
        token, err = api_login(EMAIL, PASSWORD, TWOFA)
        if err:
            log.error(f"❌ {err}")
            sys.exit(1)
        print("✅ تم تسجيل الدخول")

    if not token:
        log.error("❌ لازم DISCORD_TOKEN أو (EMAIL + PASSWORD)")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()
    threading.Thread(target=start_bot, args=(token,), daemon=True).start()

    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()