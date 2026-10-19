import os
import sys
import types
import io
import asyncio
import threading
import logging
import requests

# =========================
# audioop stub (قبل discord)
# =========================
if "audioop" not in sys.modules:
    try:
        import audioop  # noqa
    except ImportError:
        _m = types.ModuleType("audioop")
        def _stub(*a, **k): return b""
        def _stub_ratecv(*a, **k): return (b"", None)
        for _n in ["add","adpcm2lin","alaw2lin","avg","bias","byteswap","cross",
                   "findfactor","findfit","findmax","getsample","lin2adpcm",
                   "lin2alaw","lin2lin","lin2ulaw","max","maxpp","minmax","mul",
                   "rms","tomono","tostereo","ulaw2lin"]:
            setattr(_m, _n, _stub)
        _m.ratecv = _stub_ratecv
        sys.modules["audioop"] = _m

import discord
from discord.ext import commands
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

API_BASE = "https://discord.com/api/v9"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_EMAIL = os.getenv("DISCORD_EMAIL", "").strip()
DISCORD_PASSWORD = os.getenv("DISCORD_PASSWORD", "").strip()
DISCORD_2FA = os.getenv("DISCORD_2FA", "").strip()
DEFAULT_GUILD = os.getenv("GUILD_ID", "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

state = {
    "discord_token": DISCORD_TOKEN or None,
    "token_type": None,
    "discord_bot": None,
    "discord_loop": None,
    "telegram_loop": None,
    "telegram_app": None,
    "scanning": False,
    "chat_id": None,
}

# =========================
# Flask keep-alive
# =========================
flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/ping")
def ping():
    return "alive", 200

def run_flask():
    port = int(os.getenv("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)

def self_ping_loop():
    if not RENDER_URL:
        return
    import time
    while True:
        try:
            requests.get(f"{RENDER_URL}/ping", timeout=10)
        except Exception:
            pass
        time.sleep(14 * 60)

# =========================
# Discord login
# =========================
def api_login(email, password, twofa=""):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
    }
    payload = {"email": email, "password": password, "undelete": False,
               "captcha_key": None, "login_source": None, "gift_code_sku_id": None}
    try:
        r = requests.post(f"{API_BASE}/auth/login", json=payload, headers=headers, timeout=30)
        data = r.json()
    except Exception as e:
        return None, f"اتصال: {e}"
    if "token" in data:
        return data["token"], None
    if data.get("mfa"):
        if not twofa:
            return None, "الحساب يحتاج 2FA"
        try:
            r2 = requests.post(
                f"{API_BASE}/auth/mfa/totp",
                json={"code": twofa, "ticket": data["ticket"],
                      "login_source": None, "gift_code_sku_id": None},
                headers=headers, timeout=30)
            d2 = r2.json()
            if "token" in d2:
                return d2["token"], None
            return None, f"MFA: {d2.get('message', d2)}"
        except Exception as e:
            return None, f"MFA: {e}"
    if "captcha_key" in data:
        return None, "Discord طلب CAPTCHA — جرب لاحقاً"
    return None, f"فشل: {data.get('message', data)}"

def detect_token_type(token):
    for prefix, kind in [("Bot ", "bot"), ("", "user")]:
        try:
            r = requests.get(f"{API_BASE}/users/@me",
                             headers={"Authorization": f"{prefix}{token}", "User-Agent": UA},
                             timeout=10)
            if r.status_code == 200:
                return kind
        except Exception:
            pass
    return None

# =========================
# Discord bot thread
# =========================
def _discord_worker(token, token_type):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state["discord_loop"] = loop

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True

    self_bot = (token_type == "user")
    bot = commands.Bot(command_prefix="!x", self_bot=self_bot,
                       intents=intents, help_command=None)
    state["discord_bot"] = bot

    @bot.event
    async def on_ready():
        log.info(f"✅ Discord: {bot.user} ({token_type})")
        chat = state.get("chat_id")
        if chat:
            send_tg(chat, f"✅ تم اتصال Discord: {bot.user}")

    try:
        loop.run_until_complete(bot.start(token))
    except Exception as e:
        log.error(f"discord: {e}")
        chat = state.get("chat_id")
        if chat:
            send_tg(chat, f"❌ فشل Discord: {e}")

def start_discord():
    if state["discord_bot"]:
        return True
    token = state["discord_token"]
    if not token:
        return False
    kind = detect_token_type(token)
    if not kind:
        log.error("❌ توكن Discord غير صالح")
        return False
    state["token_type"] = kind
    threading.Thread(target=_discord_worker, args=(token, kind), daemon=True).start()
    return True

# =========================
# Telegram senders (thread-safe)
# =========================
def send_tg(chat_id, text):
    if not state["telegram_loop"] or not state["telegram_app"]:
        return
    async def _do():
        try:
            for i in range(0, len(text), 4000):
                await state["telegram_app"].bot.send_message(chat_id=chat_id, text=text[i:i+4000])
        except Exception as e:
            log.error(f"tg: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["telegram_loop"])

def send_tg_doc(chat_id, filename, data, caption=""):
    if not state["telegram_loop"] or not state["telegram_app"]:
        return
    async def _do():
        try:
            buf = io.BytesIO(data)
            buf.name = filename
            await state["telegram_app"].bot.send_document(chat_id=chat_id, document=buf, caption=caption[:1000])
        except Exception as e:
            log.error(f"tg doc: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["telegram_loop"])

# =========================
# Formatting
# =========================
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

# =========================
# Hunter
# =========================
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

# =========================
# Scan
# =========================
async def do_scan(guild_id, chat_id):
    state["scanning"] = True
    try:
        bot = state["discord_bot"]
        if not bot or not bot.is_ready():
            send_tg(chat_id, "⏳ Discord لسا ما اتصل. جرب بعد ثواني أو استخدم /status")
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception as e:
                send_tg(chat_id, f"❌ ما قدرت أجيب السيرفر: {e}")
                return

        send_tg(chat_id, f"🔍 بدء فحص: {guild.name}\nرومات: {len(guild.text_channels)}")

        h = Hunter()
        total = len(guild.text_channels)
        for i, ch in enumerate(guild.text_channels, 1):
            if i == total or i % 5 == 0:
                send_tg(chat_id, f"📂 ({i}/{total}) ...")
            try:
                n = await h.pull(ch)
                if n >= 0:
                    h.read.append(ch)
                else:
                    r = await h.ref_pull(ch)
                    if r:
                        h.read.append(ch)
                    else:
                        h.blocked.append(ch)
                await asyncio.sleep(0.3)
            except Exception as e:
                log.error(f"{ch.name}: {e}")

        msgs = sorted(h.captured.values(), key=lambda m: m.created_at)

        summary = (
            f"✅ انتهى\n"
            f"🏰 {guild.name}\n"
            f"📖 مقروء: {len(h.read)} | 🚫 محجوب: {len(h.blocked)}\n"
            f"💬 رسائل: {len(msgs)}"
        )
        send_tg(chat_id, summary)

        if not msgs:
            return

        text_data = "".join(fmt_message(m) + "\n\n" for m in msgs)
        data = text_data.encode("utf-8")

        if len(data) < 3500:
            send_tg(chat_id, text_data)
        else:
            send_tg_doc(chat_id, f"{guild.name}_messages.txt", data, summary)
    finally:
        state["scanning"] = False

# =========================
# Telegram handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    ready = state["discord_bot"] and state["discord_bot"].is_ready()
    text = (
        "👋 أهلاً\n\n"
        "الأوامر:\n"
        "• /scan <guild_id> — فحص سيرفر\n"
        "• /status — الحالة\n"
        "• /login <email> <password> [2fa] — دخول Discord\n\n"
        f"Discord: {'✅ متصل' if ready else '⏳ يتصل...'}"
    )
    await update.message.reply_text(text)

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("الاستخدام: /login <email> <password> [2fa]")
        return
    email, password = args[0], args[1]
    twofa = args[2] if len(args) > 2 else ""
    msg = await update.message.reply_text("⏳ جاري تسجيل الدخول...")
    token, err = api_login(email, password, twofa)
    if err:
        await msg.edit_text(f"❌ {err}")
        return
    if state["discord_bot"]:
        await msg.edit_text("⚠️ Discord متصل مسبقاً. أعد تشغيل الخدمة لتغيير الحساب.")
        return
    state["discord_token"] = token
    if start_discord():
        await msg.edit_text("✅ تم الدخول، جاري الاتصال...")
    else:
        await msg.edit_text("❌ فشل تشغيل Discord")

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    if state["scanning"]:
        await update.message.reply_text("⏳ فيه فحص جاري، انتظر")
        return
    if not state["discord_loop"]:
        await update.message.reply_text("❌ Discord ما اتصل بعد. استخدم /status")
        return

    if context.args:
        try:
            gid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ آيدي غير صالح")
            return
    elif DEFAULT_GUILD:
        try:
            gid = int(DEFAULT_GUILD)
        except ValueError:
            await update.message.reply_text("❌ GUILD_ID في env غير صالح")
            return
    else:
        await update.message.reply_text("الاستخدام: /scan <guild_id>")
        return

    chat_id = update.effective_chat.id
    asyncio.run_coroutine_threadsafe(do_scan(gid, chat_id), state["discord_loop"])
    await update.message.reply_text("▶️ بدء الفحص...")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    bot = state["discord_bot"]
    ready = bot and bot.is_ready()
    lines = [
        f"Discord: {'✅' if ready else '❌'}",
        f"نوع: {state['token_type'] or '—'}",
        f"فحص: {'⏳' if state['scanning'] else '❌'}",
    ]
    if ready:
        lines.append(f"الحساب: {bot.user}")
    await update.message.reply_text("\n".join(lines))

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    await update.message.reply_text("ℹ️ الإلغاء غير مدعوم حالياً. أعد تشغيل الخدمة إذا تبي توقف.")

# =========================
# Post-init (بعد تشغيل telegram loop)
# =========================
async def post_init(app: Application):
    state["telegram_loop"] = asyncio.get_running_loop()
    state["telegram_app"] = app
    log.info("✅ Telegram جاهز")

    if state["discord_token"]:
        if start_discord():
            log.info("⏳ Discord auto-start")
    elif DISCORD_EMAIL and DISCORD_PASSWORD:
        token, err = api_login(DISCORD_EMAIL, DISCORD_PASSWORD, DISCORD_2FA)
        if token:
            state["discord_token"] = token
            start_discord()
        else:
            log.error(f"auto login: {err}")

# =========================
def main():
    if not TELEGRAM_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN غير موجود في env")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    log.info("🤖 Telegram polling...")
    app.run_polling()

if __name__ == "__main__":
    main()