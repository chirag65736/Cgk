"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           CARDX CHK - COMPLETE TELEGRAM BOT (Railway Edition)                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import logging, asyncio, time, os, signal, random, string, re, json, io
from datetime import datetime, timedelta
from typing import Dict, Any

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor
import aiohttp
import requests

from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from database import (
    get_user, create_user, get_user_credits, update_credits,
    get_db_connection, is_gate_enabled, set_gate_status,
    update_user_stats, is_banned, ban_user, unban_user,
    get_all_user_ids, get_user_proxies, DB_CONFIG,
    _premium_cache, _premium_cache_ttl
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv("BOT_TOKEN", "8921031179:AAG8jE62_G9V6djbuFVp0ivLE4CReFWFzNw")
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
START_IMAGE_URL = "https://i.ibb.co/KpVyW1h4/Web-Photo-Editor-Picsart-Ai-Image-Enhancer.jpg"
LOG_CHANNEL_ID = -1003838614236
BOT_LINK = os.getenv("BOT_LINK", "http://t.me/blackcheckers_bot")
REQUIRED_CHANNEL_ID = int(os.getenv("REQUIRED_CHANNEL_ID", "-1003908746727"))
REQUIRED_GROUP_ID = int(os.getenv("REQUIRED_GROUP_ID", "-1003811358760"))
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/p_r_v_l_f_s_2")
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/bfbtv")

ADMIN_IDS = {8685134338, 8760363324}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# BOT INIT
# ═══════════════════════════════════════════════════════════════════════════════

_session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED STATE
# ═══════════════════════════════════════════════════════════════════════════════

MSH_SESSIONS = {}
MST_SESSIONS = {}
STCO_SESSIONS = {}
SH_SESSIONS = {}
user_last_command_time = {}

CUSTOM_CHARGED_EMOJI_ID = "4956719506027185156"
CUSTOM_APPROVED_EMOJI_ID = "4958610528588008305"
CUSTOM_DECLINED_EMOJI_ID = "4956612582816351459"
_SEP = "━━━━━━━━━━━━━━━━"

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def luhn_check(card_number):
    card_number = str(card_number).strip()
    if not card_number.isdigit():
        return False
    total = 0
    for i, char in enumerate(card_number[::-1]):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0

def get_premium_status(user_id):
    now = time.monotonic()
    cached = _premium_cache.get(user_id)
    if cached is not None and now < cached[1]:
        return cached[0]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_premium, premium_expiry FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, None
    is_premium_flag = row['is_premium']
    expiry = row['premium_expiry']
    result = (False, None)
    if is_premium_flag == 1:
        if expiry and datetime.now() < expiry:
            result = (True, expiry)
        else:
            cursor.execute("UPDATE users SET is_premium = 0, premium_expiry = NULL, credits = 150 WHERE user_id = %s", (user_id,))
            conn.commit()
    conn.close()
    _premium_cache[user_id] = (result, now + _premium_cache_ttl)
    return result

async def get_user_plan_name(user_id):
    is_premium, _ = get_premium_status(user_id)
    if is_premium:
        try:
            def _sync():
                conn = get_db_connection()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT plan FROM receipts WHERE user_id = %s ORDER BY purchased_on DESC LIMIT 1", (user_id,))
                row = cur.fetchone()
                conn.close()
                return row['plan'].upper() if row else "PREMIUM"
            return await asyncio.to_thread(_sync)
        except Exception:
            pass
        return "PREMIUM"
    return "TRIAL"

async def get_bin_info(bin_number):
    url = f"https://bins.antipublic.cc/bins/{bin_number}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "bin": data.get("bin"),
                        "scheme": data.get("brand", "N/A"),
                        "type": data.get("type", "N/A"),
                        "brand": data.get("level", "N/A"),
                        "bank": data.get("bank", "N/A"),
                        "country": data.get("country_name", "N/A"),
                        "country_emoji": data.get("country_flag", ""),
                    }
    except Exception:
        pass
    return {}

async def ensure_user_credits(user_id, username="unknown"):
    def _sync():
        try:
            if not get_user(user_id):
                create_user(user_id, username or "unknown")
        except Exception as e:
            logger.error(f"ensure_user: {e}")
    await asyncio.to_thread(_sync)

def _status_sync(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    plan, credits_str, joined_str = "Trial", "0", "N/A"
    try:
        cur.execute("SELECT is_premium, premium_expiry, joined_at FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            if row['joined_at']:
                joined_str = row['joined_at'].strftime('%Y-%m-%d')
            if row['is_premium'] == 1 and row['premium_expiry'] and datetime.now() < row['premium_expiry']:
                cur.execute("SELECT plan FROM receipts WHERE user_id = %s ORDER BY purchased_on DESC LIMIT 1", (user_id,))
                r = cur.fetchone()
                plan = r['plan'] if r else "Premium"
        cr = get_user_credits(user_id)
        credits_str = str(cr if cr is not None else 0)
    except Exception as e:
        logger.error(f"status: {e}")
    finally:
        conn.close()
    return credits_str, plan, joined_str

async def _get_caption(user):
    credits_str, access_str, joined_str = await asyncio.to_thread(_status_sync, user.id)
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    dl = '<a href="https://t.me/FailureFr_07">kคli liຖนxx</a>'
    return (
        f"𝗨𝘀𝗲𝗿 ➛ {ul}\n"
        f"𝗨𝘀𝗲𝗿 𝗜𝗗 ➛ <code>{user.id}</code>\n"
        f"𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>{access_str}</b>\n"
        f"𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛ <b>{credits_str}</b>\n"
        f"𝗝𝗼𝗶𝗻𝗲𝗱 ➛ <b>{joined_str}</b>\n"
        f"{_SEP}\n𝗗𝗲𝘃 ➛ {dl}"
    )

def _loading_caption(user):
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    dl = '<a href="https://t.me/FailureFr_07">kคli liຖนxx</a>'
    return (
        f"𝗨𝘀𝗲𝗿 ➛ {ul}\n"
        f"𝗨𝘀𝗲𝗿 𝗜𝗗 ➛ <code>{user.id}</code>\n"
        f"𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>Loading…</b>\n"
        f"𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛ <b>Loading…</b>\n"
        f"𝗝𝗼𝗶𝗻𝗲𝗱 ➛ <b>Loading…</b>\n"
        f"{_SEP}\n𝗗𝗲𝘃 ➛ {dl}"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════

_MEMBERSHIP_CACHE = {}
_VALID_STATUSES = {"member", "administrator", "creator"}

async def check_membership(user_id, bot_instance):
    now = time.monotonic()
    cached = _MEMBERSHIP_CACHE.get(user_id)
    if cached and now < cached[1]:
        return cached[0]
    try:
        ch, gr = await asyncio.gather(
            bot_instance.get_chat_member(REQUIRED_CHANNEL_ID, user_id),
            bot_instance.get_chat_member(REQUIRED_GROUP_ID, user_id),
        )
        is_member = ch.status in _VALID_STATUSES and gr.status in _VALID_STATUSES
    except Exception as e:
        logger.warning(f"Membership check: {e}")
        is_member = True
    ttl = 60 if is_member else 15
    _MEMBERSHIP_CACHE[user_id] = (is_member, now + ttl)
    return is_member

_JOIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗝𝗼𝗶𝗻 𝗖𝗵𝗮𝗻𝗻𝗲𝗹", url=CHANNEL_LINK)],
    [InlineKeyboardButton(text="𝗝𝗼𝗶𝗻 𝗚𝗿𝗼𝘂𝗽", url=GROUP_LINK)],
    [InlineKeyboardButton(text="𝗩𝗲𝗿𝗶𝗳𝘆", callback_data="verify_membership")]
])

JOIN_TEXT = (
    "⚠️ <b>𝗔𝗰𝗰𝗲𝘀𝘀 𝗥𝗲𝘀𝘁𝗿𝗶𝗰𝘁𝗲𝗱!</b>\n\n"
    "𝗧𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗯𝗼𝘁 𝘆𝗼𝘂 𝗺𝘂𝘀𝘁 𝗷𝗼𝗶𝗻 𝗼𝘂𝗿 𝗰𝗵𝗮𝗻𝗻𝗲𝗹 𝗮𝗻𝗱 𝗴𝗿𝗼𝘂𝗽 𝗳𝗶𝗿𝘀𝘁.\n\n"
    "𝗖𝗹𝗶𝗰𝗸 𝗩𝗲𝗿𝗶𝗳𝘆 𝗮𝗳𝘁𝗲𝗿 𝗷𝗼𝗶𝗻𝗶𝗻𝗴 𝗯𝗼𝘁𝗵."
)

class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        if not user or user.id in ADMIN_IDS:
            return await handler(event, data)
        if await asyncio.to_thread(is_banned, user.id):
            try:
                await event.reply("🚫 <b>You have been banned.</b>", parse_mode="HTML")
            except Exception:
                pass
            return
        return await handler(event, data)

class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        if not user or user.id in ADMIN_IDS:
            return await handler(event, data)
        if not await check_membership(user.id, data["bot"]):
            await event.reply(text=JOIN_TEXT, parse_mode="HTML", reply_markup=_JOIN_KB, disable_web_page_preview=True)
            return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())
dp.message.middleware(MembershipMiddleware())

@router.callback_query(F.data == "verify_membership")
async def verify_membership_callback(callback: types.CallbackQuery):
    _MEMBERSHIP_CACHE.pop(callback.from_user.id, None)
    joined = await check_membership(callback.from_user.id, callback.bot)
    if joined:
        await asyncio.gather(
            callback.answer("✅ 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱!", show_alert=True),
            callback.message.delete(),
        )
    else:
        await callback.answer("❌ 𝗝𝗼𝗶𝗻 𝗯𝗼𝘁𝗵 𝗳𝗶𝗿𝘀𝘁!", show_alert=True)

# ═══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════════════════════════════════════════

_MAIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗖𝗵𝗲𝗰𝗸𝗲𝗿", callback_data="menu_gates"),
     InlineKeyboardButton(text="𝗕𝘂𝘆 𝗡𝗼𝘄", callback_data="menu_pricing")],
    [InlineKeyboardButton(text="𝗨𝗽𝗱𝗮𝘁𝗲𝘀", url="https://t.me/+E6zoRhIFhtNmM2E5"),
     InlineKeyboardButton(text="𝗚𝗿𝗼𝘂𝗽", url="https://t.me/bfbtv")],
    [InlineKeyboardButton(text="𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url="https://t.me/failurefr_07")]
])

def _back(t): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data=t)]])

_KB_BACK_MAIN = _back("back_main")
_KB_BACK_GATES = _back("menu_gates")
_KB_BACK_MASS = _back("menu_mass_in_gates")
_KB_BACK_AUTH = _back("menu_auth")
_KB_BACK_CHARGE = _back("menu_charge")

PRICING_TEXT = (
    f'<b>𝗔𝗰𝗰𝗲𝘀𝘀 ➛ 𝗖𝗢𝗥𝗘</b> <tg-emoji emoji-id="5379869575338812919">💎</tg-emoji>\n'
    f'<b>𝗦𝗽𝗮𝗻 ➛</b> [𝟳 𝗗𝗔𝗬𝗦]\n'
    f'<b>𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛</b> 𝟭𝟯,𝟬𝟬𝟬\n'
    f'<b>𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟭𝟬$\n{_SEP}\n'
    f'<b>𝗔𝗰𝗰𝗲𝘀𝘀 ➛ 𝗘𝗟𝗜𝗧𝗘</b> <tg-emoji emoji-id="5836898273666798437">💎</tg-emoji>\n'
    f'<b>𝗦𝗽𝗮𝗻 ➛</b> [𝟭𝟱 𝗗𝗔𝗬𝗦]\n'
    f'<b>𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛</b> 𝟮𝟯,𝟬𝟬𝟬\n'
    f'<b>𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟭𝟱$\n{_SEP}\n'
    f'<b>𝗔𝗰𝗰𝗲𝘀𝘀 ➛ 𝗥𝗢𝗢𝗧</b> <tg-emoji emoji-id="4956420911310832630">💎</tg-emoji>\n'
    f'<b>𝗦𝗽𝗮𝗻 ➛</b> [𝟯𝟬 𝗗𝗔𝗬𝗦]\n'
    f'<b>𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛</b> 𝟱𝟯,𝟬𝟬𝟬\n'
    f'<b>𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟯𝟬$'
)

_KB_PRICING = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💎 𝗣𝗮𝘆 𝗩𝗶𝗮", callback_data="show_buy_plans")],
    [InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data="back_main")]
])

_KB_GATES = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗔𝘂𝘁𝗵", callback_data="menu_auth"),
     InlineKeyboardButton(text="𝗖𝗵𝗮𝗿𝗴𝗲", callback_data="menu_charge")],
    [InlineKeyboardButton(text="𝗠𝗮𝘀𝘀", callback_data="menu_mass_in_gates")],
    [InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data="back_main")]
])

_KB_MASS = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝗠𝗮𝘀𝘀", callback_data="info_msh_gate"),
     InlineKeyboardButton(text="𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝗮𝘀𝘀", callback_data="info_mst_gate")],
    [InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data="menu_gates")]
])

_KB_AUTH = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗦𝘁𝗿𝗶𝗽𝗲", callback_data="info_auth_stripe"),
     InlineKeyboardButton(text="𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲", callback_data="info_auth_braintree")],
    [InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data="menu_gates")]
])

_KB_CHARGE = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="𝗦𝘁𝗿𝗶𝗽𝗲", callback_data="info_charge_stripe"),
     InlineKeyboardButton(text="𝗣𝗮𝘆𝗽𝗮𝗹", callback_data="info_charge_paypal")],
    [InlineKeyboardButton(text="𝗦𝗵𝗼𝗽𝗶𝗳𝘆", callback_data="info_charge_shopify"),
     InlineKeyboardButton(text="𝗡𝗠𝗜", callback_data="info_charge_nmi")],
    [InlineKeyboardButton(text="« 𝗕𝗮𝗰𝗸", callback_data="menu_gates")]
])

STATIC_MENU_MAP = {
    "menu_pricing": (PRICING_TEXT, _KB_PRICING),
    "menu_gates": ("<b>𝗚𝗮𝘁𝗲𝘀 𝗦𝘁𝗮𝘁𝘂𝘀:</b>\n𝗔𝘂𝘁𝗵 ➛ 3\n𝗠𝗮𝘀𝘀 ➛ 2\n𝗖𝗵𝗮𝗿𝗴𝗲 ➛ 11\n" + _SEP, _KB_GATES),
    "menu_mass_in_gates": ("<b><i>Select a Mass Gate</i></b>", _KB_MASS),
    "menu_auth": ("<b><i>Select Auth</i></b>", _KB_AUTH),
    "menu_charge": ("<b><i>Select Charge</i></b>", _KB_CHARGE),
    "info_msh_gate": (f"{_SEP}\n<b>Shopify Mass</b>\n<b>Cmd ➛ /msh</b>\n{_SEP}", _KB_BACK_MASS),
    "info_mst_gate": (f"{_SEP}\n<b>Stripe Mass</b>\n<b>Cmd ➛ /mst</b>\n{_SEP}", _KB_BACK_MASS),
    "info_auth_stripe": (f"{_SEP}\n<b>Stripe 0$</b>\n<b>Cmd ➛ /chk</b>\n{_SEP}", _KB_BACK_AUTH),
    "info_auth_braintree": (f"{_SEP}\n<b>Braintree</b>\n<b>Cmd ➛ /b3</b>\n{_SEP}", _KB_BACK_AUTH),
    "info_charge_stripe": (f"{_SEP}\n<b>Stripe 1$</b>\n<b>Cmd ➛ /st</b>\n{_SEP}", _KB_BACK_CHARGE),
    "info_charge_paypal": (f"{_SEP}\n<b>PayPal</b>\n<b>Cmd ➛ /pp</b>\n{_SEP}", _KB_BACK_CHARGE),
    "info_charge_shopify": (f"{_SEP}\n<b>Shopify 5$</b>\n<b>Cmd ➛ /hc</b>\n{_SEP}", _KB_BACK_CHARGE),
    "info_charge_nmi": (f"{_SEP}\n<b>NMI</b>\n<b>Cmd ➛ /nmi</b>\n{_SEP}", _KB_BACK_CHARGE),
}

async def _edit(msg, text, kb):
    try:
        if msg.caption is not None:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"_edit: {e}")

async def _ans(cb, text="", **kw):
    try:
        await cb.answer(text, **kw)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("start"))
async def start(message: types.Message):
    user = message.from_user
    asyncio.create_task(ensure_user_credits(user.id, user.username))
    quick = _loading_caption(user)
    caption_task = asyncio.create_task(_get_caption(user))
    sent = await message.reply_photo(photo=START_IMAGE_URL, caption=quick, reply_markup=_MAIN_KB)
    try:
        full = await caption_task
        await sent.edit_caption(caption=full, reply_markup=_MAIN_KB)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query()
async def button_handler(callback: types.CallbackQuery):
    data = callback.data or ""
    msg = callback.message
    if not isinstance(msg, types.Message):
        await _ans(callback, "❌ Expired.", show_alert=True)
        return
    static = STATIC_MENU_MAP.get(data)
    if static:
        text, kb = static
        asyncio.create_task(_ans(callback))
        asyncio.create_task(_edit(msg, text, kb))
        return
    if data == "back_main":
        user = callback.from_user
        quick = _loading_caption(user)
        caption_task = asyncio.create_task(_get_caption(user))
        asyncio.create_task(_ans(callback))
        try:
            if msg.caption is not None:
                await msg.edit_caption(caption=quick, reply_markup=_MAIN_KB, parse_mode="HTML")
            else:
                await msg.answer_photo(photo=START_IMAGE_URL, caption=quick, reply_markup=_MAIN_KB, parse_mode="HTML")
        except Exception:
            pass
        try:
            full = await caption_task
            await msg.edit_caption(caption=full, reply_markup=_MAIN_KB, parse_mode="HTML")
        except Exception:
            pass
        return
    if data == "show_buy_plans":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💎 𝗧𝗮𝗽 𝘁𝗼 𝗕𝘂𝘆", url=f"{BOT_LINK}?start=buy")
        ]])
        asyncio.create_task(_ans(callback))
        asyncio.create_task(msg.edit_reply_markup(reply_markup=kb))
        return

# ═══════════════════════════════════════════════════════════════════════════════
# /buy
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text.startswith("/buy"))
async def buy_command(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='💎 𝗣𝗮𝘆 𝗩𝗶𝗮', callback_data="show_buy_plans")
    ]])
    await message.reply(text=PRICING_TEXT, parse_mode="HTML", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════════════════════
# /info
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text.startswith("/info"))
async def info_command(message: types.Message):
    user = message.from_user
    user_id = user.id
    u_row = await asyncio.to_thread(get_user, user_id)
    if not u_row:
        await asyncio.to_thread(create_user, user_id, user.username or "unknown")
        u_row = await asyncio.to_thread(get_user, user_id)
    raw_name = f"@{user.username}" if user.username else (user.first_name or "User")
    username_link = f'<a href="tg://user?id={user_id}">{raw_name}</a>'
    access_str = "Trial"
    if u_row and u_row.get('is_premium') == 1 and u_row.get('premium_expiry') and datetime.now() < u_row['premium_expiry']:
        access_str = "Premium"
    joined_disp = u_row['joined_at'].strftime('%Y-%m-%d') if u_row and u_row.get('joined_at') else "N/A"
    credits_disp = f"{u_row.get('credits', 0):,}" if u_row else "0"
    text = (
        f"𝗨𝘀𝗲𝗿 ➛ {username_link}\n"
        f"𝗨𝘀𝗲𝗿 𝗜𝗗 ➛ <code>{user_id}</code>\n"
        f"𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>{access_str}</b>\n"
        f"𝗖𝗿𝗲𝗱𝗶𝘁𝘀 ➛ <b>{credits_disp}</b>\n"
        f"𝗝𝗼𝗶𝗻𝗲𝗱 ➛ <b>{joined_disp}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="𝗕𝘂𝘆 𝗡𝗼𝘄", callback_data="show_buy_plans")]])
    await message.reply(text, parse_mode="HTML", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════════════════════
# /bin
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text.startswith("/bin"))
async def bin_command(message: types.Message):
    user = message.from_user
    parts = message.text.split()
    raw = parts[1] if len(parts) > 1 else (message.reply_to_message.text if message.reply_to_message else "")
    if not raw:
        await message.reply("❌ Usage: <code>/bin 456789</code>", parse_mode="HTML")
        return
    digits = re.sub(r'\D', '', raw)
    if len(digits) < 6:
        await message.reply("❌ Need 6+ digits.", parse_mode="HTML")
        return
    bin_6 = digits[:6]
    data = await get_bin_info(bin_6)
    if not data or "error" in data:
        await message.reply("❌ BIN lookup failed.", parse_mode="HTML")
        return
    brand = data.get("scheme", "N/A")
    level = data.get("brand", "N/A")
    bank = data.get("bank", "N/A")
    country = data.get("country", "N/A")
    flag = data.get("country_emoji", "")
    country_disp = f"{flag} {country}" if flag else country
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    text = (
        f"𝗕𝗶𝗻 ➛ <code>{bin_6}</code>\n"
        f"𝗕𝗿𝗮𝗻𝗱 ➛ <b>{brand}</b>\n"
        f"𝗟𝗲𝘃𝗲𝗹 ➛ <b>{level}</b>\n"
        f"𝗕𝗮𝗻𝗸 ➛ <b>{bank}</b>\n"
        f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➛ <b>{country_disp}</b>\n"
        f"𝗨𝘀𝗲𝗿 ➛ {ul}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="𝗕𝘂𝘆 𝗡𝗼𝘄", callback_data="show_buy_plans")]])
    await message.reply(text=text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text.startswith("/binn"))
async def binn_command(message: types.Message):
    await bin_command(message)

# ═══════════════════════════════════════════════════════════════════════════════
# GATE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

async def simple_check(card_string):
    parts = card_string.split('|')
    if len(parts) < 4:
        return {"status": "error", "response": "Invalid format"}
    cc, mm, yy, cvv = parts
    if len(yy) == 4: yy = yy[2:]
    if not luhn_check(cc):
        return {"status": "declined", "response": "Invalid card number"}
    return {"status": "approved", "response": "Payment Method Added"}

async def _process_gate(message, gate_name, gate_display):
    if not await asyncio.to_thread(is_gate_enabled, gate_name):
        await message.reply(f"🚧 <b>{gate_display} 𝗚𝗮𝘁𝗲 𝗶𝘀 𝘂𝗻𝗱𝗲𝗿 𝗺𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲.</b>", parse_mode="HTML")
        return
    user = message.from_user
    user_id = user.id
    is_premium, _ = get_premium_status(user_id)
    if not is_premium:
        last = user_last_command_time.get(user_id, 0)
        if time.time() - last < 10:
            await message.reply(f"⚠️ Wait {round(10 - (time.time()-last), 1)}s.", parse_mode="HTML")
            return
        user_last_command_time[user_id] = time.time()
    credits = await asyncio.to_thread(get_user_credits, user_id)
    if credits < 1:
        await message.reply("❌ <b>Insufficient Credits!</b>", parse_mode="HTML")
        return
    parts = message.text.split(maxsplit=1)
    raw_text = parts[1].strip() if len(parts) > 1 else ""
    if message.reply_to_message:
        raw_text += " " + (message.reply_to_message.text or message.reply_to_message.caption or "")
    pattern = r'(\d{13,19})[\|/:\s]+(\d{1,2})[\|/:\s]+(\d{2,4})[\|/:\s]+(\d{3,4})'
    match = re.search(pattern, raw_text)
    if not match:
        await message.reply("❌ <b>Invalid Card Format.</b>", parse_mode="HTML")
        return
    cc, mm, yy_raw, cvv = match.groups()
    mm = mm.zfill(2)
    yy = yy_raw[2:] if len(yy_raw) == 4 else yy_raw
    formatted_cc = f"{cc}|{mm}|{yy}|{cvv}"
    if not luhn_check(cc):
        await message.reply("❌ <b>Invalid Card Number</b>", parse_mode="HTML")
        return
    proc_msg = await message.reply("<pre>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴…⏳</pre>", parse_mode="HTML")
    api_result = await simple_check(formatted_cc)
    bin_info = await get_bin_info(cc[:6])
    bin_scheme = bin_info.get("scheme", "N/A")
    bin_bank = bin_info.get("bank", "N/A")
    country_name = bin_info.get("country", "N/A")
    country_flag = bin_info.get("country_emoji", "")
    bin_country = f"{country_flag} {country_name}" if country_flag else country_name
    status_raw = api_result.get("status", "").lower()
    is_charged = False
    if status_raw == "charged":
        final_status = f'𝗖𝗛𝗔𝗥𝗚𝗘𝗗 <tg-emoji emoji-id="{CUSTOM_CHARGED_EMOJI_ID}">💎</tg-emoji>'
        is_charged = True
        deduct = 2
    elif status_raw == "approved":
        final_status = f'𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 <tg-emoji emoji-id="{CUSTOM_APPROVED_EMOJI_ID}">✅</tg-emoji>'
        is_charged = True
        deduct = 1
    elif status_raw == "declined":
        final_status = f'𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 <tg-emoji emoji-id="{CUSTOM_DECLINED_EMOJI_ID}">❌</tg-emoji>'
        deduct = 1
    else:
        final_status = "𝗘𝗥𝗥𝗢𝗥 ⚠️"
        deduct = 0
    if deduct > 0:
        new_bal = max(0, credits - deduct)
        await asyncio.to_thread(update_credits, user_id, new_bal)
    await asyncio.to_thread(update_user_stats, user_id, is_charged)
    plan_name = await get_user_plan_name(user_id)
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    user_display = f"{ul} <b>({plan_name})</b>"
    dev_link = '<a href="https://t.me/FailureFr_07">kCลli liຖนxx</a>'
    final_caption = (
        f"𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {final_status}\n"
        f"𝗖𝗮𝗿𝗱 ➛ <code>{formatted_cc}</code>\n"
        f"𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ {gate_display}\n"
        f"𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{api_result.get('response', 'N/A')}</b>\n"
        f"𝗕𝗿𝗮𝗻𝗱 ➛ <b>{bin_scheme}</b>\n"
        f"𝗜𝘀𝘀𝘂𝗲𝗿 ➛ <b>{bin_bank}</b>\n"
        f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➛ <b>{bin_country}</b>\n"
        f"𝗨𝘀𝗲𝗿 ➛ {user_display}\n"
        f"𝗗𝗲𝘃 ➛ {dev_link}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="𝗕𝘂𝘆 𝗡𝗼𝘄", callback_data="show_buy_plans")]])
    try:
        await proc_msg.edit_text(text=final_caption, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.reply(text=final_caption, parse_mode="HTML", reply_markup=kb)

# Register all gates
@router.message(F.text.startswith("/chk"))
async def chk_command(message): await _process_gate(message, "chk", "𝗦𝘁𝗿𝗶𝗽𝗲 𝟬$")

@router.message(F.text.startswith("/stco"))
async def stco_command(message): await _process_gate(message, "stco", "𝗦𝘁𝗿𝗶𝗽𝗲 𝗛𝗶𝘁𝘁𝗲𝗿")

@router.message(F.text.startswith("/st"))
async def st_command(message):
    if message.text.startswith("/stco"): return
    await _process_gate(message, "st", "𝗦𝘁𝗿𝗶𝗽𝗲 𝟭$")

@router.message(F.text.startswith("/pp"))
async def pp_command(message): await _process_gate(message, "pp", "𝗣𝗮𝘆𝗽𝗮𝗹 𝟬.𝟭𝟬$")

@router.message(F.text.startswith("/hc"))
async def hc_command(message): await _process_gate(message, "hc", "𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝟱$")

@router.message(F.text.startswith("/sp"))
async def sp_command(message): await _process_gate(message, "sp", "𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝟭$")

@router.message(F.text.startswith("/pf"))
async def pf_command(message): await _process_gate(message, "pf", "𝗣𝗮𝘆𝗙𝗮𝘀𝘁")

@router.message(F.text.startswith("/ft"))
async def ft_command(message): await _process_gate(message, "ft", "𝗙𝗮𝘁𝗭𝗲𝗯𝗿𝗮")

@router.message(F.text.startswith("/bl"))
async def bl_command(message): await _process_gate(message, "bl", "𝗕𝗹𝘂𝗲𝗽𝗮𝘆")

@router.message(F.text.startswith("/at"))
async def at_command(message): await _process_gate(message, "at", "𝗔𝘂𝘁𝗵𝗼𝗿𝗶𝘇𝗲.𝗻𝗲𝘁")

@router.message(F.text.startswith("/pw"))
async def pw_command(message): await _process_gate(message, "pw", "𝗣𝗮𝘆𝗪𝗮𝘆")

@router.message(F.text.startswith("/rz"))
async def rz_command(message): await _process_gate(message, "rz", "𝗥𝗮𝘇𝗼𝗿𝗽𝗮𝘆")

@router.message(F.text.startswith("/pyu"))
async def pyu_command(message): await _process_gate(message, "pyu", "𝗣𝗮𝘆𝗨")

@router.message(F.text.startswith("/b3"))
async def b3_command(message): await _process_gate(message, "b3", "𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲")

@router.message(F.text.startswith("/nmi2"))
async def nmi2_command(message): await _process_gate(message, "nmi2", "𝗡𝗠𝗜𝟮")

@router.message(F.text.startswith("/nmi"))
async def nmi_command(message):
    if message.text.startswith("/nmi2"): return
    await _process_gate(message, "nmi", "𝗡𝗠𝗜")

@router.message(F.text.startswith("/vbv"))
async def vbv_command(message): await _process_gate(message, "vbv", "𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲 𝗩𝗕𝗩")

@router.message(F.text.startswith("/sh"))
async def sh_command(message): await _process_gate(message, "sh", "𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝗠𝗮𝘀𝘀")

@router.message(F.text.startswith("/mst"))
async def mst_command(message): await _process_gate(message, "mst", "𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝗮𝘀𝘀")

@router.message(F.text.startswith("/msh"))
async def msh_command(message): await _process_gate(message, "msh", "𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝟬-𝟮𝟬$")

# ═══════════════════════════════════════════════════════════════════════════════
# /stats
# ═══════════════════════════════════════════════════════════════════════════════

def _leaderboard():
    import collections
    counts = collections.Counter()
    details = {}
    for fn in ["mshh.txt", "mst.txt"]:
        if not os.path.exists(fn): continue
        try:
            with open(fn, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split("|")
                    if len(parts) >= 3:
                        uid, uname, fname = parts[0], parts[1], parts[2]
                        counts[uid] += 1
                        if uid not in details:
                            details[uid] = {"username": uname if uname != "None" else None, "first_name": fname}
        except Exception:
            pass
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10], details

@router.message(F.text.startswith("/stats"))
async def stats_command(message: types.Message):
    sm = await message.answer("⏳ Loading...", parse_mode="HTML")
    try:
        top, det = await asyncio.to_thread(_leaderboard)
        if not top:
            text = "<b>🏆 𝗟𝗲𝗮𝗱𝗲𝗿𝗯𝗼𝗮𝗿𝗱</b>\n" + _SEP + "\nNo data yet.\n" + _SEP
        else:
            header = "<b>🏆 𝗟𝗲𝗮𝗱𝗲𝗿𝗯𝗼𝗮𝗿𝗱</b>\n" + _SEP + "\n"
            body = ""
            for i, (uid, c) in enumerate(top, 1):
                d = det.get(uid, {})
                fn = d.get('first_name', 'Unknown')
                un = d.get('username')
                link = f'<a href="https://t.me/{un}">{fn}</a>' if un else f'<a href="tg://user?id={uid}">{fn}</a>'
                medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
                body += f"{medal} {link} ➛ <b>{c} 💎</b>\n"
            text = header + body + _SEP
        await sm.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"stats: {e}")
        await sm.edit_text("❌ Error.", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# /ban /unban
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("ban"))
async def ban_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("⛔ Admin only.")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.reply("Usage: /ban [user_id]")
        return
    tid = int(parts[1])
    if tid in ADMIN_IDS:
        await message.reply("❌ Cannot ban admin.")
        return
    await asyncio.to_thread(ban_user, tid)
    await message.reply(f"🚫 User <code>{tid}</code> banned.", parse_mode="HTML")

@router.message(Command("unban"))
async def unban_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("⛔ Admin only.")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.reply("Usage: /unban [user_id]")
        return
    tid = int(parts[1])
    removed = await asyncio.to_thread(unban_user, tid)
    await message.reply(f"✅ Unbanned {tid}." if removed else f"⚠️ Not banned.", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# /broad
# ═══════════════════════════════════════════════════════════════════════════════

_broad_running = False

@router.message(Command("broad"))
async def broad_command(message: types.Message):
    global _broad_running
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("⛔ Admin only.")
        return
    if not message.reply_to_message:
        await message.reply("↩️ Reply to a message with /broad")
        return
    if _broad_running:
        await message.reply("⚠️ Already running.")
        return
    _broad_running = True
    try:
        user_ids = await asyncio.to_thread(get_all_user_ids)
        total = len(user_ids)
        target = message.reply_to_message
        sm = await message.reply(f"📡 Broadcasting to {total}...")
        sent = blocked = failed = 0
        for idx, uid in enumerate(user_ids, 1):
            try:
                await target.copy_to(chat_id=uid)
                sent += 1
            except TelegramForbiddenError:
                blocked += 1
            except Exception:
                failed += 1
            if idx % 50 == 0:
                try:
                    await sm.edit_text(f"📡 {idx}/{total}\nSent: {sent}\nBlocked: {blocked}\nFailed: {failed}")
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        await sm.edit_text(f"✅ Done\nSent: {sent}\nBlocked: {blocked}\nFailed: {failed}")
    finally:
        _broad_running = False

# ═══════════════════════════════════════════════════════════════════════════════
# /vps
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("vps"))
async def vps_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("⛔ Admin only.")
        return
    try:
        import psutil, platform
        def _fmt(n):
            for u in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024: return f"{n:.1f} {u}"
                n /= 1024
            return f"{n:.1f} PB"
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime = str(timedelta(seconds=int(time.time() - psutil.boot_time())))
        text = (
            f"<b>VPS STATUS</b>\n{_SEP}\n"
            f"<b>CPU</b> ➛ <b>{cpu:.1f}%</b>\n"
            f"<b>RAM</b> ➛ <b>{_fmt(ram.used)} / {_fmt(ram.total)}</b>\n"
            f"<b>DISK</b> ➛ <b>{_fmt(disk.used)} / {_fmt(disk.total)}</b>\n"
            f"<b>OS</b> ➛ <b>{platform.system()}</b>\n"
            f"<b>Uptime</b> ➛ <b>{uptime}</b>"
        )
        await message.reply(text, parse_mode="HTML")
    except ImportError:
        await message.reply("⚠️ pip install psutil")

# ═══════════════════════════════════════════════════════════════════════════════
# /on /off
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text.startswith("/on"))
async def on_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: /on <gate>")
        return
    gate = parts[1].lower()
    set_gate_status(gate, True)
    await message.reply(f"✅ <b>{gate.upper()}</b> ENABLED", parse_mode="HTML")

@router.message(F.text.startswith("/off"))
async def off_command(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: /off <gate>")
        return
    gate = parts[1].lower()
    set_gate_status(gate, False)
    await message.reply(f"🚧 <b>{gate.upper()}</b> DISABLED", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# /cmds
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("cmds"))
async def cmds_command(message: types.Message):
    text = (
        "<b>📋 Commands</b>\n" + _SEP + "\n"
        "<b>Auth:</b> /chk /b3 /vbv\n"
        "<b>Charge:</b> /st /pp /hc /sp /pf /ft /nmi /nmi2 /bl /at /pw /rz /pyu\n"
        "<b>Mass:</b> /msh /mst /sh /stco\n"
        "<b>Utility:</b> /bin /info /buy /stats /proxy /cmds\n"
        "<b>Admin:</b> /ban /unban /broad /vps /on /off"
    )
    await message.reply(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# /proxy
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_proxy(line):
    s = line.strip()
    m = re.match(r'^(?:http|https|socks4|socks5)://', s, re.IGNORECASE)
    if m: s = s[m.end():]
    for pat in [r'^([^:@]+):([^:@]+)@([^:@]+):(\d+)$', r'^([^:@]+):([^:@]+):([^:@]+):(\d+)$']:
        match = re.match(pat, s)
        if match:
            u, p, ip, port = match.groups()
            return f"{u} {p} {ip} {port}"
    match = re.match(r'^([^:@]+):(\d+)$', s)
    if match:
        ip, port = match.groups()
        return f"{ip} {port}"
    return None

@router.message(F.text.startswith("/proxy"))
async def proxy_command(message: types.Message):
    user_id = message.from_user.id
    raw = ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1: raw += parts[1].strip() + "\n"
    if message.reply_to_message and message.reply_to_message.text:
        raw += message.reply_to_message.text + "\n"
    if not raw.strip():
        await message.reply("Usage: /proxy <proxy>")
        return
    valid = []
    for line in raw.strip().split('\n'):
        p = _parse_proxy(line)
        if p: valid.append(p)
    if not valid:
        await message.reply("❌ No valid proxies.")
        return
    def _save():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT proxy FROM proxies WHERE user_id = %s", (user_id,))
        existing = {r[0] for r in cur.fetchall()}
        added = 0
        for p in valid:
            if p not in existing:
                cur.execute("INSERT INTO proxies (user_id, proxy) VALUES (%s, %s)", (user_id, p))
                added += 1
        conn.commit()
        conn.close()
        return added
    added = await asyncio.to_thread(_save)
    await message.reply(f"✅ <b>{added}</b> proxies saved.", parse_mode="HTML")

@router.message(F.text.startswith("/myproxies"))
async def myproxies_command(message: types.Message):
    user_id = message.from_user.id
    def _count():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM proxies WHERE user_id = %s", (user_id,))
        c = cur.fetchone()[0]
        conn.close()
        return c
    count = await asyncio.to_thread(_count)
    await message.reply(f"📊 You have <b>{count}</b> proxies.", parse_mode="HTML")

@router.message(F.text.startswith("/clearproxy"))
async def clearproxy_command(message: types.Message):
    user_id = message.from_user.id
    def _clear():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM proxies WHERE user_id = %s", (user_id,))
        count = cur.fetchone()[0]
        if count > 0:
            cur.execute("DELETE FROM proxies WHERE user_id = %s", (user_id,))
            conn.commit()
        conn.close()
        return count
    count = await asyncio.to_thread(_clear)
    await message.reply(f"✅ Deleted <b>{count}</b> proxies.", parse_mode="HTML")

@router.message(F.text.startswith("/checkproxy"))
async def checkproxy_command(message: types.Message):
    await message.reply("✅ Use /myproxies to view your proxies.", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# /fb
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("fb"))
async def feedback_cmd(message: types.Message):
    await message.reply("📝 <b>Send your feedback</b>\n\nReply with your message.", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

async def on_startup(bot: Bot):
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    logger.info(f"✅ Bot running as @{me.username} ({me.id})")
    logger.info(f"✅ Admins: {ADMIN_IDS}")

async def on_shutdown(bot: Bot):
    logger.info("🛑 Shutting down...")
    await bot.session.close()

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

def _run():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    def _sig(*_): stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(s, _sig)
        except NotImplementedError: pass
    async def _runner():
        task = asyncio.create_task(main())
        await stop.wait()
        task.cancel()
        try: await task
        except (asyncio.CancelledError, Exception): pass
    try:
        loop.run_until_complete(_runner())
    except KeyboardInterrupt:
        pass
    finally:
        try: loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception: pass
        loop.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    _run()