import os
import re
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
from flask import Flask, request, redirect, render_template_string
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
DEFAULT_GUILD = os.getenv("GUILD_ID", "").strip()
RENDER_URL    = os.getenv("RENDER_EXTERNAL_URL", "").strip()
NOPECHA_KEY   = os.getenv("NOPECHA_KEY", "").strip()
PORT          = int(os.getenv("PORT", "8080"))

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
    "login_attempts": 0,
}

# ============================================================
# التحقق من متغيرات البيئة (قبل أي شيء)
# ============================================================
def _mask(val, head=6, tail=4):
    """يقنّع القيمة مع إبقاء بعض المعالم للتشخيص."""
    if not val:
        return "(فارغ)"
    if len(val) <= head + tail + 3:
        return val[:2] + "***" + val[-1:]
    return val[:head] + "..." + val[-tail:]

def _has_suspicious(val):
    """يكشف الرموز الغريبة اللي غالباً تكون من لصق خاطئ."""
    bad_chars = {
        ">": "علامة أكبر من (غالباً من redirect أو لصق خاطئ)",
        "<": "علامة أصغر من",
        '"': "علامة اقتباس مزدوجة",
        "'": "علامة اقتباس أحادية",
        " ": "مسافة",
        "\n": "سطر جديد",
        "\t": "تاب",
    }
    issues = []
    for c, desc in bad_chars.items():
        if c in val:
            issues.append(desc)
    return issues

def _show_env_status():
    """يطبع حالة كل المتغيرات (مقنّعة) قبل البدء."""
    log.info("=" * 60)
    log.info("📋 فحص المتغيرات:")
    log.info(f"  TELEGRAM_BOT_TOKEN: {_mask(TG_TOKEN, 12, 6)}")
    log.info(f"  TELEGRAM_CHAT_ID:   {TG_CHAT_ID or '(فارغ)'}")
    log.info(f"  DISCORD_TOKEN:      {_mask(DC_TOKEN, 6, 4) if DC_TOKEN else '(فارغ)'}")
    log.info(f"  DISCORD_EMAIL:      {_mask(DC_EMAIL, 3, 8) if DC_EMAIL else '(فارغ)'}")
    log.info(f"  DISCORD_PASSWORD:   {'(موجود)' if DC_PASSWORD else '(فارغ)'}")
    log.info(f"  DISCORD_2FA:        {'(موجود)' if DC_2FA else '(فارغ)'}")
    log.info(f"  GUILD_ID:           {DEFAULT_GUILD or '(فارغ)'}")
    log.info(f"  NOPECHA_KEY:        {'(موجود)' if NOPECHA_KEY else '(فارغ)'}")
    log.info(f"  PORT:               {PORT}")
    log.info("=" * 60)

def _validate_tg_token(token):
    """يتحقق من صيغة توكن تيليجرام."""
    if not token:
        return False, "TELEGRAM_BOT_TOKEN فارغ تماماً"
    issues = _has_suspicious(token)
    if issues:
        return False, f"فيه رموز غريبة: {'، '.join(issues)}"
    if not re.match(r"^\d+:[A-Za-z0-9_-]+$", token):
        parts = token.split(":", 1)
        if len(parts) != 2:
            return False, "صيغة غير صحيحة — ما فيه ':' بين الرقم والسر"
        if not parts[0].isdigit():
            return False, "الجزء قبل ':' لازم يكون أرقام (معرف البوت)"
        if len(parts[1]) < 30:
            return False, f"الجزء بعد ':' قصير جداً ({len(parts[1])} حرف، المتوقع 35+) — التوكن مقطوع"
    return True, None

# ============================================================
# Accessibility Cookie (مجاني)
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
            o_bits = [(t[n // 8] >> (n % 8)) & 1 for n in range(0, 8 * len(t))]
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
        return ":".join([
            "1", str(req["payload"]["s"]),
            time.strftime("%Y%m%d%H%M%S"),
            req["payload"]["d"], "", result,
        ])
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

def solve_nopecha(sitekey, rqdata="", url="https://discord.com/login"):
    if not NOPECHA_KEY:
        return None, "NOPECHA_KEY غير موجود"
    try:
        r = requests.post(
            "https://api.nopecha.com/token",
            json={"type": "hcaptcha", "sitekey": sitekey, "url": url, "data": rqdata or None},
            headers={"Authorization": f"Bearer {NOPECHA_KEY}"},
            timeout=120,
        )
        data = r.json()
        if data.get("data"):
            return data["data"], None
        return None, f"NopeCHA: {data.get('message', data)}"
    except Exception as e:
        return None, f"NopeCHA: {e}"

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

def api_login(email, password, twofa="", max_retries=3):
    payload = {
        "login": email, "password": password, "undelete": False,
        "captcha_key": None, "login_source": None, "gift_code_sku_id": None,
    }
    last_err = "فشل غير معروف"
    for attempt in range(1, max_retries + 1):
        headers = _login_headers()
        try:
            r = requests.post(f"{API_BASE}/auth/login", data=json.dumps(payload), headers=headers, timeout=30)
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
                return None, "2FA مطلوب — أضف DISCORD_2FA"
            try:
                r2 = requests.post(
                    f"{API_BASE}/auth/mfa/totp",
                    data=json.dumps({"code": twofa, "ticket": data["ticket"],
                                     "login_source": None, "gift_code_sku_id": None}),
                    headers=headers, timeout=30,
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
            log.warning("CAPTCHA — محاولة الحلول")
            solved, err1 = solve_accessibility(sitekey, "discord.com")
            if not solved:
                log.warning(f"accessibility فشل: {err1} — NopeCHA")
                solved, err2 = solve_nopecha(sitekey, rqdata)
                if not solved:
                    return None, (
                        f"فشل حل CAPTCHA.\n"
                        f"accessibility: {err1}\n"
                        f"NopeCHA: {err2}\n\n"
                        "⚠️ الأفضل استخدام IP سكني (جهازك/جوالك) أو بروكسي سكني."
                    )
            headers2 = dict(headers)
            headers2["X-Captcha-Key"] = solved
            if rqtoken:
                headers2["X-Captcha-Rqtoken"] = rqtoken
            if data.get("captcha_session_id"):
                headers2["X-Captcha-Session-Id"] = data["captcha_session_id"]
            try:
                r3 = requests.post(f"{API_BASE}/auth/login", data=json.dumps(payload), headers=headers2, timeout=30)
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
                timeout=10,
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

def start_discord(token=None):
    if state["discord_bot"]:
        return True
    if token:
        state["discord_token"] = token
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
# Flask
# ============================================================
flask_app = Flask(__name__)

LOGIN_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>دخول Discord</title>
<style>*{box-sizing:border-box}body{background:#0f1115;color:#e5e7eb;font-family:system-ui;margin:0;padding:24px;min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#161a23;padding:28px;border-radius:14px;width:100%;max-width:400px;box-shadow:0 10px 40px rgba(0,0,0,.6)}
h1{font-size:20px;margin:0 0 8px;color:#7c9eff}p.sub{font-size:13px;color:#9ca3af;margin:0 0 20px}
label{font-size:13px;color:#9ca3af;display:block;margin:14px 0 6px}
input{width:100%;padding:11px;border-radius:8px;border:1px solid #2b3242;background:#0b0e14;color:#e5e7eb;font-size:14px}
button{width:100%;padding:12px;border:0;border-radius:8px;background:#5865f2;color:#fff;font-weight:600;font-size:15px;margin-top:20px;cursor:pointer}
button:hover{background:#4752c4}.status{margin-top:16px;padding:12px;border-radius:8px;font-size:13px;line-height:1.5}
.ok{background:#14361f;color:#4ade80;border:1px solid #166534}.err{background:#3b1219;color:#f87171;border:1px solid #7f1d1d}</style>
</head><body><div class="box"><h1>🔐 تسجيل دخول Discord</h1>
<p class="sub">⚠️ هذه الصفحة تعمل فقط إذا كنت تشغل البوت محلياً أو عبر tunnel. على لوحات الاستضافة (Pterodactyl) لا تقدر توصلها — استخدم متغيرات البيئة.</p>
<form method="post" action="/login">
<label>الإيميل / الهاتف</label><input name="email" required>
<label>كلمة المرور</label><input name="password" type="password" required>
<label>رمز 2FA (اختياري)</label><input name="twofa" inputmode="numeric">
<button type="submit">دخول</button></form>{{status}}</div></body></html>"""

STATUS_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>الحالة</title>
<style>body{background:#0f1115;color:#e5e7eb;font-family:system-ui;padding:24px;max-width:500px;margin:auto}
.box{background:#161a23;padding:24px;border-radius:14px}h1{color:#7c9eff;font-size:20px;margin:0 0 16px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2b3242;font-size:14px}
.row:last-child{border:0}a{color:#7c9eff;text-decoration:none;display:inline-block;margin-top:16px}</style></head><body>
<div class="box"><h1>📊 حالة البوت</h1>
<div class="row"><span>Discord</span><span>{{ready}}</span></div>
<div class="row"><span>النوع</span><span>{{kind}}</span></div>
<div class="row"><span>الحساب</span><span>{{user}}</span></div>
<div class="row"><span>آخر خطأ</span><span>{{error}}</span></div>
<div class="row"><span>Telegram</span><span>{{tg}}</span></div>
<a href="/">← رجوع</a></div></body></html>"""

def _render_login(status=""):
    return render_template_string(LOGIN_HTML, status=status)

@flask_app.route("/", methods=["GET"])
def index():
    if state["discord_ready"]:
        return redirect("/status")
    return _render_login()

@flask_app.route("/status")
def status_page():
    return render_template_string(
        STATUS_HTML,
        ready="✅ متصل" if state["discord_ready"] else "❌ غير متصل",
        kind=state["token_type"] or "—",
        user=(state["discord_bot"].user if state["discord_bot"] and state["discord_ready"] else "—"),
        error=state["last_error"] or "—",
        tg="✅ متصل" if state["tg_app"] else "❌ فشل",
    )

@flask_app.route("/login", methods=["POST"])
def login_post():
    if state["discord_ready"]:
        return redirect("/status")
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    twofa = request.form.get("twofa", "").strip()
    if not email or not password:
        return _render_login('<div class="status err">لازم الإيميل وكلمة المرور</div>')
    state["login_attempts"] += 1
    log.info(f"محاولة دخول #{state['login_attempts']}")
    token, err = api_login(email, password, twofa)
    if err:
        state["last_error"] = err
        return _render_login(f'<div class="status err">❌ {err}</div>')
    state["last_error"] = None
    if start_discord(token=token):
        return _render_login('<div class="status ok">✅ تم الدخول! جاري الاتصال...<br><a href="/status" style="color:#4ade80">اذهب للحالة</a></div>')
    return _render_login('<div class="status err">❌ التوكن غير صالح</div>')

@flask_app.route("/ping")
def ping():
    return "alive", 200

def run_flask():
    try:
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        log.error(f"Flask: {e}")

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
# Telegram helpers
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
# Format & Hunter
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
    "ملاحظة: على لوحات الاستضافة، Discord يجب أن يكون في env (DISCORD_EMAIL+DISCORD_PASSWORD أو DISCORD_TOKEN)."
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state["default_chat"] = update.effective_chat.id
        ready = state["discord_ready"]
        lines = [f"👋 أهلاً", f"Discord: {'✅ جاهز' if ready else '❌ غير متصل'}"]
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
            await update.message.reply_text(
                "❌ DISCORD_EMAIL أو DISCORD_PASSWORD غير موجود في متغيرات البيئة.\n"
                "على لوحات الاستضافة، أضفهما من لوحة التحكم ثم أعد التشغيل."
            )
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
            f"NopeCHA: {'✅' if NOPECHA_KEY else '❌'}",
            f"DC_EMAIL: {'✅' if DC_EMAIL else '❌'}",
            f"DC_PASSWORD: {'✅' if DC_PASSWORD else '❌'}",
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
                log.info("⏳ Discord auto-start (DISCORD_TOKEN)")
        elif DC_EMAIL and DC_PASSWORD:
            log.info("🔐 محاولة دخول Discord تلقائياً...")
            token, err = await asyncio.to_thread(api_login, DC_EMAIL, DC_PASSWORD, DC_2FA)
            if token:
                state["discord_token"] = token
                state["last_error"] = None
                if start_discord():
                    log.info("✅ auto login نجح")
            else:
                state["last_error"] = err
                log.error(f"auto login: {err}")
                notify(f"❌ فشل تسجيل دخول Discord:\n{err}\n\nاستخدم /retry_login للمحاولة مرة أخرى.")
        else:
            log.warning("⚠️ لا DISCORD_TOKEN ولا DISCORD_EMAIL/PASSWORD — أضفهما في env")
            notify(
                "⚠️ لا يوجد Discord credentials في المتغيرات.\n"
                "أضف أحد التالي في لوحة الاستضافة ثم أعد التشغيل:\n"
                "• DISCORD_TOKEN (توكن حساب)\n"
                "• أو DISCORD_EMAIL + DISCORD_PASSWORD"
            )
    except Exception as e:
        log.error(f"post_init: {e}")

# ============================================================
def main():
    _show_env_status()

    ok, err = _validate_tg_token(TG_TOKEN)
    if not ok:
        log.error("=" * 60)
        log.error("❌ TELEGRAM_BOT_TOKEN غير صالح!")
        log.error(f"   السبب: {err}")
        log.error(f"   القيمة (مقنّعة): {_mask(TG_TOKEN, 12, 6)}")
        log.error(f"   الطول: {len(TG_TOKEN)} حرف")
        log.error("")
        log.error("   🔧 الحل:")
        log.error("   1. افتح لوحة الاستضافة → Environment Variables")
        log.error("   2. ابحث عن TELEGRAM_BOT_TOKEN")
        log.error("   3. امسح القيمة الحالية")
        log.error("   4. افتح @BotFather → /mybots → اختر بوتك → API Token")
        log.error("   5. انسخ التوكن كاملاً والصقه (بدون > < \" ' ولا مسافات)")
        log.error("   6. أعد التشغيل")
        log.error("")
        log.error("   ⚠️ الصيغة الصحيحة: 1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        log.error("=" * 60)
        sys.exit(1)

    log.info(f"✅ TELEGRAM_BOT_TOKEN صالح (طول={len(TG_TOKEN)})")

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

    log.info(f"🤖 Telegram polling بدأ — Flask على منفذ {PORT}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()