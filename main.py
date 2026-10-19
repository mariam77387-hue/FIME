import os
import sys
import types
import io
import time
import asyncio
import threading
import logging
import requests

# audioop stub (قبل discord)
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
from telegram.constants import ParseMode

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

API_BASE = "https://discord.com/api/v9"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")

TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DC_TOKEN     = os.getenv("DISCORD_TOKEN", "").strip()
DC_EMAIL     = os.getenv("DISCORD_EMAIL", "").strip()
DC_PASSWORD  = os.getenv("DISCORD_PASSWORD", "").strip()
DC_2FA       = os.getenv("DISCORD_2FA", "").strip()
DEFAULT_GUILD = os.getenv("GUILD_ID", "").strip()
RENDER_URL   = os.getenv("RENDER_EXTERNAL_URL", "").strip()

state = {
    "discord_token": DC_TOKEN or None,
    "token_type": None,
    "discord_bot": None,
    "discord_loop": None,
    "discord_ready": False,
    "tg_loop": None,
    "tg_app": None,
    "default_chat": int(TG_CHAT_ID) if TG_CHAT_ID.isdigit() else None,
    "scanning": False,
    "last_error": None,
    "login_locked": False,
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
    while True:
        try:
            requests.get(f"{RENDER_URL}/ping", timeout=10)
        except Exception:
            pass
        time.sleep(14 * 60)

# =========================
# Telegram senders
# =========================
def send_tg(chat_id, text, md=False):
    if not chat_id or not state["tg_loop"] or not state["tg_app"]:
        return
    async def _do():
        try:
            for i in range(0, len(text), 4000):
                await state["tg_app"].bot.send_message(
                    chat_id=chat_id,
                    text=text[i:i+4000],
                    parse_mode=ParseMode.MARKDOWN if md else None,
                    disable_web_page_preview=True,
                )
        except Exception as e:
            log.error(f"tg send: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["tg_loop"])

def send_tg_doc(chat_id, filename, data, caption=""):
    if not chat_id or not state["tg_loop"] or not state["tg_app"]:
        return
    async def _do():
        try:
            buf = io.BytesIO(data)
            buf.name = filename
            await state["tg_app"].bot.send_document(chat_id=chat_id, document=buf, caption=caption[:1000])
        except Exception as e:
            log.error(f"tg doc: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["tg_loop"])

def notify(text):
    chat = state.get("default_chat")
    if chat:
        send_tg(chat, text)

# =========================
# Discord login
# =========================
def api_login(email, password, twofa="", max_retries=3):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
    }
    payload = {"email": email, "password": password, "undelete": False,
               "captcha_key": None, "login_source": None, "gift_code_sku_id": None}

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(f"{API_BASE}/auth/login", json=payload, headers=headers, timeout=30)
            try:
                data = r.json()
            except Exception:
                last_err = f"HTTP {r.status_code}"
                data = {}
        except Exception as e:
            last_err = f"اتصال: {e}"
            data = {}

        if "token" in data:
            return data["token"], None

        if data.get("mfa"):
            if not twofa:
                return None, "الحساب يحتاج 2FA — حط DISCORD_2FA في env"
            try:
                r2 = requests.post(
                    f"{API_BASE}/auth/mfa/totp",
                    json={"code": twofa, "ticket": data["ticket"],
                          "login_source": None, "gift_code_sku_id": None},
                    headers=headers, timeout=30)
                d2 = r2.json()
                if "token" in d2:
                    return d2["token"], None
                return None, f"2FA مرفوض: {d2.get('message', d2)}"
            except Exception as e:
                return None, f"2FA: {e}"

        if "captcha_key" in data or "captcha_sitekey" in data:
            last_err = "CAPTCHA من Discord — جرب بعد دقائق"
            if attempt < max_retries:
                wait = 15 * attempt
                log.warning(f"CAPTCHA. انتظار {wait}s (محاولة {attempt}/{max_retries})")
                time.sleep(wait)
                continue
            return None, last_err

        if r.status_code in (400, 401):
            return None, "الإيميل أو كلمة المرور غلط"
        if r.status_code == 429:
            last_err = "Rate limit من Discord"
            time.sleep(20 * attempt)
            continue

        last_err = f"فشل: {data.get('message', data) or last_err}"
        time.sleep(5 * attempt)

    return None, last_err or "فشل غير معروف"

def detect_token_type(token):
    for prefix, kind in [("", "user"), ("Bot ", "bot")]:
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

    bot = commands.Bot(
        command_prefix="!x",
        self_bot=(token_type == "user"),
        intents=intents,
        help_command=None,
    )
    state["discord_bot"] = bot

    @bot.event
    async def on_ready():
        state["discord_ready"] = True
        log.info(f"✅ Discord: {bot.user} ({token_type})")
        notify(f"✅ Discord جاهز\nالحساب: {bot.user}\nالنوع: {token_type}\nأرسل /help")

    try:
        loop.run_until_complete(bot.start(token))
    except Exception as e:
        state["last_error"] = f"discord: {e}"
        log.error(state["last_error"])
        notify(f"❌ فشل Discord: {e}")

def start_discord():
    if state["discord_bot"]:
        return True
    token = state["discord_token"]
    if not token:
        return False
    kind = detect_token_type(token)
    if not kind:
        state["last_error"] = "توكن Discord غير صالح"
        return False
    state["token_type"] = kind
    threading.Thread(target=_discord_worker, args=(token, kind), daemon=True).start()
    return True

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
        for i, emb in enumerate(getattr(m, "embeds", []) or [], 1):
            t = emb.title or ""
            d = (emb.description or "")[:150]
            out.append(f"    🧩 [{i}] {t} — {d}")
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

def is_private_channel(ch, guild):
    try:
        ow = ch.overwrites_for(guild.default_role)
        return ow.view_channel is False
    except Exception:
        return False

# =========================
# Scan
# =========================
async def do_scan(guild_id, chat_id, only_private=False):
    state["scanning"] = True
    try:
        bot = state["discord_bot"]
        if not bot or not state["discord_ready"]:
            send_tg(chat_id, "⏳ Discord لسا ما اتصل. /status")
            return

        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except Exception as e:
                send_tg(chat_id, f"❌ ما قدرت أجيب السيرفر: {e}")
                return

        channels = guild.text_channels
        if only_private:
            channels = [c for c in channels if is_private_channel(c, guild)]

        if not channels:
            send_tg(chat_id, "⚠️ ما فيه رومات مطابقة")
            return

        mode = "🔒 خاصة فقط" if only_private else "📚 كل الرومات"
        send_tg(chat_id, f"🔍 بدء الفحص: {guild.name}\nالوضع: {mode}\nرومات: {len(channels)}")

        h = Hunter()
        total = len(channels)
        last_update = 0

        for i, ch in enumerate(channels, 1):
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
                await asyncio.sleep(0.25)
            except Exception as e:
                log.error(f"{ch.name}: {e}")

            if i - last_update >= 5 or i == total:
                send_tg(chat_id, f"⏳ {i}/{total} — رسائل: {len(h.captured)}")
                last_update = i

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

        full = "".join(fmt_message(m) + "\n\n" for m in msgs)
        data = full.encode("utf-8")

        if len(data) < 3500:
            send_tg(chat_id, full)
        else:
            send_tg_doc(chat_id, f"{guild.name}_all.txt", data, summary)

            # ملف منفصل لكل روم
            by_channel = {}
            for m in msgs:
                by_channel.setdefault(getattr(m.channel, "name", "?"), []).append(m)
            for ch_name, ch_msgs in by_channel.items():
                text = "".join(fmt_message(m) + "\n\n" for m in ch_msgs)
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in ch_name)[:50]
                send_tg_doc(chat_id, f"{safe}.txt", text.encode("utf-8"), f"#{ch_name} — {len(ch_msgs)}")
                await asyncio.sleep(1)
    finally:
        state["scanning"] = False

# =========================
# Telegram handlers
# =========================
HELP = (
    "*أوامر البوت*\n\n"
    "/private `<guild_id>` — فحص الرومات الخاصة فقط\n"
    "/scan `<guild_id>` — فحص كل الرومات\n"
    "/channels `<guild_id>` — عرض الرومات مصنفة\n"
    "/status — حالة الاتصال\n"
    "/cancel — إلغاء عرض\n\n"
    "لو حطيت `GUILD_ID` في env، تقدر تحذف الآيدي."
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["default_chat"] = update.effective_chat.id
    ready = state["discord_ready"]
    err = f"\n\n⚠️ آخر خطأ: `{state['last_error']}`" if state["last_error"] else ""
    await update.message.reply_text(
        f"👋 أهلاً\nDiscord: {'✅ جاهز' if ready else '⏳ يتصل...'}{err}\n\n{HELP}",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = state["discord_bot"]
    ready = bot and state["discord_ready"]
    lines = [
        f"*Discord:* {'✅' if ready else '❌'}",
        f"*النوع:* {state['token_type'] or '—'}",
        f"*الحساب:* {bot.user if ready else '—'}",
        f"*فحص جاري:* {'نعم' if state['scanning'] else 'لا'}",
    ]
    if state["last_error"]:
        lines.append(f"*آخر خطأ:* `{state['last_error']}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def _resolve_guild(context):
    if context.args:
        try:
            return int(context.args[0]), None
        except ValueError:
            return None, "❌ آيدي غير صالح"
    if DEFAULT_GUILD:
        try:
            return int(DEFAULT_GUILD), None
        except ValueError:
            return None, "❌ GUILD_ID في env غير صالح"
    return None, "الاستخدام: أرسل الأمر مع آيدي السيرفر"

async def cmd_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["default_chat"] = update.effective_chat.id
    if state["scanning"]:
        await update.message.reply_text("⏳ فيه فحص جاري")
        return
    if not state["discord_ready"]:
        await update.message.reply_text("❌ Discord ما اتصل بعد")
        return
    gid, err = await _resolve_guild(context)
    if err:
        await update.message.reply_text(err)
        return
    asyncio.run_coroutine_threadsafe(
        do_scan(gid, update.effective_chat.id, only_private=True),
        state["discord_loop"],
    )
    await update.message.reply_text("▶️ بدء فحص الرومات الخاصة...")

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["default_chat"] = update.effective_chat.id
    if state["scanning"]:
        await update.message.reply_text("⏳ فيه فحص جاري")
        return
    if not state["discord_ready"]:
        await update.message.reply_text("❌ Discord ما اتصل بعد")
        return
    gid, err = await _resolve_guild(context)
    if err:
        await update.message.reply_text(err)
        return
    asyncio.run_coroutine_threadsafe(
        do_scan(gid, update.effective_chat.id, only_private=False),
        state["discord_loop"],
    )
    await update.message.reply_text("▶️ بدء فحص كل الرومات...")

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["default_chat"] = update.effective_chat.id
    if not state["discord_ready"]:
        await update.message.reply_text("❌ Discord ما اتصل بعد")
        return
    gid, err = await _resolve_guild(context)
    if err:
        await update.message.reply_text(err)
        return

    async def _list():
        bot = state["discord_bot"]
        guild = bot.get_guild(gid)
        if guild is None:
            try:
                guild = await bot.fetch_guild(gid)
            except Exception as e:
                send_tg(update.effective_chat.id, f"❌ {e}")
                return
        private = [c for c in guild.text_channels if is_private_channel(c, guild)]
        public = [c for c in guild.text_channels if not is_private_channel(c, guild)]
        lines = [f"*🏰 {guild.name}*",
                 f"🔒 خاصة: {len(private)} | 🌐 عامة: {len(public)}", ""]
        if private:
            lines.append("*🔒 الرومات الخاصة:*")
            for c in private[:30]:
                lines.append(f"• #{c.name} (`{c.id}`)")
            if len(private) > 30:
                lines.append(f"... و{len(private)-30} روم آخر")
            lines.append("")
        if public:
            lines.append("*🌐 الرومات العامة:*")
            for c in public[:20]:
                lines.append(f"• #{c.name}")
            if len(public) > 20:
                lines.append(f"... و{len(public)-20} روم آخر")
        send_tg(update.effective_chat.id, "\n".join(lines), md=True)

    asyncio.run_coroutine_threadsafe(_list(), state["discord_loop"])
    await update.message.reply_text("📋 جاري الجلب...")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ لإيقاف فحص جاري، انتظر ينتهي أو أعد تشغيل الخدمة")

# =========================
# post_init — يبدأ discord auto
# =========================
async def post_init(app: Application):
    state["tg_loop"] = asyncio.get_running_loop()
    state["tg_app"] = app
    log.info("✅ Telegram جاهز")

    if state["discord_token"]:
        if start_discord():
            log.info("⏳ Discord auto-start")
    elif DC_EMAIL and DC_PASSWORD:
        notify("🔐 جاري تسجيل الدخول إلى Discord...")
        token, err = await asyncio.to_thread(api_login, DC_EMAIL, DC_PASSWORD, DC_2FA)
        if token:
            state["discord_token"] = token
            if start_discord():
                log.info("✅ auto login نجح")
        else:
            state["last_error"] = err
            log.error(f"auto login: {err}")
            notify(f"❌ فشل تسجيل دخول Discord:\n`{err}`")
    else:
        notify("⚠️ ما فيه توكن ولا إيميل/باسوورد في env — أضف DISCORD_EMAIL و DISCORD_PASSWORD")

# =========================
def main():
    if not TG_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN غير موجود في env")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()

    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("private", cmd_private))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    log.info("🤖 Telegram polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()