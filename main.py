import os
import sys
import types
import time
import asyncio
import logging
import threading
import secrets
import html as html_lib
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
from flask import Flask, request, redirect

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scan")

API_BASE = "https://discord.com/api/v9"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
)

ENV_GUILD_ID = os.getenv("GUILD_ID", "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

state = {
    "token": None,
    "bot_started": False,
    "loop": None,
    "bot": None,
    "user": None,
    "scan_status": "idle",
    "progress": "",
    "guild": None,
    "messages": [],
    "error": None,
}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True


# =========================
# تسجيل الدخول
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
            return None, "الحساب يحتاج رمز 2FA"
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
            return None, f"MFA مرفوض: {d2.get('message', d2)}"
        except Exception as e:
            return None, f"MFA: {e}"

    if "captcha_key" in data:
        return None, "Discord طلب CAPTCHA. جرب بعد فترة أو من شبكة ثانية."
    return None, f"فشل: {data.get('message', data)}"


# =========================
# البوت
# =========================
def _run_bot(token):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state["loop"] = loop

    bot = commands.Bot(command_prefix="!x", self_bot=True, intents=intents, help_command=None)
    state["bot"] = bot

    @bot.event
    async def on_ready():
        state["user"] = str(bot.user)
        log.info(f"✅ {bot.user}")
        if ENV_GUILD_ID and state["scan_status"] == "idle":
            try:
                gid = int(ENV_GUILD_ID)
                state["scan_status"] = "scanning"
                asyncio.create_task(do_scan(gid))
            except ValueError:
                log.error("GUILD_ID env غير صالح")

    try:
        loop.run_until_complete(bot.start(token))
    except Exception as e:
        state["error"] = str(e)
        log.error(f"bot: {e}")


def start_bot(token):
    if state["bot_started"]:
        return
    state["bot_started"] = True
    threading.Thread(target=_run_bot, args=(token,), daemon=True).start()


# =========================
# تنسيق
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
# الالتقاط
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


async def do_scan(gid):
    try:
        state["scan_status"] = "scanning"
        state["messages"] = []
        state["error"] = None
        bot = state["bot"]
        guild = bot.get_guild(gid)
        if guild is None:
            guild = await bot.fetch_guild(gid)
        state["guild"] = f"{guild.name} ({guild.id})"

        h = Hunter()
        total = len(guild.text_channels)
        for i, ch in enumerate(guild.text_channels, 1):
            state["progress"] = f"({i}/{total}) #{ch.name}"
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
                state["messages"] = sorted(h.captured.values(), key=lambda m: m.created_at)
                await asyncio.sleep(0.4)
            except Exception as e:
                log.error(f"#{ch.name}: {e}")

        state["progress"] = (
            f"تم — {len(h.read)} مقروء | {len(h.blocked)} محجوب | {len(h.captured)} رسالة"
        )
        state["scan_status"] = "done"
    except Exception as e:
        state["error"] = str(e)
        state["scan_status"] = "error"
        log.error(f"scan: {e}")


# =========================
# HTML
# =========================
LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>دخول</title>
<style>
body{background:#1a1a2e;color:#e0e0e0;font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
.box{background:#16213e;padding:40px;border-radius:12px;width:360px;box-shadow:0 10px 40px rgba(0,0,0,.5)}
h1{margin:0 0 20px;color:#a78bfa;font-size:22px}
label{display:block;margin:12px 0 4px;font-size:13px;color:#9ca3af}
input{width:100%;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e0e0e0;box-sizing:border-box;font-size:14px}
button{width:100%;margin-top:20px;padding:12px;background:#7c3aed;border:none;border-radius:6px;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#6d28d9}
.err{background:#7f1d1d;padding:10px;border-radius:6px;margin-bottom:10px;font-size:13px}
</style></head><body><div class="box">
<h1>🔐 تسجيل الدخول</h1>
{{error}}
<form method="post">
<label>البريد / الهاتف</label><input name="email" required autofocus>
<label>كلمة المرور</label><input name="password" type="password" required>
<label>رمز 2FA (اختياري)</label><input name="twofa">
<button type="submit">دخول</button>
</form></div></body></html>"""

PANEL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>لوحة</title>
<style>
body{background:#1a1a2e;color:#e0e0e0;font-family:system-ui,sans-serif;margin:0;padding:40px}
.box{background:#16213e;padding:40px;border-radius:12px;max-width:600px;margin:0 auto}
h1{margin:0 0 20px;color:#a78bfa;font-size:22px}
input{width:100%;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e0e0e0;box-sizing:border-box;margin-top:6px}
button{margin-top:20px;padding:12px 24px;background:#7c3aed;border:none;border-radius:6px;color:#fff;font-weight:600;cursor:pointer}
a{color:#a78bfa;margin-left:15px;text-decoration:none}
p{color:#9ca3af;line-height:1.6}
</style></head><body><div class="box">
<h1>🎯 فحص السيرفر</h1>
<p>متصل: <strong>{{user}}</strong></p>
<form method="post">
<label>آيدي السيرفر</label>
<input name="guild_id" value="{{gid}}" required placeholder="1234567890">
<button type="submit">ابدأ الفحص</button>
<a href="/scan">النتائج ←</a>
</form></div></body></html>"""

SCAN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>نتائج</title>
<meta http-equiv="refresh" content="5">
<style>
body{background:#1a1a2e;color:#e0e0e0;font-family:ui-monospace,monospace;margin:0;padding:20px;font-size:13px}
h1{color:#a78bfa;font-family:system-ui,sans-serif}
.status{background:#16213e;padding:15px;border-radius:8px;margin-bottom:20px;font-family:system-ui,sans-serif}
.msg{background:#0f172a;padding:10px;border-radius:6px;margin-bottom:8px;white-space:pre-wrap;word-break:break-word;border-left:3px solid #7c3aed}
a{color:#a78bfa}
.err{color:#f87171}
</style></head><body>
<h1>📊 النتائج</h1>
<div class="status">
<p><strong>الحالة:</strong> {{status}}</p>
<p><strong>السيرفر:</strong> {{guild}}</p>
<p><strong>التقدم:</strong> {{progress}}</p>
{{error}}
<p><strong>عدد الرسائل:</strong> {{count}}</p>
<a href="/panel">← رجوع</a> | <a href="/scan">تحديث</a>
</div>
{{messages}}
</body></html>"""


# =========================
# المسارات
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    if state["token"]:
        return redirect("/panel")
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        twofa = request.form.get("twofa", "").strip()
        if not email or not password:
            error = '<div class="err">لازم بريد وكلمة مرور</div>'
        else:
            token, err = api_login(email, password, twofa)
            if err:
                error = f'<div class="err">{html_lib.escape(err)}</div>'
            else:
                state["token"] = token
                start_bot(token)
                return redirect("/panel")
    return LOGIN_HTML.replace("{{error}}", error)


@app.route("/panel", methods=["GET", "POST"])
def panel():
    if not state["token"]:
        return redirect("/")
    if request.method == "POST":
        try:
            gid = int(request.form.get("guild_id", "").strip())
        except ValueError:
            return "آيدي غير صالح", 400
        state["scan_status"] = "scanning"
        state["progress"] = "بدء..."
        loop = state["loop"]
        if loop and state["bot"]:
            asyncio.run_coroutine_threadsafe(do_scan(gid), loop)
        return redirect("/scan")
    user = state["user"] or "جاري الاتصال..."
    html = PANEL_HTML.replace("{{user}}", html_lib.escape(user)).replace("{{gid}}", ENV_GUILD_ID)
    return html


@app.route("/scan")
def scan_view():
    if not state["token"]:
        return redirect("/")
    msgs = state["messages"]
    body = "".join(
        f'<div class="msg">{html_lib.escape(fmt_message(m))}</div>' for m in msgs
    )
    err = f'<p class="err">{html_lib.escape(state["error"])}</p>' if state["error"] else ""
    html = (
        SCAN_HTML
        .replace("{{status}}", state["scan_status"])
        .replace("{{guild}}", state["guild"] or "—")
        .replace("{{progress}}", state["progress"] or "—")
        .replace("{{error}}", err)
        .replace("{{count}}", str(len(msgs)))
        .replace("{{messages}}", body)
    )
    return html


@app.route("/ping")
def ping():
    return "alive", 200


def self_ping_loop():
    if not RENDER_URL:
        return
    while True:
        try:
            requests.get(f"{RENDER_URL}/ping", timeout=10)
        except Exception:
            pass
        time.sleep(14 * 60)


def main():
    threading.Thread(target=self_ping_loop, daemon=True).start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()