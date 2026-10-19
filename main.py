import os
import sys
import types
import io
import time
import json
import math
import base64
import random
import hashlib
import urllib.parse
import asyncio
import threading
import logging
import requests
import httpx

# audioop stub
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
from telegram.error import TelegramError

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

API_BASE = "https://discord.com/api/v9"

TG_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DC_TOKEN      = os.getenv("DISCORD_TOKEN", "").strip()
DC_EMAIL      = os.getenv("DISCORD_EMAIL", "").strip()
DC_PASSWORD   = os.getenv("DISCORD_PASSWORD", "").strip()
DC_2FA        = os.getenv("DISCORD_2FA", "").strip()
PROXY_URL     = os.getenv("PROXY_URL", "").strip()
DEFAULT_GUILD = os.getenv("GUILD_ID", "").strip()
RENDER_URL    = os.getenv("RENDER_EXTERNAL_URL", "").strip()

NONECAP_KEY = os.getenv("NONECAP_API_KEY", "").strip() or "nc_live_OIWI2jryLCq_-yhorZBwpzft3NwXxjiQ"

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
    "is_render": bool(RENDER_URL),
}

# ============================================================
# hCaptcha Accessibility (مجاني — بدون مفتاح)
# ============================================================
_HC_HEADERS = {
    "Host": "hcaptcha.com",
    "Connection": "keep-alive",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-type": "application/json; charset=utf-8",
    "Origin": "https://newassets.hcaptcha.com",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "https://newassets.hcaptcha.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

_HC_COOKIES = {
    "hc_accessibility": (
        "wAHi1MOKSosBLK6HVeeBzfbaQknsYZOOkIB/s3TXYK3NzxiIzJ3HzV6"
        "uQOMlyTSI1GIVz9AazrmLIgl7NAufVofFaQDhnTL9CNyhqVwlaibJmi6mQrr377HrCaTI7VCWxo1kniM"
        "jJDOEz4X29+NH5awd4jH6hPyKIOZhNjWuMrNSKu6ZFLuRSgOiy4c+0idoOSRYiOiX9HK8KkQaHk8EfkR"
        "05vRrjPBkaNVKqg1RcpcfREQ06gIS9YzkItTt+2z/aHHZU1rAdJTyJ8oijsq2Mis23zqp9EWQ52H4oWE"
        "stionkOct9Z8NgybESmrdNsowi3NXNOoVwWoU4ZEwGCbjG8eO+2HnSP1vPKUi6tT7Z39E2eCMAJJDn9d"
        "yenkOuFRcOMmFiMIIIFsTUniyM7EhvSWxWDFvI+4zbx/+TP5pQClZJcLbXinpw1SMk3GVT3S6EG2n/Dy"
        "LQ0/p3+/CJYbr7sVjdeRLABGyCMvaOPy+dvaRH+mszz58EoV35sq9835SPRD17jNym9E=UCa12gEu9VI"
        "PScd9"
    )
}

def _hc_n_data(req):
    try:
        x = "0123456789/:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        req = req.split(".")
        req = {
            "header": json.loads(base64.b64decode(req[0] + "=======").decode()),
            "payload": json.loads(base64.b64decode(req[1] + "=======").decode()),
        }

        def a(r):
            for t in range(len(r) - 1, -1, -1):
                if r[t] < len(x) - 1:
                    r[t] += 1
                    return True
                r[t] = 0
            return False

        def i(r):
            return "".join(x[n] for n in r)

        def o(r, e):
            hashed = hashlib.sha1(e.encode())
            t = hashed.digest()
            o_bits = []
            for n in range(0, 8 * len(t)):
                o_bits.append((t[n // 8] >> (n % 8)) & 1)
            a_bits = o_bits[:r]
            return 0 == a_bits[0] and a_bits[1:].index(1) >= r - 2 if 1 in a_bits else False

        def get():
            for e in range(25):
                n = [0 for _ in range(e)]
                while a(n):
                    u = req["payload"]["d"] + "::" + i(n)
                    if o(req["payload"]["s"], u):
                        return i(n)

        result = get()
        if not result:
            return False
        hsl = ":".join([
            "1", str(req["payload"]["s"]),
            time.strftime("%Y%m%d%H%M%S"),
            req["payload"]["d"], "", result,
        ])
        return hsl
    except Exception:
        return False

def _hc_req_data(host, sitekey):
    try:
        r = httpx.get(
            f"https://hcaptcha.com/checksiteconfig?host={host}&sitekey={sitekey}&sc=1&swa=1",
            headers=_HC_HEADERS, timeout=8,
        )
        j = r.json()
        return j["c"] if j.get("pass") else False
    except Exception:
        return False

def _hc_get_captcha(host, sitekey, n, req):
    try:
        payload = {
            "sitekey": sitekey, "v": "b1129b9", "host": host, "n": n,
            "hl": "en", "c": json.dumps(req),
        }
        data = urllib.parse.urlencode(payload)
        h = dict(_HC_HEADERS)
        h["Content-type"] = "application/x-www-form-urlencoded"
        h["Content-length"] = str(len(data))
        r = httpx.post(
            f"https://hcaptcha.com/getcaptcha?s={sitekey}",
            cookies=_HC_COOKIES, content=data, headers=h, timeout=10,
        )
        return r.json()
    except Exception:
        return {}

def solve_accessibility(sitekey, host="discord.com"):
    try:
        req = _hc_req_data(host, sitekey)
        if not req:
            return None, "checksiteconfig فشل"
        req["type"] = "hsl"
        n = _hc_n_data(req["req"])
        if not n:
            return None, "hash فشل"
        res = _hc_get_captcha(host, sitekey, n, req)
        if isinstance(res, dict) and res.get("generated_pass_UUID"):
            return res["generated_pass_UUID"], None
        return None, "generated_pass_UUID غير موجود"
    except Exception as e:
        return None, f"accessibility: {e}"

# ============================================================
# NoneCap
# ============================================================
def solve_nonecap(sitekey, rqdata="", rqtoken="", url="https://discord.com/login", wait=30):
    if not NONECAP_KEY:
        return None, "NONECAP_API_KEY غير موجود"
    body = {"type": "hcaptcha", "sitekey": sitekey, "url": url}
    if rqdata:
        body["rqdata"] = rqdata
    if rqtoken:
        body["rqtoken"] = rqtoken
    try:
        r = requests.post(
            f"https://api.nonecap.com/v1/solves?wait={wait}",
            headers={"Authorization": f"Bearer {NONECAP_KEY}", "Content-Type": "application/json"},
            json=body, timeout=wait + 30,
        )
    except Exception as e:
        return None, f"NoneCap اتصال: {e}"
    try:
        data = r.json()
    except Exception:
        return None, f"NoneCap HTTP {r.status_code}"
    if r.status_code == 200:
        token = data.get("token")
        if token:
            return token, None
        if data.get("status") == "pending":
            return _poll_nonecap(data.get("id"), wait)
        return None, "NoneCap بدون token"
    if r.status_code == 402:
        return None, "NoneCap رصيد غير كافٍ"
    if r.status_code == 429:
        return None, "NoneCap rate limit"
    if r.status_code == 401:
        return None, "NoneCap مفتاح غير صالح"
    return None, f"NoneCap HTTP {r.status_code}"

def _poll_nonecap(solve_id, wait=30):
    if not solve_id:
        return None, "NoneCap لا يوجد id"
    for _ in range(10):
        time.sleep(5)
        try:
            r = requests.get(
                f"https://api.nonecap.com/v1/solves/{solve_id}?wait={wait}",
                headers={"Authorization": f"Bearer {NONECAP_KEY}"}, timeout=wait + 30,
            )
            d = r.json()
            if d.get("token"):
                return d["token"], None
            if d.get("status") in ("failed", "error"):
                return None, "NoneCap فشل"
        except Exception:
            continue
    return None, "NoneCap timeout"

# ============================================================
# CaptchaHandler
# ============================================================
class MultiCaptchaHandler(discord.CaptchaHandler):
    def solvecaptcha(self, data, proxy=None, proxy_auth=None):
        sitekey = data.get("captcha_sitekey", "")
        rqdata = data.get("captcha_rqdata", "")
        rqtoken = data.get("captcha_rqtoken", "")
        log.info(f"CaptchaHandler: sitekey={sitekey[:12]}")
        # 1) accessibility (مجاني، قد ينجح محلياً)
        token, err = solve_accessibility(sitekey, "discord.com")
        if token:
            return token
        log.warning(f"accessibility فشل: {err}")
        # 2) NoneCap
        token, err = solve_nonecap(sitekey, rqdata, rqtoken)
        if token:
            return token
        raise RuntimeError(f"فشل كل الحلول: {err}")

    async def fetch_token(self, data, proxy=None, proxy_auth=None):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.solvecaptcha, data, proxy, proxy_auth)

# ============================================================
# Flask
# ============================================================
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

# ============================================================
# Telegram
# ============================================================
def _clean(text):
    try:
        return str(text)[:4000]
    except Exception:
        return "?"

async def _send_one(bot, chat_id, text, md):
    try:
        await bot.send_message(
            chat_id=chat_id, text=text[:4000],
            parse_mode=ParseMode.MARKDOWN if md else None,
            disable_web_page_preview=True,
        )
    except TelegramError:
        if md:
            await bot.send_message(chat_id=chat_id, text=text[:4000], disable_web_page_preview=True)
        else:
            raise

def send_tg(chat_id, text, md=False):
    if not chat_id or not state["tg_loop"] or not state["tg_app"]:
        return
    async def _do():
        try:
            full = _clean(text)
            for i in range(0, len(full), 3900):
                chunk = full[i:i+3900]
                try:
                    await _send_one(state["tg_app"].bot, chat_id, chunk, md)
                except Exception as e:
                    log.error(f"tg send: {e}")
        except Exception as e:
            log.error(f"tg wrapper: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["tg_loop"])

def send_tg_doc(chat_id, filename, data, caption=""):
    if not chat_id or not state["tg_loop"] or not state["tg_app"]:
        return
    async def _do():
        try:
            buf = io.BytesIO(data)
            buf.name = filename
            await state["tg_app"].bot.send_document(
                chat_id=chat_id, document=buf, caption=_clean(caption)[:1000],
            )
        except Exception as e:
            log.error(f"tg doc: {e}")
    asyncio.run_coroutine_threadsafe(_do(), state["tg_loop"])

def notify(text):
    chat = state.get("default_chat")
    if chat:
        send_tg(chat, text)

# ============================================================
# Discord login
# ============================================================
def _random_ua():
    chrome = random.choice(["131.0.0.0", "132.0.0.0", "133.0.0.0"])
    os_part = random.choice([
        "Windows NT 10.0; Win64; x64",
        "Macintosh; Intel Mac OS X 10_15_7",
        "X11; Linux x86_64",
    ])
    return f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36"

def _super_properties():
    sp = {
        "os": random.choice(["Windows", "Mac OS X", "Linux"]),
        "browser": "Chrome", "device": "", "system_locale": "en-US",
        "browser_user_agent": _random_ua(), "browser_version": "131.0.0.0",
        "os_version": "10", "referrer": "", "referring_domain": "",
        "referrer_current": "", "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": random.randint(300000, 350000),
        "client_event_source": None,
    }
    raw = json.dumps(sp, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()

def _login_headers():
    return {
        "Content-Type": "application/json",
        "User-Agent": _random_ua(),
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/login",
        "X-Discord-Locale": "en-US",
        "X-Debug-Options": "bugReporterEnabled",
        "X-Super-Properties": _super_properties(),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

def _proxy_dict():
    if not PROXY_URL:
        return None
    return {"http": PROXY_URL, "https": PROXY_URL}

def api_login(email, password, twofa="", max_retries=2):
    payload = {
        "login": email, "password": password, "undelete": False,
        "captcha_key": None, "login_source": None, "gift_code_sku_id": None,
    }
    last_err = "فشل غير معروف"
    for attempt in range(1, max_retries + 1):
        headers = _login_headers()
        proxies = _proxy_dict()
        try:
            r = requests.post(
                f"{API_BASE}/auth/login",
                data=json.dumps(payload), headers=headers,
                proxies=proxies, timeout=30,
            )
        except Exception as e:
            last_err = f"اتصال: {e}"
            time.sleep(3)
            continue
        try:
            data = r.json()
        except Exception:
            data = {}
        log.info(f"login HTTP {r.status_code} keys={list(data.keys())}")
        if "token" in data:
            return data["token"], None
        if data.get("mfa") or data.get("ticket"):
            if not twofa:
                return None, "2FA مطلوب — حط DISCORD_2FA"
            try:
                r2 = requests.post(
                    f"{API_BASE}/auth/mfa/totp",
                    data=json.dumps({"code": twofa, "ticket": data["ticket"],
                                     "login_source": None, "gift_code_sku_id": None}),
                    headers=headers, proxies=proxies, timeout=30,
                )
                d2 = r2.json()
                if "token" in d2:
                    return d2["token"], None
                return None, "2FA مرفوض"
            except Exception as e:
                return None, f"2FA: {e}"
        if "captcha_sitekey" in data:
            sitekey = data.get("captcha_sitekey", "")
            rqdata = data.get("captcha_rqdata", "")
            rqtoken = data.get("captcha_rqtoken", "")
            log.warning(f"CAPTCHA — محاولة الحلول")
            # 1) accessibility
            solved, err1 = solve_accessibility(sitekey, "discord.com")
            if not solved:
                log.warning(f"accessibility فشل: {err1} — NoneCap")
                solved, err2 = solve_nonecap(sitekey, rqdata, rqtoken)
                if not solved:
                    return None, (
                        f"فشل حل CAPTCHA.\n"
                        f"السبب: {err2}\n\n"
                        "⚠️ ملاحظة: Discord يرفض توكنات hCaptcha من IPs مراكز البيانات (Render).\n"
                        "الحل: شغّل الأداة على جهازك (IP منزلي) أو استخدم بروكسي سكني عبر PROXY_URL."
                    )
            headers2 = dict(headers)
            headers2["X-Captcha-Key"] = solved
            if rqtoken:
                headers2["X-Captcha-Rqtoken"] = rqtoken
            if data.get("captcha_session_id"):
                headers2["X-Captcha-Session-Id"] = data["captcha_session_id"]
            try:
                r3 = requests.post(
                    f"{API_BASE}/auth/login",
                    data=json.dumps(payload), headers=headers2,
                    proxies=proxies, timeout=30,
                )
                d3 = r3.json()
                if "token" in d3:
                    return d3["token"], None
                return None, f"CAPTCHA مرفوض: {d3.get('captcha_key', ['?'])}"
            except Exception as e:
                return None, f"retry: {e}"
        if r.status_code in (400, 401):
            msg = data.get("message", "") or ""
            if "invalid" in msg.lower() or "credentials" in msg.lower():
                return None, "الإيميل أو كلمة المرور غير صحيحة"
            last_err = f"HTTP {r.status_code}: {msg}"
            if attempt < max_retries:
                time.sleep(5)
                continue
            return None, last_err
        if r.status_code == 429:
            last_err = "Rate limit — انتظر"
            time.sleep(20)
            continue
        last_err = f"HTTP {r.status_code}"
        time.sleep(5)
    return None, last_err

def detect_token_type(token):
    for prefix, kind in [("", "user"), ("Bot ", "bot")]:
        try:
            r = requests.get(
                f"{API_BASE}/users/@me",
                headers={"Authorization": f"{prefix}{token}", "User-Agent": _random_ua()},
                proxies=_proxy_dict(), timeout=10,
            )
            if r.status_code == 200:
                return kind
        except Exception:
            pass
    return None

# ============================================================
# Discord worker
# ============================================================
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
        captcha_handler=MultiCaptchaHandler() if token_type == "user" else None,
    )
    state["discord_bot"] = bot

    @bot.event
    async def on_ready():
        state["discord_ready"] = True
        log.info(f"✅ Discord: {bot.user} ({token_type})")
        notify(f"✅ Discord جاهز\nالحساب: {bot.user}\nأرسل /private <guild_id>")

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

# ============================================================
# Format
# ============================================================
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

# ============================================================
# Hunter
# ============================================================
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

# ============================================================
# Scan
# ============================================================
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
        send_tg(chat_id, f"🔍 بدء: {guild.name}\nالوضع: {mode}\nرومات: {len(channels)}")
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

# ============================================================
# Telegram handlers
# ============================================================
HELP = (
    "الأوامر:\n\n"
    "/private <guild_id> — الرومات الخاصة فقط\n"
    "/scan <guild_id> — كل الرومات\n"
    "/channels <guild_id> — تصنيف الرومات\n"
    "/status — الحالة\n"
    "/retry_login — إعادة محاولة الدخول\n\n"
    "لو حطيت GUILD_ID في env، تقدر تحذف الآيدي."
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state["default_chat"] = update.effective_chat.id
        ready = state["discord_ready"]
        lines = [f"👋 أهلاً", f"Discord: {'✅ جاهز' if ready else '⏳ يتصل...'}"]
        if state["last_error"]:
            lines.append(f"⚠️ آخر خطأ: {state['last_error']}")
        lines.append("")
        lines.append(HELP)
        await update.message.reply_text("\n".join(lines)[:4000], disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_start: {e}")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(HELP[:4000], disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_help: {e}")

async def cmd_retry_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if state["discord_ready"]:
            await update.message.reply_text("✅ Discord متصل أصلاً")
            return
        if not DC_EMAIL or not DC_PASSWORD:
            await update.message.reply_text("❌ DISCORD_EMAIL أو DISCORD_PASSWORD غير موجود")
            return
        await update.message.reply_text("⏳ إعادة محاولة الدخول...")
        token, err = await asyncio.to_thread(api_login, DC_EMAIL, DC_PASSWORD, DC_2FA)
        if err:
            state["last_error"] = err
            await update.message.reply_text(f"❌ {err}"[:4000])
            return
        state["discord_token"] = token
        if start_discord():
            await update.message.reply_text("✅ نجح، جاري الاتصال...")
        else:
            await update.message.reply_text("❌ التوكن غير صالح")
    except Exception as e:
        log.error(f"cmd_retry_login: {e}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bot = state["discord_bot"]
        ready = bot and state["discord_ready"]
        lines = [
            f"Discord: {'✅' if ready else '❌'}",
            f"النوع: {state['token_type'] or '—'}",
            f"الحساب: {bot.user if ready else '—'}",
            f"فحص جاري: {'نعم' if state['scanning'] else 'لا'}",
            f"NoneCap: {'✅' if NONECAP_KEY else '❌'}",
            f"بروكسي: {'✅' if PROXY_URL else '❌'}",
            f"بيئة: {'Render (IP مركز بيانات)' if state['is_render'] else 'محلي (IP منزلي)'}",
        ]
        if state["last_error"]:
            lines.append(f"آخر خطأ: {state['last_error']}")
        await update.message.reply_text("\n".join(lines)[:4000], disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_status: {e}")

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
    return None, "أرسل الأمر مع آيدي السيرفر"

async def cmd_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state["default_chat"] = update.effective_chat.id
        if state["scanning"]:
            await update.message.reply_text("⏳ فيه فحص جاري")
            return
        if not state["discord_ready"]:
            await update.message.reply_text("❌ Discord ما اتصل بعد. /status")
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
    except Exception as e:
        log.error(f"cmd_private: {e}")

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state["default_chat"] = update.effective_chat.id
        if state["scanning"]:
            await update.message.reply_text("⏳ فيه فحص جاري")
            return
        if not state["discord_ready"]:
            await update.message.reply_text("❌ Discord ما اتصل بعد. /status")
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
    except Exception as e:
        log.error(f"cmd_scan: {e}")

async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state["default_chat"] = update.effective_chat.id
        if not state["discord_ready"]:
            await update.message.reply_text("❌ Discord ما اتصل بعد. /status")
            return
        gid, err = await _resolve_guild(context)
        if err:
            await update.message.reply_text(err)
            return

        async def _list():
            try:
                bot = state["discord_bot"]
                guild = bot.get_guild(gid)
                if guild is None:
                    guild = await bot.fetch_guild(gid)
                private = [c for c in guild.text_channels if is_private_channel(c, guild)]
                public = [c for c in guild.text_channels if not is_private_channel(c, guild)]
                lines = [f"🏰 {guild.name}", f"🔒 خاصة: {len(private)} | 🌐 عامة: {len(public)}", ""]
                if private:
                    lines.append("🔒 الخاصة:")
                    for c in private[:30]:
                        lines.append(f"• #{c.name} ({c.id})")
                    lines.append("")
                if public:
                    lines.append("🌐 العامة:")
                    for c in public[:20]:
                        lines.append(f"• #{c.name}")
                send_tg(update.effective_chat.id, "\n".join(lines))
            except Exception as e:
                log.error(f"list: {e}")

        asyncio.run_coroutine_threadsafe(_list(), state["discord_loop"])
        await update.message.reply_text("📋 جاري الجلب...")
    except Exception as e:
        log.error(f"cmd_channels: {e}")

async def on_error(update, context):
    log.error(f"tg handler: {context.error}")
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ خطأ: {context.error}"[:4000],
            )
    except Exception:
        pass

# ============================================================
# post_init
# ============================================================
async def post_init(app: Application):
    try:
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
                state["last_error"] = None
                if start_discord():
                    log.info("✅ auto login نجح")
            else:
                state["last_error"] = err
                log.error(f"auto login: {err}")
                notify(f"❌ فشل تسجيل دخول Discord: {err}")
        else:
            notify("⚠️ ما فيه DISCORD_EMAIL أو DISCORD_PASSWORD")
    except Exception as e:
        log.error(f"post_init: {e}")

# ============================================================
def main():
    if not TG_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN غير موجود")
        sys.exit(1)

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()

    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("retry_login", cmd_retry_login))
    app.add_handler(CommandHandler("private", cmd_private))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_error_handler(on_error)

    log.info("🤖 Telegram polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()