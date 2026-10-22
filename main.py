# main.py
# Python 3.11+
# pip install aiogram==3.13.1 aiohttp==3.9.5 aiofiles==23.2.1 python-dotenv==1.0.1

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is required in .env")

# Optional: comma-separated numeric IDs allowed to run admin-only actions.
ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Provider API endpoints. Populate with the endpoints you contract with.
PROVIDERS: dict[str, dict[str, Any]] = {
    "tiktok": {
        "url": os.getenv("PROVIDER_TIKTOK_URL", ""),
        "key": os.getenv("PROVIDER_TIKTOK_KEY", ""),
        "unit": "views",
        "min": 100,
        "max": 100_000,
        "step": 100,
        "eta_seconds": 90,
    },
    "telegram": {
        "url": os.getenv("PROVIDER_TELEGRAM_URL", ""),
        "key": os.getenv("PROVIDER_TELEGRAM_KEY", ""),
        "unit": "members",
        "min": 100,
        "max": 50_000,
        "step": 100,
        "eta_seconds": 120,
    },
    "instagram": {
        "url": os.getenv("PROVIDER_INSTAGRAM_URL", ""),
        "key": os.getenv("PROVIDER_INSTAGRAM_KEY", ""),
        "unit": "followers",
        "min": 100,
        "max": 100_000,
        "step": 100,
        "eta_seconds": 180,
    },
    "discord": {
        "url": os.getenv("PROVIDER_DISCORD_URL", ""),
        "key": os.getenv("PROVIDER_DISCORD_KEY", ""),
        "unit": "members (fake)",
        "min": 50,
        "max": 20_000,
        "step": 50,
        "eta_seconds": 240,
    },
}

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=10)
USER_AGENT = "RashqBot/1.0 (+telegram)"
RATE_LIMIT_PER_USER_SECONDS = 30

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("rashq")

# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

TIKTOK_RE = re.compile(r"^@?[A-Za-z0-9._]{2,24}$")
TELEGRAM_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")
INSTAGRAM_RE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")
DISCORD_INVITE_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite)/([A-Za-z0-9-]{2,32})$"
)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(slots=True)
class Order:
    service: str
    target: str
    amount: int
    created_at: float = field(default_factory=time.time)
    request_id: str = ""


def validate_target(service: str, raw: str) -> str | None:
    """Return canonical target or None if invalid."""
    raw = raw.strip()
    if not raw:
        return None
    if service == "tiktok":
        if URL_RE.match(raw):
            m = re.search(r"@([A-Za-z0-9._]{2,24})", raw)
            if not m:
                return None
            raw = "@" + m.group(1)
        if not TIKTOK_RE.match(raw):
            return None
        return raw.lstrip("@")
    if service == "telegram":
        if URL_RE.match(raw):
            m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,32})", raw)
            if not m:
                return None
            raw = "@" + m.group(1)
        if not TELEGRAM_RE.match(raw):
            return None
        return raw.lstrip("@")
    if service == "instagram":
        if URL_RE.match(raw):
            m = re.search(r"instagram\.com/([A-Za-z0-9._]{1,30})", raw)
            if not m:
                return None
            raw = "@" + m.group(1)
        if not INSTAGRAM_RE.match(raw):
            return None
        return raw.lstrip("@")
    if service == "discord":
        m = DISCORD_INVITE_RE.match(raw)
        if not m:
            return None
        return m.group(1)
    return None


def validate_amount(service: str, raw: str) -> int | None:
    cfg = PROVIDERS[service]
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < cfg["min"] or n > cfg["max"]:
        return None
    if n % cfg["step"] != 0:
        return None
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Provider client
# ─────────────────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    pass


class ProviderClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def place_order(self, order: Order) -> str:
        cfg = PROVIDERS[order.service]
        url = cfg["url"]
        key = cfg["key"]
        if not url or not key:
            # Provider not configured — return a synthetic receipt so the bot
            # stays usable during integration. Real traffic requires config.
            rid = "SIM-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
            log.warning("provider %s not configured, issuing synthetic receipt %s", order.service, rid)
            await asyncio.sleep(0.4)
            return rid

        payload = {
            "key": key,
            "action": "add",
            "service": order.service,
            "link": order.target,
            "quantity": order.amount,
        }
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            async with self._session.post(url, data=payload, headers=headers, timeout=HTTP_TIMEOUT) as r:
                body = await r.text()
                if r.status >= 500:
                    raise ProviderError(f"provider 5xx: {r.status}")
                if r.status >= 400:
                    raise ProviderError(f"provider {r.status}: {body[:200]}")
                data = json.loads(body)
        except aiohttp.ClientError as exc:
            raise ProviderError(f"network: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned non-json") from exc

        rid = str(data.get("order") or data.get("order_id") or data.get("id") or "")
        if not rid:
            raise ProviderError(f"provider response missing order id: {body[:200]}")
        return rid


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter (per-user, in-memory)
# ─────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        self._last: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def allow(self, user_id: int) -> tuple[bool, int]:
        async with self._lock:
            now = time.monotonic()
            prev = self._last.get(user_id, 0.0)
            delta = now - prev
            if delta < self._window:
                return False, int(self._window - delta)
            self._last[user_id] = now
            return True, 0


# ─────────────────────────────────────────────────────────────────────────────
# FSM
# ─────────────────────────────────────────────────────────────────────────────

class Flow(StatesGroup):
    choose_service = State()
    enter_target = State()
    enter_amount = State()
    confirm = State()


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_LABEL = {
    "tiktok": "🎵 TikTok — مشاهدات/متابعين",
    "telegram": "✈️ Telegram — أعضاء قناة",
    "instagram": "📸 Instagram — متابعين",
    "discord": "🎮 Discord — أعضاء وهميين",
}

SERVICE_SHORT = {
    "tiktok": "TikTok",
    "telegram": "Telegram",
    "instagram": "Instagram",
    "discord": "Discord",
}


def services_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=SERVICE_LABEL[s], callback_data=f"svc:{s}")]
        for s in PROVIDERS
    ]
    rows.append([InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأكيد", callback_data="confirm")],
            [InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel")],
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

router = Router()
limiter = RateLimiter(RATE_LIMIT_PER_USER_SECONDS)


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    text = (
        "<b>بوت الرشق</b>\n\n"
        "اختر الخدمة من الأزرار بالأسفل لبدء الطلب.\n"
        "كل خدمة لها حد أدنى وأقصى وخطوة ثابتة."
    )
    await msg.answer(text, reply_markup=services_kb())


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("تم الإلغاء. أرسل /start للبدء من جديد.")


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("تم الإلغاء. أرسل /start للبدء من جديد.")
    await cb.answer()


@router.callback_query(F.data.startswith("svc:"))
async def cb_service(cb: CallbackQuery, state: FSMContext) -> None:
    service = cb.data.split(":", 1)[1]
    if service not in PROVIDERS:
        await cb.answer("خدمة غير معروفة", show_alert=True)
        return
    cfg = PROVIDERS[service]
    await state.set_state(Flow.enter_target)
    await state.update_data(service=service)
    hint = {
        "tiktok": "أرسل اسم المستخدم أو رابط الفيديو/الحساب.",
        "telegram": "أرسل @username أو رابط t.me.",
        "instagram": "أرسل @username أو رابط الحساب.",
        "discord": "أرسل رابط دعوة Discord (discord.gg/xxxx).",
    }[service]
    await cb.message.edit_text(
        f"<b>{SERVICE_LABEL[service]}</b>\n\n"
        f"{hint}\n\n"
        f"الكمية: من {cfg['min']} إلى {cfg['max']} بخطوة {cfg['step']}.",
    )
    await cb.answer()


@router.message(Flow.enter_target)
async def step_target(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service = data.get("service")
    if not service:
        await state.clear()
        await msg.answer("انتهت الجلسة. أرسل /start.")
        return
    canonical = validate_target(service, msg.text or "")
    if not canonical:
        await msg.answer("القيمة غير صالحة. أعد الإرسال بصيغة صحيحة.")
        return
    await state.update_data(target=canonical)
    cfg = PROVIDERS[service]
    await state.set_state(Flow.enter_amount)
    await msg.answer(
        f"الهدف: <code>{canonical}</code>\n"
        f"أرسل الكمية ({cfg['min']}–{cfg['max']}, خطوة {cfg['step']})."
    )


@router.message(Flow.enter_amount)
async def step_amount(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service = data.get("service")
    target = data.get("target")
    if not service or not target:
        await state.clear()
        await msg.answer("انتهت الجلسة. أرسل /start.")
        return
    amount = validate_amount(service, (msg.text or "").strip())
    if amount is None:
        cfg = PROVIDERS[service]
        await msg.answer(
            f"الكمية غير صالحة. الحد {cfg['min']}–{cfg['max']} بخطوة {cfg['step']}."
        )
        return
    await state.update_data(amount=amount)
    await state.set_state(Flow.confirm)
    cfg = PROVIDERS[service]
    await msg.answer(
        "<b>مراجعة الطلب</b>\n"
        f"الخدمة: {SERVICE_SHORT[service]}\n"
        f"الهدف: <code>{target}</code>\n"
        f"الكمية: {amount} {cfg['unit']}\n"
        f"الوقت المتوقع: ~{cfg['eta_seconds']} ثانية",
        reply_markup=confirm_kb(),
    )


@router.callback_query(F.data == "confirm", Flow.confirm)
async def cb_confirm(cb: CallbackQuery, state: FSMContext, provider: ProviderClient) -> None:
    data = await state.get_data()
    service = data.get("service")
    target = data.get("target")
    amount = data.get("amount")
    if not (service and target and amount):
        await state.clear()
        await cb.message.edit_text("انتهت الجلسة. أرسل /start.")
        await cb.answer()
        return

    ok, retry_in = await limiter.allow(cb.from_user.id)
    if not ok:
        await cb.answer(f"انتظر {retry_in} ثانية قبل طلب جديد.", show_alert=True)
        return

    await cb.answer()
    await cb.message.edit_text("⏳ يتم إرسال الطلب إلى المزوّد...")

    order = Order(service=service, target=target, amount=amount)
    try:
        rid = await provider.place_order(order)
        order.request_id = rid
    except ProviderError as exc:
        log.exception("order failed: %s", exc)
        await state.clear()
        await cb.message.edit_text(
            f"❌ فشل الطلب: <code>{str(exc)[:200]}</code>\nأرسل /start للمحاولة مجددًا."
        )
        return
    except Exception:
        log.exception("unexpected order failure")
        await state.clear()
        await cb.message.edit_text("❌ خطأ غير متوقع. أرسل /start.")
        return

    await state.clear()
    await cb.message.edit_text(
        "<b>✅ تم استلام الطلب</b>\n"
        f"المعرّف: <code>{rid}</code>\n"
        f"الخدمة: {SERVICE_SHORT[service]}\n"
        f"الهدف: <code>{target}</code>\n"
        f"الكمية: {amount} {PROVIDERS[service]['unit']}\n"
        f"وقت المعالجة المتوقع: ~{PROVIDERS[service]['eta_seconds']} ثانية"
    )


@router.message(Command("stats"))
async def cmd_stats(msg: Message) -> None:
    if ADMIN_IDS and msg.from_user and msg.from_user.id not in ADMIN_IDS:
        return
    lines = ["<b>إعداد المزوّدين</b>"]
    for svc, cfg in PROVIDERS.items():
        state = "✅" if cfg["url"] and cfg["key"] else "⚠️ غير مهيّأ"
        lines.append(f"{SERVICE_SHORT[svc]}: {state}")
    await msg.answer("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Application lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def run_bot() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector, timeout=HTTP_TIMEOUT) as session:
        dp["provider"] = ProviderClient(session)
        # aiogram injects dict entries as handler kwargs by name.
        dp.workflow_data.update({"provider": dp["provider"]})
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("bot started")
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            await bot.session.close()
            log.info("bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        log.info("shutdown")