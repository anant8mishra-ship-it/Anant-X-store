import os
import sys
import asyncio
import sqlite3
import random
import logging
import time
import aiohttp
import hmac
import hashlib
import html
import base64
import urllib.parse
import io
import json
import re
import socket
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, Dice, BufferedInputFile,
    WebAppInfo
)
from aiohttp import web

try:  # optional: lets the bot speak the welcome line by itself (pip install gTTS)
    from gtts import gTTS
except Exception:
    gTTS = None

# ==============================================================================
# 1. BOT CONFIGURATION & CONSTANTS
# ==============================================================================
# Bot-Hosting servers may not show an Environment Variables tab. In that case,
# fill in the four HOST_* values below once. Environment variables still take
# precedence when the bot is deployed on a host that supports them.
# Do not share the real token publicly.
HOST_BOT_TOKEN = ""
HOST_ADMIN_ID = "5587013495"
HOST_BOT_USERNAME = "Anantxsellerbot"
HOST_ADMIN_CONTACT = "@nageshsahoo01"

# Bumped every time this file is edited here, so you can confirm from inside
# Telegram (via /version, or the top of /admin) that the build you deployed
# is actually the one running — instead of guessing.
BOT_CODE_VERSION = "2026-09-26-new-start-message-v12"

BOT_TOKEN = os.getenv("BOT_TOKEN", HOST_BOT_TOKEN).strip()

# Railway-safe SQLite path. Set DB_PATH=/data/yp_shop.db when using a Railway Volume.
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "yp_shop.db"))
DB_DIR = os.path.dirname(os.path.abspath(DB_PATH))
if DB_DIR:
    os.makedirs(DB_DIR, exist_ok=True)

def _read_override_bot_token() -> str:
    """If the owner has pasted a new bot token from /admin -> Change Bot Token,
    it's saved to the settings table as 'override_bot_token'. That always wins
    over the env var / HOST_BOT_TOKEN, so a token change survives a redeploy
    too. Safe to call even before the settings table exists (first run)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key='override_bot_token'")
        row = cur.fetchone()
        conn.close()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""

BOT_TOKEN = _read_override_bot_token() or BOT_TOKEN

# IMPORTANT: Telegram username/ID controls the bot admin and notifications.
# It does NOT decide where money is received. ZapUPI uses its own API account;
# FamPay uses the UPI ID and FamGateway API key configured from /admin.
BOT_USERNAME = os.getenv("BOT_USERNAME", HOST_BOT_USERNAME).strip().lstrip("@")
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", HOST_ADMIN_CONTACT).strip()
if ADMIN_CONTACT and not ADMIN_CONTACT.startswith("@") and not ADMIN_CONTACT.startswith("http"):
    ADMIN_CONTACT = f"@{ADMIN_CONTACT}"

_admin_id_raw = os.getenv("ADMIN_ID", HOST_ADMIN_ID).strip()
try:
    ADMIN_ID = int(_admin_id_raw)
except (TypeError, ValueError):
    ADMIN_ID = 0

def configured_support_url() -> str:
    """Build the default support link from the configured admin contact."""
    if ADMIN_CONTACT.startswith(("http://", "https://")):
        return ADMIN_CONTACT
    username = ADMIN_CONTACT.lstrip("@").strip()
    return f"https://t.me/{username}" if username and username != "YOUR_USERNAME" else "https://t.me/YOUR_SUPPORT"

USDT_TO_INR = 90.0

# How a key is paid for:
#   True  -> wallet pays first: enough balance = key from wallet; less balance = the wallet
#            balance is used and the QR is only for the remaining amount; no balance = full QR.
#   False -> the user ALWAYS gets a direct QR for the full key price; the wallet is never touched.
PRODUCT_PAY_FROM_WALLET = True
VIP_DISCOUNT_PERCENTAGE = 15.0
VIP_PRICE_INR = 299.0

WELCOME_STICKER_ID = "CAACAgIAAxkBAAEU-WZmH_..."  # Replace with your sticker ID
SPIN_DELAY_SECONDS = 2.5

# Never let a payment-provider request hold a Telegram callback forever.
# Without a client timeout, a slow/unreachable gateway leaves the user stuck
# on Telegram's "Generating..." spinner with no actionable error.
ZAPUPI_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
AUTO_VERIFY_INTERVAL_SECONDS = 3
PAYMENT_TIMEOUT_SECONDS = 900

# Orders are watched immediately after their QR is created.  The database
# scanner started at boot remains as a recovery path for orders that were
# created before a restart.
_auto_verify_tasks: Dict[str, asyncio.Task] = {}

FIXED_CATEGORIES = [
    "ANDROID NON ROOT PANEL",
    "ANDROID ROOT PANEL",
    "IPHONE PANEL",
    "PC PANEL",
]

# ==============================================================================
# YOUR PREMIUM EMOJIS – all required emoji IDs
# ==============================================================================
DEFAULT_EMOJIS = {
    'product_store': '6163205892834598715',
    'profile': '6035084557378654059',
    'add_balance': '5278467510604160626',
    'history': '6160968017304888311',
    'referral': '6032609071373226027',
    'support': '6161112036148255813',
    'ludo_spin': '6147764669361692707',
    'back': '6039539366177541657',
    'upi': '5807750375033278838',
    'binance': '5843689746538173057',
    'reseller': '6120436698695338614',
    'tutorial': '5368653135101310687',
    'download': '6161336001512874965',
    'telegram': '6161096071754818473',
    'whatsapp': '6118193823823698862',
    'welcome': '5312361253610475399',
    'vip': '6086672466132865380',
    'category_android_non_root': '6161172706856282588',
    'category_android_root': '6161449831031118974',
    'category_iphone': '6161399700172840408',
    'category_pc': '5350554349074391003',
    'grid_id': '5474625972751837256',
    'name': '5215399540814781035',
    'account_level': '6129584162992034014',
    'regular_user': '5904630315946611415',
    'wallet': '6210859306602995217',
    'current_balance': '5316711376876485361',
    'global_stats': '6161437856662298090',
    'total_orders': '6160968017304888311',
    'total_spent': '5197503331215361533',
    'total_referrals': '5938196735200333756',
    'joined_grid': '5433614043006903194',
    'info_icon': '6037421444789440735',
    'check_icon': '6161241250239356403',
    'checkbox_icon': '6161437856662298090',
    'shield_icon': '6086672466132865380',
    'money_icon': '5890848474563352982',
    'redeem_icon': '5377624166436445368',
    'wallet_left': '6210859306602995217',
    'wallet_right': '5305699699204837855',
    'point_down': '6161302621027049305',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

def fmt_curr(amount: float) -> str:
    return f"₹{amount:,.2f}"

def gateway_payment_status(payload: Any) -> str:
    """Read the payment status from the different gateway response shapes."""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    candidates = []
    if isinstance(data, dict):
        candidates.append(data)
        for nested_key in ("order", "payment", "transaction"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)
    candidates.append(payload)
    for item in candidates:
        for key in ("status", "payment_status", "order_status", "txn_status"):
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""

def gateway_payment_succeeded(status: Any) -> bool:
    return str(status or "").strip().lower() in {
        "success", "successful", "paid", "completed", "complete",
        "payment_success", "payment successful",
    }

def gateway_payment_pending(status: Any) -> bool:
    return str(status or "").strip().lower() in {
        "", "pending", "processing", "created", "initiated", "in_progress",
    }

def natural_sort_key(value: Any) -> List[Any]:
    """Sort names naturally: A, B, C... and 1, 2, 10 instead of 1, 10, 2."""
    text = str(value or "").strip()
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", text)]

# ==============================================================================
# 2. DATABASE FUNCTIONS
# ==============================================================================
def db_query(query: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = True) -> Any:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    try:
        c.execute(query, params)
        if fetchone:
            res = c.fetchone()
        elif fetchall:
            res = c.fetchall()
        else:
            res = None
        if commit: conn.commit()
        return res
    except Exception as e:
        logger.error(f"DB Error: {e} | Query: {query} | Params: {params}")
        if commit: conn.rollback()
        return None
    finally:
        conn.close()

def db_update_count(query: str, params: tuple = ()) -> int:
    """Execute an UPDATE/DELETE and return the affected row count for CAS/idempotency checks."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.error(f"DB Update Error: {e} | Query: {query} | Params: {params}")
        conn.rollback()
        return 0
    finally:
        conn.close()

def get_setting(key: str, default: str = "") -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return val[0] if val and val[0] else default

def set_setting(key: str, value: str) -> None:
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

def get_owner_id() -> int:
    """Current store owner. Defaults to ADMIN_ID until ownership is transferred
    from the admin panel (Co-Admins & Ownership -> Transfer Ownership)."""
    raw = get_setting("owner_id", "")
    try:
        return int(raw) if raw else ADMIN_ID
    except (TypeError, ValueError):
        return ADMIN_ID

def get_co_admin_ids() -> List[int]:
    """Co-admins get full /admin panel access (products/keys/prices/coupons/
    broadcast/etc.) but can never manage other co-admins or transfer
    ownership — those two actions stay Owner-only."""
    raw = get_setting("co_admin_list", "[]")
    try:
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            return []
        return sorted({int(x) for x in data if str(x).strip().lstrip("-").isdigit()})
    except Exception:
        return []

def set_co_admin_ids(ids: List[int]) -> None:
    set_setting("co_admin_list", json.dumps(sorted({int(i) for i in ids})))

def is_owner_user(user_id: int) -> bool:
    try:
        return int(user_id) == get_owner_id()
    except (TypeError, ValueError):
        return False

def is_admin_user(user_id: int) -> bool:
    """True for the current owner and any co-admin — use this for normal
    admin-panel access checks. Use is_owner_user() instead for the two
    owner-only actions (co-admin management, ownership transfer)."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    return uid == get_owner_id() or uid in get_co_admin_ids()

def gateway_enabled(name: str) -> bool:
    return get_setting(f"{name}_enabled", "ON").upper() == "ON"

def fampay_upi_id() -> str:
    """Return the direct UPI receiver, including legacy database support."""
    return (get_setting("fampay_upi_id", "") or get_setting("payment_upi_id", "")).strip()

async def premium_click_animation(message: Message, title: str) -> None:
    """A short Telegram-native transition before a premium screen opens."""
    for frame in ("✦○○", "✦✦○", "✦✦✦"):
        try:
            await message.edit_text(
                f"✨ <b>{html.escape(title)}</b>\n\n<code>{frame}</code>",
                parse_mode="HTML",
            )
            await asyncio.sleep(0.08)
        except Exception:
            break

_BUTTON_ANIMATION_LABELS = {
    "none": "Off",
    "normal": "Normal",
    "shimmer": "Shimmer",
    "dots": "Loading dots",
    "pulse": "Pulse",
    "scan": "Scanner",
    "matrix": "Matrix",
}

_BUTTON_ANIMATION_FRAMES = {
    "normal": ("⏳", "⏳", "⏳"),
    "shimmer": ("✦○○", "○✦○", "○○✦"),
    "dots": ("●○○", "○●○", "○○●"),
    "pulse": ("◉", "◎", "◉"),
    "scan": ("[▱□□]", "[□▱□]", "[□□▱]"),
    "matrix": ("01 10 01", "10 01 10", "01 01 10"),
}

def _button_animation(callback_data: str) -> str:
    """Return a per-button animation, falling back to the global setting."""
    value = get_setting(f"button_animation_cb_{callback_data}", "").strip().lower()
    if value not in _BUTTON_ANIMATION_LABELS:
        value = get_setting("button_animation", "normal").strip().lower()
    return value if value in _BUTTON_ANIMATION_LABELS else "normal"

def _button_animation_title(callback_data: str) -> str:
    """Find a short readable title for the button being opened."""
    for menu in ("main", "admin"):
        for key, label, callback in _button_catalog(menu):
            if callback == callback_data:
                return label
    return "Please wait"

async def animate_button_press(call: CallbackQuery) -> None:
    """Show a very short Telegram-native click animation before a screen opens."""
    callback_data = str(call.data or "")
    animation = _button_animation(callback_data)
    if animation == "none" or not call.message:
        return
    title = html.escape(_button_animation_title(callback_data))
    for frame in _BUTTON_ANIMATION_FRAMES.get(animation, _BUTTON_ANIMATION_FRAMES["normal"]):
        try:
            await call.message.edit_text(
                f"✨ <b>{title}</b>\n\n<code>{frame}</code>",
                parse_mode="HTML",
            )
            await asyncio.sleep(0.07)
        except Exception:
            break

def log_activity(user_id: int, action: str, details: str = "") -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_query(
            "INSERT INTO activity_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, action, details, timestamp)
        )
    except Exception as e:
        logger.error(f"Failed to log activity: {e}")

def get_emoji(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">✨</tg-emoji>'
    return "✨"

def get_emoji_icon(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    return emoji_id

# ==============================================================================
# 3. STRING RESOURCES – using placeholders for premium emojis
# ==============================================================================
UI_TEXTS = {
    "start_menu": (
        "◆═══════════════════════◆\n"
        "     🎮 <b>NAGESH PANEL</b> 🎮\n"
        "◆═══════════════════════◆\n\n"
        "👋 Hey <b>{name}</b>, welcome back!\n\n"
        "┌─────────────────────────\n"
        "  🆔 <b>ID</b>              <code>{user_id}</code>\n"
        "  💰 <b>Balance</b>      <code>{balance}</code>\n"
        "  🏅 <b>Rank</b>          🟢 <b>{rank}</b>\n"
        "  🎯 <b>Live Products</b>  <code>{cheats} Online</code>\n"
        "└─────────────────────────\n\n"
        "⚡ <b>Instant Auto-Delivery</b>  •  🔒 <b>100% Secure</b>\n\n"
        "👇 <i>Tap a product below to grab your key</i>"
    ),
    "panel_select_menu": (
        "<blockquote><b>🔥 NAGESH PANEL SHOP 🔥</b></blockquote>\n\n"
        "<blockquote>"
        "🚀 <b>Welcome:</b> {name}\n"
        "✅ <b>Account ID:</b> <code>{user_id}</code>\n"
        "💰 <b>Wallet Balance:</b> <code>{balance}</code>\n"
        "❓ <b>Status / Rank:</b> 🟢 <b>{rank}</b>\n"
        "🔫 <b>Available Cheats:</b> <code>{cheats} Online</code>"
        "</blockquote>\n\n"
        "<blockquote>"
        "✨ <b><u>SELECT PRODUCT PANEL</u></b>\n"
        "✨ <i>Choose a panel below to view its packages:</i>"
        "</blockquote>"
    ),
    "download_files": (
        "<blockquote><tg-emoji emoji-id='6091571559233755994'>📥</tg-emoji> "
        "<b>DOWNLOAD FILES</b></blockquote>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<tg-emoji emoji-id='6093677128295914531'>🎉</tg-emoji> "
        "<i>Latest files, features, and stock alerts in one place.</i>\n\n"
        "✔️ Updated APKs and product resources\n"
        "✔️ Secure configs and setup guides\n\n"
        "<tg-emoji emoji-id='6080214566191505147'>🔗</tg-emoji> "
        "<b>Tap below to open the current download channel.</b>"
    ),
    "lucky_dice_result": (
        "{ludo_spin} <b><u>LUCKY DICE RESULT 🔨 💯</u></b>\n\n"
        "🎲 <b>Dice Value:</b> {dice_value}\n\n"
        "💸 <b>You Won:</b> {won_amount}\n"
        "💰 <b>Total Balance:</b> {new_balance}\n\n"
        "Congratulations! Come back after 24 hours."
    ),
    "vip_menu": (
        "🌟 <b><u>VIP MEMBERSHIP CLUB</u></b> 🌟\n\n"
        "Unlock premium benefits and permanent discounts!\n\n"
        "💎 <b>VIP Benefits:</b>\n"
        "• Flat 15% off on ALL products (Stacks with Reseller!)\n"
        "• Priority Support\n"
        "• Exclusive VIP-only giveaways\n\n"
        "💳 <b>VIP Price:</b> ₹299.00 (Lifetime)\n"
        "👤 <b>Your Status:</b> {vip_status}"
    ),
    "add_balance_menu": (
        "{add_balance} <b>ADD BALANCE</b> {info_icon}\n\n"
        "{shield_icon} Secure exact-amount payment {check_icon}\n\n"
        "┣ {upi} Scan the payment QR {checkbox_icon}\n"
        "┣ {check_icon} Automatic payment verification {checkbox_icon}\n"
        "┣ {shield_icon} Balance is credited securely {checkbox_icon}\n\n"
        "{shield_icon} Jitna amount aap type/select karoge, exactly utna hi "
        "amount payment confirm hote hi aapke wallet balance mein add hoga. {check_icon}"
    )
}

def get_ui_text(key: str, **kwargs) -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (f"ui_{key}",), fetchone=True)
    template = val[0] if val and val[0] else UI_TEXTS.get(key, "")

    emoji_map = {
        '{product_store}': get_emoji('product_store'),
        '{profile}': get_emoji('profile'),
        '{add_balance}': get_emoji('add_balance'),
        '{history}': get_emoji('history'),
        '{referral}': get_emoji('referral'),
        '{tutorial}': get_emoji('tutorial'),
        '{support}': get_emoji('support'),
        '{ludo_spin}': get_emoji('ludo_spin'),
        '{download}': get_emoji('download'),
        '{telegram}': get_emoji('telegram'),
        '{whatsapp}': get_emoji('whatsapp'),
        '{upi}': get_emoji('upi'),
        '{binance}': get_emoji('binance'),
        '{info_icon}': get_emoji('info_icon'),
        '{check_icon}': get_emoji('check_icon'),
        '{checkbox_icon}': get_emoji('checkbox_icon'),
        '{shield_icon}': get_emoji('shield_icon'),
        '{money_icon}': get_emoji('money_icon'),
        '{redeem_icon}': get_emoji('redeem_icon'),
        '{wallet_left}': get_emoji('wallet_left'),
        '{wallet_right}': get_emoji('wallet_right'),
        '{point_down}': get_emoji('point_down'),
    }
    for placeholder, emoji_tag in emoji_map.items():
        template = template.replace(placeholder, emoji_tag)

    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"Formatting problem in template {key}: {e!r}; using safe replace")
            for k, v in kwargs.items():
                template = template.replace("{" + str(k) + "}", str(v))
    return template

# ==============================================================================
# 4. DATABASE INITIALISATION & MIGRATION
# ==============================================================================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            phone TEXT, 
            first_name TEXT, 
            username TEXT,
            balance REAL DEFAULT 0.0, 
            account_type TEXT DEFAULT 'Regular', 
            orders_count INTEGER DEFAULT 0, 
            spent REAL DEFAULT 0.0, 
            referrals_count INTEGER DEFAULT 0, 
            referral_earned REAL DEFAULT 0.0, 
            referred_by INTEGER, 
            last_spin TEXT, 
            joined_date TEXT,
            is_reseller INTEGER DEFAULT 0,
            reseller_since TEXT,
            total_saved REAL DEFAULT 0.0,
            is_banned INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            vip_since TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            category TEXT, 
            panel_name TEXT DEFAULT '',
            name TEXT, 
            price_inr REAL, 
            reseller_price REAL DEFAULT 0.0,
            stock INTEGER, 
            apk_link TEXT, 
            validity TEXT DEFAULT 'Lifetime', 
            device_limit TEXT DEFAULT '1 Device',
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            product_id INTEGER, 
            key_text TEXT, 
            is_used INTEGER DEFAULT 0
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            product_name TEXT, 
            price_paid REAL, 
            delivered_key TEXT, 
            purchase_date TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            message TEXT, 
            status TEXT DEFAULT 'Open',
            created_at TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, 
            value TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY, 
            amount REAL, 
            uses_left INTEGER
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS product_coupons (
            code TEXT PRIMARY KEY,
            product_id INTEGER,
            discount_percent REAL,
            uses_left INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS redeemed (
            user_id INTEGER, 
            code TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            order_id TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_inr REAL, 
            status TEXT, 
            timestamp INTEGER
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS purchase_locks (
            user_id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS crypto_txns (
            txid TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_usdt REAL, 
            timestamp INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS spin_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            amount REAL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')

    # Product categories are managed from /admin.  Product rows continue to
    # store the category name for backwards compatibility, while this catalog
    # controls which category buttons are shown in the shop.
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    ''')

    # Optional AI support configuration.  This is additive: old databases do
    # not need any changes to existing users, products, orders, or payments.
    c.execute('''
        CREATE TABLE IF NOT EXISTS ai_api_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            base_url TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
            model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
            api_key TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    ''')

    # API keys issued to other bots.  Only a SHA-256 hash is stored; the
    # plaintext key is shown once, at creation time, from the admin panel.
    c.execute('''
        CREATE TABLE IF NOT EXISTS reseller_api_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_key_hash TEXT NOT NULL UNIQUE,
            api_key_last4 TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0.0,
            is_active INTEGER NOT NULL DEFAULT 1,
            total_spent REAL NOT NULL DEFAULT 0.0,
            total_orders INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS welcome_voice_seen (
            user_id INTEGER PRIMARY KEY,
            sent_at TEXT NOT NULL
        )
    ''')

    # 🏆 LEADERBOARD PINS — admin can manually pin a fake/custom entry at any rank.
    # Real leaderboard is built from users.orders_count; pinned rows override specific positions.
    c.execute('''
        CREATE TABLE IF NOT EXISTS leaderboard_pins (
            rank_position INTEGER PRIMARY KEY,
            display_name  TEXT NOT NULL,
            orders_count  INTEGER NOT NULL DEFAULT 0,
            is_pinned     INTEGER NOT NULL DEFAULT 1
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS source_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            price REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            price REAL NOT NULL,
            gmail TEXT DEFAULT '',
            buyer_user_id TEXT DEFAULT '',
            buyer_username TEXT DEFAULT '',
            bot_username TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    ''')

    migrations = [
        "ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN vip_since TEXT",
        "ALTER TABLE products ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE tickets ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN warnings INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN panel_name TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN external_enabled INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN external_product_id TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN requires_android_id INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN external_duration TEXT DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN purpose TEXT DEFAULT 'wallet'",
        "ALTER TABLE transactions ADD COLUMN product_id INTEGER",
        "ALTER TABLE transactions ADD COLUMN quantity INTEGER DEFAULT 1",
        "ALTER TABLE transactions ADD COLUMN payment_method TEXT DEFAULT 'wallet'",
        "ALTER TABLE transactions ADD COLUMN utr TEXT DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN reviewed_at TEXT DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN reviewed_by INTEGER",
        "ALTER TABLE transactions ADD COLUMN provider_order_id TEXT DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN wallet_used REAL DEFAULT 0",
    ]
    for mig in migrations:
        try: c.execute(mig)
        except sqlite3.OperationalError: pass

    # Backfill the API duration for existing products. The purchase code still
    # has additional fallbacks, so old databases remain compatible.
    try:
        c.execute("UPDATE products SET external_duration = validity WHERE COALESCE(external_duration, '') = ''")
    except sqlite3.OperationalError:
        pass
    
    c.execute("SELECT COUNT(*) FROM spin_rewards")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO spin_rewards (amount) VALUES (?)", [(0.0,), (1.0,), (2.0,), (5.0,), (10.0,)])

    default_settings = [
        ('spin_status', 'ON'),
        ('daily_spin_limit', '50.0'),
        ('reseller_system_status', 'ON'),
        ('bot_status', 'ON'),
        ('how_to_video', 'None'),
        ('all_files_link', 'None'),
        ('zapupi_api', ''),
        ('zapupi_enabled', 'ON'),
        ('payment_upi_id', ''),
        ('fampay_upi_id', ''),
        ('fampay_api_key', ''),
        ('fampay_enabled', 'ON'),
        ('binance_api', ''),
        ('binance_secret', ''),
        ('binance_address', ''),
        ('vip_status', 'OFF'),
        ('reseller_setup_fee', '200.0'),
        ('reseller_min_balance', '500.0'),
        ('migration_done', '0'),
        ('support_telegram', configured_support_url()),
        ('support_whatsapp', 'https://wa.me/YOUR_NUMBER'),
        ('ui_start_menu', UI_TEXTS['start_menu']),
        ('ui_download_files', UI_TEXTS['download_files']),
        ('ui_lucky_dice_result', UI_TEXTS['lucky_dice_result']),
        ('ui_vip_menu', UI_TEXTS['vip_menu']),
        ('ui_add_balance_menu', UI_TEXTS['add_balance_menu']),
        ('external_api_url', 'https://adminpanels.shop/api/reseller_v1.php'),
        ('external_api_key', ''),
        ('external_master_key', ''),
        ('reseller_api_status', 'OFF'),
        ('start_alert_status', 'ON'),
        ('source_sale_status', 'OFF'),
        ('source_sale_title', 'Bot Source Code'),
        ('source_sale_desc', 'Complete Telegram shop bot source code.'),
        ('source_sale_price', '0'),
        ('source_sale_file_id', ''),
        ('source_sale_file_name', ''),
        ('bot_sale_status', 'OFF'),
        ('bot_sale_title', 'Buy This Bot'),
        ('bot_sale_desc', 'Get your own copy of this bot fully set up and ready to use.'),
        ('bot_sale_price', '0'),
        ('reseller_api_public_url', ''),
        ('reseller_api_port', ''),
        ('reseller_api_min_price', '1.0'),
        ('button_color', 'primary'),
        ('button_layout', '2'),
        ('button_animation', 'normal'),
        ('ai_support_status', 'OFF'),
        ('ai_active_api_id', ''),
        ('ai_system_prompt', 'You are a helpful customer support assistant. Answer clearly and briefly. If you do not know, ask the user to open a support ticket.'),
    ]
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        default_settings.append((f"emoji_{slot}", emoji_id))
    
    for key, val in default_settings:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    # Older builds called the FamPay receiver `payment_upi_id`. Preserve it
    # during the upgrade so an existing QR setup keeps working.
    old_qr = c.execute(
        "SELECT value FROM settings WHERE key='payment_upi_id'"
    ).fetchone()
    fam_qr = c.execute(
        "SELECT value FROM settings WHERE key='fampay_upi_id'"
    ).fetchone()
    if old_qr and old_qr[0] and (not fam_qr or not fam_qr[0]):
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('fampay_upi_id', ?)",
            (old_qr[0],),
        )

    # Seed the four original panel buttons once.  New buttons are added from
    # the admin panel and are deliberately not hard-coded here.
    for sort_order, category in enumerate(FIXED_CATEGORIES):
        c.execute(
            "INSERT OR IGNORE INTO product_categories (name, sort_order, is_active) VALUES (?, ?, 1)",
            (category, sort_order),
        )

    # Keep categories from an older database visible so products are never
    # orphaned during an upgrade.  The admin can rename or delete empty ones.
    existing_categories = c.execute(
        "SELECT DISTINCT TRIM(category) FROM products "
        "WHERE category IS NOT NULL AND TRIM(category) != ''"
    ).fetchall()
    next_order = c.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM product_categories"
    ).fetchone()[0]
    for (category,) in existing_categories:
        c.execute(
            "INSERT OR IGNORE INTO product_categories (name, sort_order, is_active) VALUES (?, ?, 1)",
            (str(category).strip(), next_order),
        )
        next_order += 1

    # When the configured owner changes, make the source configuration the
    # authority for the support link instead of inheriting the old database's
    # contact. After this one-time migration, the admin panel can edit it.
    owner_marker = f"{ADMIN_ID}:{BOT_USERNAME}"
    previous_owner = c.execute(
        "SELECT value FROM settings WHERE key='owner_config_marker'"
    ).fetchone()
    if not previous_owner or previous_owner[0] != owner_marker:
        c.execute(
            "UPDATE settings SET value=? WHERE key='support_telegram'",
            (configured_support_url(),),
        )
        c.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('owner_config_marker', ?)",
            (owner_marker,),
        )

    conn.commit()
    conn.close()

def migrate_categories() -> None:
    done = get_setting("migration_done", "0")
    
    # Apply the restyled defaults once. After this marker is set, text changed
    # from the admin panel must survive restarts and redeploys.
    logger.info("Checking emoji and UI text defaults...")
    
    # Update all emoji settings
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        set_setting(f"emoji_{slot}", emoji_id)
    
    if get_setting("ui_style_v2", "0") != "1":
        set_setting("ui_start_menu", UI_TEXTS['start_menu'])
        set_setting("ui_add_balance_menu", UI_TEXTS['add_balance_menu'])
        set_setting("ui_download_files", UI_TEXTS['download_files'])
        set_setting("ui_lucky_dice_result", UI_TEXTS['lucky_dice_result'])
        set_setting("ui_vip_menu", UI_TEXTS['vip_menu'])
        set_setting("ui_style_v2", "1")
    # Start screen redesign (GIF + blockquote card + product buttons). Applied once
    # so the old saved start text in the database is replaced by the new layout.
    if get_setting("ui_start_screen_v3", "0") != "1":
        set_setting("ui_start_menu", UI_TEXTS['start_menu'])
        set_setting("ui_start_screen_v3", "1")
    # Stylish welcome-card redesign (arrows + divider header). Applied once so
    # the previously saved start text in the database is replaced.
    if get_setting("ui_start_screen_v4", "0") != "1":
        set_setting("ui_start_menu", UI_TEXTS['start_menu'])
        set_setting("ui_start_screen_v4", "1")
    # Hide implementation/provider names from the public Add Balance screen.
    # Admin gateway setup screens intentionally keep the real provider names.
    if get_setting("generic_payment_ui_v1", "0") != "1":
        set_setting("ui_add_balance_menu", UI_TEXTS["add_balance_menu"])
        set_setting("generic_payment_ui_v1", "1")
    # Remove the imported previous-owner channel once. The admin can set a
    # replacement later from the Download Files admin control.
    if get_setting("download_link_reset_v2", "0") != "1":
        set_setting("all_files_link", "None")
        set_setting("download_link_reset_v2", "1")
    logger.info("UI texts and emojis updated with new placeholders and IDs.")
    
    if done == "1":
        return
    
    logger.info("Running category migration...")
    
    mapping = {
        "android non root panel": "ANDROID NON ROOT PANEL",
        "android root panel": "ANDROID ROOT PANEL",
        "iphone panel": "IPHONE PANEL",
        "pc panel": "PC PANEL",
        "guild calory credit": "GUILD CALORY CREDIT",
        "carrom panel": "CARROM PANEL",
    }
    for old, new in mapping.items():
        db_query("UPDATE products SET category = ? WHERE LOWER(category) = ?", (new, old))
    
    # The category catalog is created in init_db.  Do not force unknown
    # categories into a hard-coded bucket: admins can now manage them in the
    # bot itself.
    set_setting("migration_done", "1")
    logger.info("Category migration complete.")

# ==============================================================================
# 5. MIDDLEWARES & SECURITY
# ==============================================================================
async def hacker_loading(message: Message, text: str = "Decrypting Data") -> Message:
    msg = await message.answer(f"⚡ {text}\n[□□□] 0%")
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■□□] 33%", parse_mode='HTML')
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■■□] 66%", parse_mode='HTML')
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■■■] 100%", parse_mode='HTML')
    return msg

class GlobalSecurityMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_action_times = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        now = time.time()
        if user_id in self.last_action_times:
            if now - self.last_action_times[user_id] < 0.3:
                if isinstance(event, CallbackQuery):
                    try: await event.answer()
                    except Exception: pass
                return
        self.last_action_times[user_id] = now

        if not is_admin_user(user_id):
            user_info = db_query("SELECT is_banned FROM users WHERE user_id=?", (user_id,), fetchone=True)
            if user_info and user_info[0] == 1:
                msg = "🚫 <b>ACCESS DENIED</b>\nYou have been banned from using this bot.\nContact support if you think this is a mistake."
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await event.answer(msg, show_alert=True)
                return
                
            status_check = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
            status = status_check[0] if status_check else 'ON'
            if status == 'OFF':
                msg = "⚠️ <b>Store Maintenance</b>\n\nThe store is currently offline for updates. Please check back later!"
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await event.answer("⚠️ Bot is currently OFF for Maintenance.", show_alert=True)
                return

        if isinstance(event, CallbackQuery) and not str(event.data or "").startswith("admin_design_"):
            await animate_button_press(event)

        try:
            result = await handler(event, data)
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if "message is not modified" in err:
                result = None
            else:
                logger.exception("Handler error for user %s: %s", user_id, exc)
                await self._report_error(event, exc)
                return
        except Exception as exc:
            logger.exception("Handler error for user %s: %s", user_id, exc)
            await self._report_error(event, exc)
            return
        # Buttons must never keep spinning: acknowledge if the handler forgot to.
        if isinstance(event, CallbackQuery):
            try: await event.answer()
            except Exception: pass
        return result

    async def _report_error(self, event, exc):
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("⚠️ Kuch error aaya, dobara try karo.", show_alert=False)
            elif isinstance(event, Message):
                if is_admin_user(event.from_user.id):
                    await event.answer(f"⚠️ Error: <code>{html.escape(type(exc).__name__ + ': ' + str(exc))[:500]}</code>", parse_mode="HTML")
                else:
                    await event.answer("⚠️ Kuch error aaya, please dobara try karo.")
        except Exception:
            pass

dp.message.middleware(GlobalSecurityMiddleware())
dp.callback_query.middleware(GlobalSecurityMiddleware())

# ------------------------------------------------------------------------------
# GIF/caption-safe edit_text
# The /start screen is now a GIF with a caption. Telegram refuses edit_text on
# such messages ("there is no text in the message to edit"), and this bot calls
# edit_text from 150+ handlers. Instead of touching each one, edit_text is made
# tolerant: on a media message it sends the new screen as a normal text message,
# removes the old GIF message, and remembers the replacement so any later edit of
# the same (old) message is redirected to the new one.
# ------------------------------------------------------------------------------
_MEDIA_REDIRECT: Dict[Tuple[int, int], Message] = {}
def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(text or "")))

_orig_message_answer = Message.answer

async def _answer_parse_safe(self: Message, text: str, *args, **kwargs):
    try:
        return await _orig_message_answer(self, text, *args, **kwargs)
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "can't parse entities" in err or "can not parse entities" in err:
            logger.warning("answer HTML parse failed (%s); resending as plain text.", exc)
            plain_kwargs = dict(kwargs); plain_kwargs["parse_mode"] = None
            return await _orig_message_answer(self, _strip_html(text), *args, **plain_kwargs)
        raise

Message.answer = _answer_parse_safe

_orig_message_edit_text = Message.edit_text

async def _edit_text_media_safe(self: Message, text: str, *args, **kwargs):
    key = (self.chat.id, self.message_id)
    target = _MEDIA_REDIRECT.get(key)
    if target is not None:
        return await target.edit_text(text, *args, **kwargs)
    try:
        return await _orig_message_edit_text(self, text, *args, **kwargs)
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            return self
        if "can't parse entities" in err or "can not parse entities" in err:
            logger.warning("edit_text HTML parse failed (%s); retrying as plain text.", exc)
            plain_kwargs = dict(kwargs); plain_kwargs["parse_mode"] = None
            return await _orig_message_edit_text(self, _strip_html(text), *args, **plain_kwargs)
        if "there is no text in the message to edit" not in err:
            raise
    answer_kwargs = {
        k: v for k, v in kwargs.items()
        if k in ("parse_mode", "entities", "link_preview_options", "reply_markup", "disable_web_page_preview")
    }
    new_message = await self.answer(text, **answer_kwargs)
    try:
        await self.delete()
    except Exception:
        pass
    _MEDIA_REDIRECT[key] = new_message
    while len(_MEDIA_REDIRECT) > 500:
        _MEDIA_REDIRECT.pop(next(iter(_MEDIA_REDIRECT)))
    return new_message

Message.edit_text = _edit_text_media_safe

# ==============================================================================
# 6. FSM STATES
# ==============================================================================
class UserStates(StatesGroup):
    wait_for_ticket = State()
    wait_for_redeem = State()
    wait_for_crypto_txid = State()
    wait_for_product_binance_txid = State()
    custom_amount_input = State()
    ai_support_chat = State()

class AdminStates(StatesGroup):
    wait_button_label = State()
    wait_custom_link = State()
    add_prod_category = State()
    add_prod_panel_name = State()
    add_prod_name = State()
    add_prod_validity = State()
    add_prod_device_limit = State()
    add_prod_price = State()
    add_prod_reseller_price = State()
    add_prod_apk = State()
    add_prod_keys = State()
    
    edit_prod_field = State()
    wait_for_new_value = State()
    wait_for_add_keys = State()
    wait_for_delete_key = State()
    
    broadcast_msg = State()
    add_coupon_code = State()
    add_coupon_amount = State()
    add_coupon_uses = State()
    add_prod_coupon_code = State()
    add_prod_coupon_percent = State()
    add_prod_coupon_uses = State()
    wait_for_buy_coupon_code = State()
    
    wait_for_zapupi_api = State()
    wait_for_payment_upi = State()
    wait_for_fampay_api_key = State()
    wait_for_binance_api = State()
    wait_for_binance_secret = State()
    wait_for_binance_address = State()
    wait_for_buy_gif = State()
    wait_for_fampay_debug_order = State()
    
    ticket_reply_msg = State()
    reseller_manage_id = State()
    manage_target_user = State()
    wait_for_add_money = State()
    wait_for_minus_money = State()
    wait_bulk_deduct_amount = State()
    wait_for_store_media = State()
    wait_for_warning = State()
    
    spin_add_reward = State()
    spin_set_limit = State()
    wait_for_howto_video = State()
    wait_for_all_files_link = State()
    
    edit_ui_text = State()
    edit_reseller_price = State()
    wait_for_reseller_setup_fee = State()
    wait_for_reseller_min_balance = State()
    confirm_ban = State()
    
    wait_for_support_telegram = State()
    wait_for_support_whatsapp = State()
    wait_for_category_emoji = State()
    wait_for_panel_emoji_id = State()
    wait_for_panel_video = State()
    wait_for_quick_api_batch = State()
    wait_for_quick_api_price_pid = State()
    wait_for_quick_api_custom_duration = State()
    wait_for_quick_api_discount_override = State()
    wait_for_quick_api_profit = State()
    wait_for_quick_reseller_discount = State()
    wait_for_welcome_voice = State()
    wait_for_emoji_slot = State()
    wait_for_ext_url = State()
    wait_for_ext_key = State()
    wait_for_ext_master = State()
    add_prod_external = State()
    add_prod_external_product_id = State()
    add_prod_external_duration = State()
    wait_for_manual_key_pid = State()
    wait_for_manual_key_duration = State()
    wait_for_manual_key_android = State()
    add_category_name = State()
    rename_category_name = State()

    wait_for_coadmin_add = State()
    wait_for_new_owner_id = State()
    wait_for_new_bot_token = State()
    wait_for_token_change_owner_id = State()

    ai_bulk_add_panel_name = State()
    ai_bulk_add_screenshot = State()
    ai_bulk_item_price = State()
    ai_bulk_item_reseller_price = State()
    
    ai_add_name = State()
    ai_add_url = State()
    ai_add_model = State()
    ai_add_key = State()
    ai_update_key = State()
    ai_edit_prompt = State()

    # Public reseller API management
    api_client_name = State()
    api_client_balance = State()
    source_title = State()
    source_price = State()
    source_desc = State()
    source_file = State()
    api_set_url = State()
    api_set_port = State()
    api_set_min_price = State()

# ==============================================================================
# 7. KEYBOARDS
# ==============================================================================
def get_category_emoji(category: str) -> str:
    slot_map = {
        "ANDROID NON ROOT PANEL": "category_android_non_root",
        "ANDROID ROOT PANEL": "category_android_root",
        "IPHONE PANEL": "category_iphone",
        "PC PANEL": "category_pc",
        "GUILD CALORY CREDIT": "product_store",
        "CARROM PANEL": "product_store",
    }
    custom = get_setting(f"cat_emoji_{category}", "")
    if custom.isdigit():
        return custom
    slot = slot_map.get(category)
    if slot:
        return get_emoji_icon(slot, DEFAULT_EMOJIS.get(slot, ""))
    return ""

def get_shop_categories() -> List[Tuple[int, str]]:
    """Return active category buttons in the order chosen by the admin."""
    rows = db_query(
        "SELECT id, name FROM product_categories "
        "WHERE is_active=1 ORDER BY sort_order, name COLLATE NOCASE",
        fetchall=True,
        commit=False,
    ) or []
    return [(int(row[0]), str(row[1]).strip()) for row in rows if str(row[1]).strip()]

def get_panel_emoji(panel_name: str) -> str:
    stored = get_setting(f"panel_emoji_{panel_name}", "")
    if stored and stored.isdigit():
        return stored
    return get_emoji_icon("product_store")

def get_panel_video(panel_name: str) -> str:
    """Gameplay video (file_id) shown above the plan list for this panel, if set."""
    return get_setting(f"panel_video_{panel_name}", "").strip()

def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Verify Contact", request_contact=True)]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )

def main_menu_kb(user_id: Optional[int] = None, exclude_shop: bool = False) -> InlineKeyboardMarkup:
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else 'ON'
    vip_sys_check = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    vip_system = vip_sys_check[0] if vip_sys_check else 'OFF'
    
    is_reseller = False
    if user_id:
        user_check = db_query("SELECT is_reseller FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if user_check:
            is_reseller = bool(user_check[0])

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    if not exclude_shop:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text="Shop Now", callback_data="menu_shop",
                icon_custom_emoji_id=get_emoji_icon("product_store"),
                style="primary"
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="My Profile", callback_data="menu_profile",
            icon_custom_emoji_id=get_emoji_icon("profile"),
            style="primary"
        ),
        InlineKeyboardButton(
            text="Add Balance", callback_data="menu_add_balance",
            icon_custom_emoji_id=get_emoji_icon("add_balance"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="My Keys", callback_data="menu_orders",
            icon_custom_emoji_id=get_emoji_icon("history"),
            style="primary"
        ),
        InlineKeyboardButton(
            text="How to use", callback_data="menu_how_to",
            icon_custom_emoji_id=get_emoji_icon("tutorial"),
            style="danger"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Download Files", callback_data="menu_all_files",
            icon_custom_emoji_id=get_emoji_icon("download"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Support", callback_data="menu_support",
            icon_custom_emoji_id=get_emoji_icon("support"),
            style="danger"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Daily Gift", callback_data="menu_spin_landing",
            icon_custom_emoji_id=get_emoji_icon("ludo_spin"),
            style="success"
        ),
        InlineKeyboardButton(text="Referral", callback_data="menu_referral",
            icon_custom_emoji_id=get_emoji_icon("referral"), style="success")
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="🏆 Top Buyers Leaderboard",
            callback_data="menu_leaderboard",
            style="primary"
        )
    ])
    
    extras_row = []
    if sys_status == 'ON' or is_reseller:
        extras_row.append(InlineKeyboardButton(
            text="Reseller Panel", callback_data="menu_reseller_dash",
            icon_custom_emoji_id=get_emoji_icon("reseller"),
            style="success"
        ))
    if vip_system == 'ON':
        extras_row.append(InlineKeyboardButton(
            text="VIP Club", callback_data="menu_vip_dash",
            icon_custom_emoji_id=get_emoji_icon("vip"),
            style="success"
        ))
    if extras_row:
        kb.inline_keyboard.append(extras_row)

    if get_setting("source_sale_status", "OFF") == "ON":
        kb.inline_keyboard.append([InlineKeyboardButton(text="💻 Buy Source Code", callback_data="menu_source_code", style="primary")])

    if get_setting("bot_sale_status", "OFF") == "ON":
        kb.inline_keyboard.append([InlineKeyboardButton(text="🤖 Buy This Bot", callback_data="menu_bot_sale", style="success")])

    for link in _custom_links():
        kb.inline_keyboard.append([InlineKeyboardButton(text=link["label"], url=link["url"], style="success")])

    return apply_button_theme(kb, "main")

def back_kb(callback: str = "back_main") -> InlineKeyboardMarkup:
    return apply_button_theme(InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="BACK", callback_data=callback,
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )
        ]]
    ))

def admin_kb() -> InlineKeyboardMarkup:
    status = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    status_val = status[0] if status else 'ON'
    vip_status = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    vip_val = vip_status[0] if vip_status else 'OFF'
    alert_val = get_setting("start_alert_status", "ON")
    src_val = get_setting("source_sale_status", "OFF")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin_view_stats", icon_custom_emoji_id=get_emoji_icon("global_stats"), style="success")],
        [InlineKeyboardButton(text="👥 User Control Panel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("profile"), style="success")],
        [InlineKeyboardButton(text="💸 Deduct All Users Balance", callback_data="admin_bulk_deduct_start", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="danger")],
        [InlineKeyboardButton(text="🛡️ Advanced Management", callback_data="admin_advanced_management", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="🧰 BOT SYSTEM Tools", callback_data="admin_bot_system_tools", icon_custom_emoji_id=get_emoji_icon("global_stats"), style="primary")],
        [
            InlineKeyboardButton(text="➕ Add Product", callback_data="admin_add_prod", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success"),
            InlineKeyboardButton(text="📦 Manage Products", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success")
        ],
        [
            InlineKeyboardButton(text="📸 AI Add Products (Screenshot)", callback_data="admin_ai_bulk_add", icon_custom_emoji_id=get_emoji_icon("product_store"), style="primary")
        ],
        [
            InlineKeyboardButton(text="🆔 Product IDs", callback_data="admin_view_product_ids", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")
        ],
        [
            InlineKeyboardButton(text="➕ Add Product (API)", callback_data="admin_add_prod_api", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success"),
            InlineKeyboardButton(text="🎯 Get Key via API", callback_data="admin_manual_get_key", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")
        ],
        [
            InlineKeyboardButton(text="🔑 Add Keys to Existing", callback_data="admin_quick_add_keys", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"),
            InlineKeyboardButton(text="⚡ Quick Add API Key", callback_data="admin_quick_add_api", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")
        ],
        [
            InlineKeyboardButton(text="🗂 Manage Categories", callback_data="admin_manage_categories", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")
        ],
        [
            InlineKeyboardButton(text="👑 Reseller Mgmt", callback_data="admin_reseller_menu", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success"),
            InlineKeyboardButton(text="🎰 Spin Settings", callback_data="admin_spin_menu", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="success")
        ],
        [
            InlineKeyboardButton(text="🌐 Reseller API", callback_data="admin_reseller_api_menu", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success"),
            InlineKeyboardButton(text="🎨 Button Design", callback_data="admin_button_design", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")
        ],
        [
            InlineKeyboardButton(text="🎟 Create Coupon", callback_data="admin_create_coupon", icon_custom_emoji_id=get_emoji_icon("redeem_icon"), style="success"),
            InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast_btn", icon_custom_emoji_id=get_emoji_icon("telegram"), style="success")
        ],
        [
            InlineKeyboardButton(text="🎫 View Tickets", callback_data="admin_view_tickets", icon_custom_emoji_id=get_emoji_icon("support"), style="success"),
            InlineKeyboardButton(text="📹 Tutorial Video", callback_data="admin_set_video", icon_custom_emoji_id=get_emoji_icon("tutorial"), style="success")
        ],
        [
            InlineKeyboardButton(text="🔗 All Files Link", callback_data="admin_set_all_files", icon_custom_emoji_id=get_emoji_icon("download"), style="success"),
            InlineKeyboardButton(text="🎨 Edit All Emojis", callback_data="admin_edit_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="🖼 Store GIF / Video", callback_data="admin_set_store_media", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success")
        ],
        [
            InlineKeyboardButton(text="⚙️ ZapUPI Setup", callback_data="admin_setup_zapupi", icon_custom_emoji_id=get_emoji_icon("upi"), style="success"),
            InlineKeyboardButton(text="🪙 Binance Setup", callback_data="admin_setup_binance", icon_custom_emoji_id=get_emoji_icon("binance"), style="success")
        ],
        [
            InlineKeyboardButton(text="💳 Payment Gateways", callback_data="admin_payment_gateways", icon_custom_emoji_id=get_emoji_icon("upi"), style="success"),
            InlineKeyboardButton(text="🧾 FamPay QR Setup", callback_data="admin_setup_fampay", icon_custom_emoji_id=get_emoji_icon("upi"), style="primary")
        ],
        [
            InlineKeyboardButton(text="🔗 External Key API", callback_data="admin_setup_external_api", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="🔗 Multi API Manager", callback_data="admin_multi_api_manager", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success"),
            InlineKeyboardButton(text="🤖 AI API Paste", callback_data="admin_ai_api_paste", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="💬 AI Support Setup", callback_data="admin_ai_support_setup", icon_custom_emoji_id=get_emoji_icon("support"), style="success")
        ],
        [
            InlineKeyboardButton(text="✏️ Edit UI Texts", callback_data="admin_edit_ui_menu", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success"),
            InlineKeyboardButton(text="📝 Edit Reseller Price", callback_data="admin_edit_reseller_price", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="💰 Reseller Fee", callback_data="admin_set_reseller_fee", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success"),
            InlineKeyboardButton(text="💳 Min Balance", callback_data="admin_set_reseller_min", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="🏷 Quick-Add Reseller Discount %", callback_data="admin_set_quick_reseller_discount", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="📞 Set Support Links", callback_data="admin_set_support_links", icon_custom_emoji_id=get_emoji_icon("support"), style="success"),
            InlineKeyboardButton(text="🎨 Set Category Emojis", callback_data="admin_set_category_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="🖼 Set Panel Emojis", callback_data="admin_set_panel_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success"),
            InlineKeyboardButton(text="🎬 Set Panel Video", callback_data="admin_set_panel_videos", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(
                text=f"Bot Status: {status_val} {'🟢' if status_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_bot",
                icon_custom_emoji_id=get_emoji_icon("check_icon"),
                style="success" if status_val == 'ON' else "danger"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"VIP System: {vip_val} {'🟢' if vip_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_vip_sys",
                icon_custom_emoji_id=get_emoji_icon("vip"),
                style="success" if vip_val == 'ON' else "danger"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🔔 Start Alerts: {alert_val} {'🟢' if alert_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_start_alerts",
                style="success" if alert_val == 'ON' else "danger"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💻 Source Sale: {src_val} {'🟢' if src_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_source_sale",
                style="success" if src_val == 'ON' else "danger"
            ),
            InlineKeyboardButton(text="⚙️ Source Settings", callback_data="admin_source_menu", style="primary")
        ],
        [InlineKeyboardButton(text="🏆 Leaderboard Override", callback_data="admin_leaderboard_override", style="success")],
        [InlineKeyboardButton(text="🛠 Product Maintenance", callback_data="admin_maint_categories", icon_custom_emoji_id=get_emoji_icon("product_store"), style="danger")],
        [InlineKeyboardButton(text="🤖 Bot Sale Settings", callback_data="admin_bot_sale_menu", style="success")],
        [InlineKeyboardButton(text="🛡️ Co-Admins & Ownership", callback_data="admin_owner_menu", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="primary")],
        [InlineKeyboardButton(text="🔑 Change Bot Token", callback_data="admin_change_bot_token", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="danger")]
    ])
    return apply_button_theme(kb, "admin")

def admin_back_kb() -> InlineKeyboardMarkup:
    return apply_button_theme(InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Back to Admin", callback_data="admin_panel_back",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger"
        )
    ]]))

# ==============================================================================
# 8. NOTIFICATIONS
# ==============================================================================
async def send_advanced_notification(user_id: int, notif_type: str, amount: float, product: str = None, key: str = None, gateway: str = "ZapUPI") -> None:
    user_info = db_query("SELECT first_name, phone, username, is_reseller, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    username = f"@{user_info[2]}" if user_info and user_info[2] else "None"
    
    tags = []
    if user_info and user_info[3]: tags.append("👑 Reseller")
    if user_info and user_info[4]: tags.append("🌟 VIP")
    tag_str = " | ".join(tags) if tags else "👤 Regular"
        
    time_now = datetime.now().strftime("%d-%m-%Y %I:%M %p")
    
    if notif_type == "ORDER":
        title = "🛒 <b>NEW ORDER PROCESSED!</b> 🛒"
        details = (f"📦 <b>Product:</b> {product}\n🔑 <b>Key:</b> <code>{key}</code>\n💰 <b>Amount Paid:</b> ₹{amount:.2f}\n📅 <b>Time:</b> {time_now}")
    else:
        title = "💰 <b>NEW WALLET DEPOSIT!</b> 💰"
        details = (f"💵 <b>Amount Added:</b> ₹{amount:.2f}\n🧾 <b>Gateway:</b> {gateway}\n🆔 <b>Reference:</b> <code>{product}</code>\n📅 <b>Time:</b> {time_now}")

    msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n👤 <b>Name:</b> {name}\n🆔 <b>User ID:</b> <code>{user_id}</code>\n📱 <b>Phone:</b> {phone}\n🔗 <b>Username:</b> {username}\n🏷 <b>Status:</b> {tag_str}\n━━━━━━━━━━━━━━━━━━\n{details}"
    try: 
        await bot.send_message(get_owner_id(), msg, parse_mode='HTML')
    except Exception as e: 
        logger.error(f"Failed to send admin notification: {e}")

# ==============================================================================
# 9. PAYMENT VERIFIER
# ==============================================================================
async def run_payment_verification(
    user_id: int,
    order_id: str,
    reply_target: Any = None,
    background: bool = False,
) -> bool:
    txn = db_query("SELECT amount_inr, status, timestamp, purpose, payment_method FROM transactions WHERE order_id=? AND user_id=?", (order_id, user_id), fetchone=True)
    if not txn:
        err = "❌ Invalid or fake Order ID detected in system!"
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
            elif reply_target: await reply_target.answer(err)
        return False
    amount, status, ts, purpose, payment_method = txn
    if payment_method == "fampay":
        if not background:
            msg = "💳 Payment is verified automatically. Please wait a few seconds."
            if isinstance(reply_target, CallbackQuery):
                await reply_target.answer(msg, show_alert=True)
            elif reply_target:
                await reply_target.answer(msg)
        return False
    if status in ('completed', 'processing', 'paid') and purpose == 'product':
        if status == 'paid':
            await fulfill_product_transaction(order_id, user_id, reply_target.message if isinstance(reply_target, CallbackQuery) else reply_target)
        else:
            msg = "⏳ This product order is already being processed. Please wait for the key."
            if not background:
                if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
                elif reply_target: await reply_target.answer(msg)
        return True
    if status == 'paid':
        msg = "✅ This payment has already been securely credited to your wallet."
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
            elif reply_target: await reply_target.answer(msg)
        return True
    if status == 'expired':
        msg = "❌ This order has expired. Please create a new payment request."
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
            elif reply_target: await reply_target.answer(msg)
        return False
    if time.time() - ts > PAYMENT_TIMEOUT_SECONDS and status == 'pending':
        if db_update_count("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,)) and purpose == 'product':
            refund_reserved_wallet(order_id)
        if purpose == 'product': release_purchase_lock(user_id)
        err_msg = "⏳ <b>Payment Timed Out!</b>\nThe 15-minute payment window has expired."
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.message.edit_text(err_msg, reply_markup=back_kb("menu_shop" if purpose == 'product' else "menu_add_balance"), parse_mode='HTML')
            elif reply_target: await reply_target.answer(err_msg, reply_markup=back_kb("menu_shop" if purpose == 'product' else "menu_add_balance"))
        return False

    api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
    if not api_key_check or not api_key_check[0]:
        msg = "⚠️ Secure payment is temporarily unavailable. Please try again later."
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
            elif reply_target: await reply_target.answer(msg)
        return False
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            async with session.post("https://pay.zapupi.com/api/order-status", json={"zap_key": api_key_check[0], "order_id": order_id}) as resp:
                res_json = await resp.json(content_type=None) if resp.status == 200 else {}
        if not isinstance(res_json, dict) or str(res_json.get("status", "")).strip().lower() != "success":
            gateway_message = res_json.get("message", "Unknown Error") if isinstance(res_json, dict) else "Invalid gateway response"
            logger.warning("Payment status request failed: %s", gateway_message)
            err = "⚠️ Payment status is temporarily unavailable. Please try again."
            if not background:
                if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
                elif reply_target: await reply_target.answer(err)
            return False
        real_status = gateway_payment_status(res_json)
        if gateway_payment_succeeded(real_status):
            claimed = db_update_count("UPDATE transactions SET status='paid' WHERE order_id=? AND status='pending'", (order_id,))
            if not claimed:
                latest = db_query("SELECT status,purpose FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
                if latest and latest[1] == 'product' and latest[0] in ('paid','processing','completed'):
                    if isinstance(reply_target, CallbackQuery): await reply_target.answer("⏳ Payment already accepted; your key is being prepared.", show_alert=True)
                    return await fulfill_product_transaction(order_id, user_id, reply_target.message)
                if isinstance(reply_target, CallbackQuery):
                    await reply_target.answer("⏳ Payment was already processed.", show_alert=True)
                return True
            if purpose == 'product':
                if background:
                    msg = await bot.send_message(
                        user_id,
                        "✨ <b>AUTO-VERIFIED!</b>\n\nPayment received. Preparing your keys...",
                        parse_mode='HTML',
                    )
                    await fulfill_product_transaction(order_id, user_id, msg)
                else:
                    await fulfill_product_transaction(order_id, user_id, reply_target.message if isinstance(reply_target, CallbackQuery) else reply_target)
            else:
                db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
                success_msg = f"🎉 <b>VERIFICATION SUCCESSFUL!</b>\n\n✅ {fmt_curr(amount)} has been added to your wallet securely."
                if background:
                    await bot.send_message(user_id, success_msg, reply_markup=main_menu_kb(user_id), parse_mode='HTML')
                elif isinstance(reply_target, CallbackQuery):
                    await reply_target.message.edit_text(success_msg, reply_markup=back_kb(), parse_mode='HTML')
                elif reply_target:
                    await reply_target.answer(success_msg, reply_markup=back_kb())
                await send_advanced_notification(user_id, "DEPOSIT", amount, product=order_id, gateway="ZapUPI")
                log_activity(user_id, "DEPOSIT_SUCCESS", f"Amount: {amount}, Gateway: ZapUPI, Order: {order_id}")
            return True
        elif gateway_payment_pending(real_status):
            fail_msg = "⏳ Payment is still Pending. Please wait a moment and verify again."
            if not background:
                if isinstance(reply_target, CallbackQuery): await reply_target.answer(fail_msg, show_alert=True)
                elif reply_target: await reply_target.answer(fail_msg)
        else:
            fail_msg = "❌ Payment failed or was cancelled. Please create a new payment request."
            if not background:
                if isinstance(reply_target, CallbackQuery): await reply_target.answer(fail_msg, show_alert=True)
                elif reply_target: await reply_target.answer(fail_msg)
        return False
    except Exception:
        logger.exception("ZapUPI verification error")
        err = "⚠️ Unable to connect to payment gateway right now."
        if not background:
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
            elif reply_target: await reply_target.answer(err)
        return False

def schedule_auto_verification(user_id: int, order_id: str, payment_method: str) -> None:
    """Start one immediate watcher per order; duplicate watchers are ignored."""
    current = _auto_verify_tasks.get(order_id)
    if current and not current.done():
        return
    task = asyncio.create_task(
        _watch_payment_until_complete(user_id, order_id, payment_method)
    )
    _auto_verify_tasks[order_id] = task

    def cleanup(done_task: asyncio.Task) -> None:
        if _auto_verify_tasks.get(order_id) is done_task:
            _auto_verify_tasks.pop(order_id, None)

    task.add_done_callback(cleanup)


async def _watch_payment_until_complete(
    user_id: int,
    order_id: str,
    payment_method: str,
) -> None:
    """Poll the gateway immediately after payment creation until it settles."""
    while True:
        txn = db_query(
            "SELECT amount_inr,status,timestamp,purpose FROM transactions "
            "WHERE order_id=? AND user_id=?",
            (order_id, user_id),
            fetchone=True,
            commit=False,
        )
        if not txn:
            return
        amount, status, timestamp, purpose = txn
        if status != "pending":
            return
        if time.time() - timestamp > PAYMENT_TIMEOUT_SECONDS:
            if db_update_count(
                "UPDATE transactions SET status='expired' "
                "WHERE order_id=? AND status='pending'",
                (order_id,),
            ):
                if purpose == "product":
                    refund_reserved_wallet(order_id)
                    release_purchase_lock(user_id)
                    await _drop_qr_message(order_id)
                try:
                    await bot.send_message(
                        user_id,
                        f"⏳ <b>Order Expired!</b>\nYour payment window for order "
                        f"<code>{order_id}</code> has timed out.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return

        if payment_method == "fampay":
            await verify_fampay_order(user_id, order_id, background=True)
        else:
            await run_payment_verification(
                user_id, order_id, background=True
            )

        latest = db_query(
            "SELECT status FROM transactions WHERE order_id=?",
            (order_id,),
            fetchone=True,
            commit=False,
        )
        if not latest or latest[0] != "pending":
            return
        # Poll every second while the QR is fresh (that is when people pay), then relax.
        fast_window = time.time() - timestamp < 180
        await asyncio.sleep(1.0 if fast_window else AUTO_VERIFY_INTERVAL_SECONDS)


async def auto_verify_task() -> None:
    """Recovery scanner for pending payments created before a restart."""
    while True:
        await asyncio.sleep(15)
        pending_txns = db_query(
            "SELECT order_id, user_id, payment_method FROM transactions "
            "WHERE status='pending'",
            fetchall=True,
            commit=False,
        ) or []
        for order_id, user_id, payment_method in pending_txns:
            schedule_auto_verification(user_id, order_id, payment_method)

# ==============================================================================
# 9b. ADMIN ALERT: SOMEONE STARTED THE BOT
# ==============================================================================
_START_ALERT_LAST: Dict[int, float] = {}
_START_ALERT_COOLDOWN = 30  # seconds; stops one user spamming /start from flooding the admin
_bg_tasks: set = set()


async def _send_start_alert(user, is_new: bool, verified: bool, referred_by: Optional[int]) -> None:
    """Tell the admin that someone pressed /start. Never raises."""
    try:
        if not get_owner_id() or user.id == get_owner_id():
            return
        if get_setting("start_alert_status", "ON") != "ON":
            return
        now = time.time()
        if not is_new and now - _START_ALERT_LAST.get(user.id, 0) < _START_ALERT_COOLDOWN:
            return
        _START_ALERT_LAST[user.id] = now

        name = html.escape(user.full_name or "Unknown")
        username = f"@{html.escape(user.username)}" if user.username else "no username"
        if is_new:
            status = "🆕 <b>New user</b>"
        elif verified:
            status = "🔁 Returning user"
        else:
            status = "🔁 Returning (phone not verified yet)"
        total = db_query("SELECT COUNT(*) FROM users", fetchone=True, commit=False)
        lines = [
            "🔔 <b>BOT STARTED</b>",
            "",
            status,
            f"👤 Name: <a href=\"tg://user?id={user.id}\">{name}</a>",
            f"🔗 Username: {username}",
            f"🆔 ID: <code>{user.id}</code>",
        ]
        if referred_by:
            lines.append(f"🎁 Referred by: <code>{referred_by}</code>")
        lines.append(f"🕒 Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
        if total:
            lines.append(f"👥 Total users: <b>{int(total[0])}</b>")
        await bot.send_message(get_owner_id(), "\n".join(lines), parse_mode="HTML")
    except Exception as err:
        logger.warning("Start alert to admin failed: %s", err)


def notify_admin_start(user, is_new: bool, verified: bool, referred_by: Optional[int]) -> None:
    """Fire-and-forget so /start never waits for the admin message."""
    task = asyncio.create_task(_send_start_alert(user, is_new, verified, referred_by))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ==============================================================================
# 10. ONBOARDING & START
# ==============================================================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    try: await message.answer_sticker(WELCOME_STICKER_ID)
    except: pass
    await _send_welcome_voice(message)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("v_"):
        order_id = args[1].split("v_")[1]
        msg = await message.answer("🔄 <b>Verifying your payment securely...</b>\n<i>Connecting to gateway...</i>", parse_mode='HTML')
        await run_payment_verification(message.from_user.id, order_id, msg)
        return

    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try: referred_by = int(args[1].split("_")[1])
        except: pass

    # Tagged-product deep link: /start p_<token> should open that product
    # directly instead of the main menu.
    tagged_panel = None
    if len(args) > 1 and args[1].startswith("p_"):
        tagged_panel = decode_panel_tag(args[1][2:])

    user = db_query("SELECT phone FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
    notify_admin_start(message.from_user, is_new=user is None, verified=bool(user and user[0]), referred_by=referred_by)
    current_username = message.from_user.username or ""
    db_query("UPDATE users SET username=? WHERE user_id=?", (current_username, message.from_user.id))

    if not user or not user[0]:
        db_query("INSERT OR IGNORE INTO users (user_id, first_name, username, referred_by, joined_date) VALUES (?, ?, ?, ?, ?)", 
                 (message.from_user.id, message.from_user.first_name, current_username, referred_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        log_activity(message.from_user.id, "ACCOUNT_CREATED")
        if tagged_panel:
            # Remember which product they wanted; open it right after they verify.
            await state.update_data(pending_panel_tag=tagged_panel)
        await message.answer("<b>🛡 VERIFICATION REQUIRED</b>\n\nTo safeguard your orders and account, we need to verify you.\n👇 <b>Tap the button below:</b>", parse_mode='HTML', reply_markup=contact_kb())
    else:
        log_activity(message.from_user.id, "CMD_START")
        if tagged_panel:
            category, panel_name = tagged_panel
            opened = await open_panel_by_tag(message, category, panel_name)
            if not opened:
                await send_main_menu(message)
        else:
            await send_main_menu(message)

@dp.message(F.contact)
async def handle_contact(message: Message, state: FSMContext):
    if message.contact.user_id == message.from_user.id:
        db_query("UPDATE users SET phone=? WHERE user_id=?", (message.contact.phone_number, message.from_user.id))
        referrer = db_query("SELECT referred_by FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
        if referrer and referrer[0]:
            db_query("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (referrer[0],))
            try: await bot.send_message(referrer[0], f"🎉 <b>Referral Success!</b>\nUser <b>{message.from_user.first_name}</b> joined using your link!", parse_mode='HTML')
            except: pass
        log_activity(message.from_user.id, "CONTACT_VERIFIED")
        await message.answer("✅ Verification successful! Welcome to the system.", reply_markup=ReplyKeyboardRemove())

        data = await state.get_data()
        pending = data.get("pending_panel_tag")
        if pending:
            await state.update_data(pending_panel_tag=None)
            category, panel_name = pending
            opened = await open_panel_by_tag(message, category, panel_name)
            if not opened:
                await send_main_menu(message)
            return

        await send_main_menu(message)
    else:
        await message.answer("❌ Security Alert: Please share your OWN contact using the provided button.")

def _panel_callback(category: str, panel: str) -> str:
    """callback_data for the existing panel-packages handler (pnl_), max 64 bytes."""
    cat, pan = category[:30], panel[:30]
    data = f"pnl_{cat}_{pan}"
    while len(data.encode("utf-8")) > 64 and pan:
        pan = pan[:-1]
        data = f"pnl_{cat}_{pan}"
    return data

def encode_panel_tag(category: str, panel_name: str) -> str:
    """Pack category+panel into a short, /start-safe (a-z0-9_-) token."""
    cat, pan = category[:40], panel_name[:40]
    def _pack(c: str, p: str) -> str:
        raw = f"{c}|{p}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    token = _pack(cat, pan)
    while len(token) > 55 and pan:
        pan = pan[:-1]
        token = _pack(cat, pan)
    return token

def decode_panel_tag(token: str) -> Optional[Tuple[str, str]]:
    """Reverse of encode_panel_tag; returns (category, panel_name) or None if invalid."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        category, panel_name = raw.split("|", 1)
        return category, panel_name
    except Exception:
        return None

def panel_deep_link(category: str, panel_name: str) -> str:
    """Shareable link that opens straight to this product's package list."""
    return f"https://t.me/{BOT_USERNAME}?start=p_{encode_panel_tag(category, panel_name)}"

def get_home_product_buttons() -> List[Tuple[str, str, str]]:
    """Products shown directly on the start screen: (label, callback_data, emoji_id).

    Follows the same order as the shop. A category with named panels lists each
    panel; a category without panels is listed as one button.
    """
    items: List[Tuple[str, str, str]] = []
    for category_id, cat in get_shop_categories():
        rows = db_query(
            "SELECT DISTINCT panel_name FROM products "
            "WHERE category LIKE ? AND is_active=1 AND panel_name != ''",
            (cat + '%',), fetchall=True, commit=False,
        ) or []
        panels = sorted({str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()}, key=natural_sort_key)
        if panels:
            for panel in panels:
                # pnl_ callback splits on "_", so use the category screen if the name has one.
                cb = f"catid_{category_id}" if "_" in cat else _panel_callback(cat, panel)
                items.append((panel, cb, get_panel_emoji(panel)))
        else:
            cnt = db_query(
                "SELECT COUNT(*) FROM products WHERE category LIKE ? AND is_active=1",
                (cat + '%',), fetchone=True, commit=False,
            )
            if cnt and cnt[0]:
                items.append((cat, f"catid_{category_id}", get_category_emoji(cat)))
    return items

def get_user_profile_fields(user) -> Dict[str, Any]:
    """Live per-user fields (name, balance, rank, cheats count) shared across UI cards."""
    row = db_query(
        "SELECT balance, is_vip, is_reseller FROM users WHERE user_id=?",
        (user.id,), fetchone=True, commit=False,
    )
    balance = float(row[0] or 0.0) if row else 0.0
    is_vip = bool(row[1]) if row else False
    is_reseller = bool(row[2]) if row else False
    if is_reseller and is_vip:
        rank = "Reseller + VIP"
    elif is_reseller:
        rank = "Reseller"
    elif is_vip:
        rank = "VIP Member"
    else:
        rank = "Standard User"
    cheats_count = len(get_home_product_buttons())
    return {
        "name": html.escape(user.first_name or "User"),
        "user_id": user.id,
        "balance": fmt_curr(balance),
        "rank": rank,
        "cheats": cheats_count,
    }

def build_start_screen(user) -> Tuple[str, InlineKeyboardMarkup]:
    """Caption text + keyboard for the /start screen (GIF card, then product buttons)."""
    fields = get_user_profile_fields(user)
    all_items = get_home_product_buttons()
    inline_products = get_setting("start_inline_products", "ON").upper() == "ON"
    items = all_items if inline_products else []
    text = get_ui_text("start_menu", **fields)

    # Products already sit at the top when shown inline, so the generic "Shop Now" button is skipped.
    kb = main_menu_kb(user.id, exclude_shop=bool(items))
    if items:
        product_rows = [
            [InlineKeyboardButton(
                text=label.upper(), callback_data=cb,
                icon_custom_emoji_id=(emoji_id or None), style="primary",
            )]
            for label, cb, emoji_id in items
        ]
        kb.inline_keyboard = product_rows + kb.inline_keyboard
    return text, kb

def _welcome_gif() -> str:
    gif = get_setting("start_welcome_gif", "").strip()
    return "" if gif.lower() in ("", "none", "off") else gif

def _welcome_voice() -> Tuple[str, str]:
    """(kind, file_id) for the audio greeting played on /start — kind is 'voice' or 'audio'."""
    raw = get_setting("start_welcome_voice", "").strip()
    if not raw or raw.lower() in ("none", "off"):
        return "", ""
    kind, _, file_id = raw.partition(":")
    return (kind, file_id) if kind in ("voice", "audio") and file_id else ("", "")

WELCOME_SPEECH_TEXT = "Welcome to Nagesh Panel Shop"
_tts_fail_until = 0.0
_tts_warned = False


def _welcome_seen(user_id: int) -> bool:
    return bool(db_query("SELECT 1 FROM welcome_voice_seen WHERE user_id=?", (user_id,), fetchone=True, commit=False))


def _mark_welcome_seen(user_id: int) -> None:
    db_query(
        "INSERT OR IGNORE INTO welcome_voice_seen (user_id, sent_at) VALUES (?, ?)",
        (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def _make_tts_mp3(text: str) -> bytes:
    buf = io.BytesIO()
    gTTS(text=text, lang="en", tld="co.in").write_to_fp(buf)
    return buf.getvalue()


async def _send_audio_ref(message: Message, kind: str, file_id: str) -> None:
    if kind == "voice":
        await message.answer_voice(voice=file_id)
    else:
        await message.answer_audio(audio=file_id, title="Welcome", performer="Nagesh Panel Shop")


async def _tts_welcome_ref(message: Message) -> Tuple[str, str]:
    """(kind, file_id) of the auto-spoken greeting. Generated once, then reused from Telegram."""
    global _tts_fail_until, _tts_warned
    cached = get_setting("welcome_tts_file", "")
    kind, _, file_id = cached.partition(":")
    if kind in ("voice", "audio") and file_id:
        return kind, file_id
    if gTTS is None:
        if not _tts_warned:
            _tts_warned = True
            logger.warning("Auto welcome voice needs the gTTS package: add a line 'gTTS' to requirements.txt")
        return "", ""
    if time.time() < _tts_fail_until:
        return "", ""
    try:
        audio = await asyncio.wait_for(asyncio.to_thread(_make_tts_mp3, WELCOME_SPEECH_TEXT), timeout=10)
        sent = await message.answer_voice(voice=BufferedInputFile(audio, filename="welcome.mp3"))
    except Exception:
        _tts_fail_until = time.time() + 600  # don't hammer the speech service if it is down
        logger.warning("Could not generate the welcome speech.", exc_info=True)
        return "", ""
    if sent.voice:
        kind, file_id = "voice", sent.voice.file_id
    elif sent.audio:
        kind, file_id = "audio", sent.audio.file_id
    else:
        return "", ""
    set_setting("welcome_tts_file", f"{kind}:{file_id}")
    return "done", ""  # already delivered to this user


async def _send_welcome_voice(message: Message) -> None:
    """Play the welcome voice ONCE per user (on their first /start). Never blocks /start on failure."""
    uid = message.from_user.id
    try:
        if _welcome_seen(uid):
            return
        if get_setting("start_welcome_voice", "").strip().lower() in ("none", "off"):
            return  # admin switched the voice off
        kind, file_id = _welcome_voice()  # a voice the admin recorded wins
        if file_id:
            await _send_audio_ref(message, kind, file_id)
            _mark_welcome_seen(uid)
            return
        kind, file_id = await _tts_welcome_ref(message)
        if kind == "done":
            _mark_welcome_seen(uid)
        elif file_id:
            await _send_audio_ref(message, kind, file_id)
            _mark_welcome_seen(uid)
    except Exception:
        logger.warning("Welcome voice/audio failed to send.", exc_info=True)


@dp.message(Command("resetwelcomevoice"))
async def cmd_reset_welcome_voice(message: Message):
    if not is_admin_user(message.from_user.id):
        return
    db_query("DELETE FROM welcome_voice_seen")
    await message.answer("✅ Done. Every user will hear the welcome voice once more on their next /start.")


async def _send_start_message(target: Message, text: str, kb: InlineKeyboardMarkup) -> Optional[Message]:
    """Send the start screen: GIF with caption when a GIF is set, else plain text."""
    gif = _welcome_gif()
    if gif:
        try:
            return await target.answer_animation(
                animation=gif, caption=text, reply_markup=kb, parse_mode='HTML',
            )
        except Exception:
            logger.warning("Welcome GIF failed to send, falling back to text.", exc_info=True)
    try:
        return await target.answer(text, reply_markup=kb, parse_mode='HTML')
    except TelegramBadRequest as exc:
        logger.warning("Start text has invalid HTML (%s); sending as plain text.", exc)
        return await target.answer(text, reply_markup=kb, parse_mode=None)

def _admin_panel_text() -> str:
    return (
        f"🔰 <b>nageshbotowner</b>\n\n"
        f"⚙️ <b>Advanced Admin Terminal</b>\n"
        f"<i>Authorized Access Granted. Use the buttons below.</i>\n\n"
        f"🏷 Build: <code>{BOT_CODE_VERSION}</code>"
    )

async def _show_admin_panel(ctx: Any) -> None:
    """Show the admin panel with the configured welcome GIF above it."""
    text = _admin_panel_text()
    kb = admin_kb()
    gif = _welcome_gif()

    if isinstance(ctx, Message):
        if gif:
            try:
                await ctx.answer_animation(
                    animation=gif, caption=text, reply_markup=kb, parse_mode="HTML",
                )
                return
            except Exception:
                logger.warning("Admin welcome GIF failed; falling back to text.", exc_info=True)
        await ctx.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    message = ctx.message
    if gif:
        try:
            await message.answer_animation(
                animation=gif, caption=text, reply_markup=kb, parse_mode="HTML",
            )
            try:
                await message.delete()
            except Exception:
                pass
            return
        except Exception:
            logger.warning("Admin welcome GIF failed; falling back to edit.", exc_info=True)
    await message.edit_text(text, reply_markup=kb, parse_mode="HTML")

async def send_main_menu(ctx: Any):
    text, kb = build_start_screen(ctx.from_user)
    if isinstance(ctx, Message):
        await _send_start_message(ctx, text, kb)
        return

    msg = ctx.message
    if not _welcome_gif():
        # No GIF configured: behave like a normal text menu (edit_text is media-safe).
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode='HTML')
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            logger.warning("Start text edit failed (%s); retrying as plain text.", exc)
            await msg.edit_text(text, reply_markup=kb, parse_mode=None)
        return

    if getattr(msg, "animation", None) and (msg.chat.id, msg.message_id) not in _STORE_MEDIA_MSGS:
        # Already the GIF screen (e.g. refreshed after a purchase): just swap caption/buttons.
        try:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode='HTML')
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            logger.warning("edit_caption failed on start screen: %s", exc)

    # Coming back from a text screen: text cannot turn into a GIF, so send a fresh one.
    sent = await _send_start_message(msg, text, kb)
    if sent:
        try:
            await msg.delete()
        except Exception:
            pass

@dp.message(F.text.startswith("/setwelcomevoice") | F.caption.startswith("/setwelcomevoice"))
async def cmd_set_welcome_voice(message: Message):
    """Admin: set the audio greeting played on /start.

    Use: send a voice note or audio file with caption /setwelcomevoice,
    or reply to one with /setwelcomevoice.
    /setwelcomevoice none  -> remove it (no sound on /start).
    """
    if not is_admin_user(message.from_user.id):
        return
    src = message
    media_voice = src.voice or (src.reply_to_message.voice if src.reply_to_message else None)
    media_audio = src.audio or (src.reply_to_message.audio if src.reply_to_message else None)
    if media_voice:
        set_setting("start_welcome_voice", f"voice:{media_voice.file_id}")
        return await message.answer("✅ Welcome voice saved. Ab /start karke sunlo.")
    if media_audio:
        set_setting("start_welcome_voice", f"audio:{media_audio.file_id}")
        return await message.answer("✅ Welcome audio saved. Ab /start karke sunlo.")
    parts = (message.text or message.caption or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg.lower() in ("none", "off", "clear"):
        set_setting("start_welcome_voice", "None")
        return await message.answer("✅ Welcome voice removed. /start ab chup rahega.")
    await message.answer(
        "🔊 <b>Set Welcome Voice</b>\n\n"
        "• Voice note (mic wala) bhejo caption <code>/setwelcomevoice</code> ke saath\n"
        "• ya mp3/audio file bhejo caption ke saath\n"
        "• ya kisi voice/audio ko reply karke <code>/setwelcomevoice</code> likho\n"
        "• hatane ke liye <code>/setwelcomevoice none</code>",
        parse_mode='HTML',
    )

@dp.message(F.text.startswith("/setwelcomegif") | F.caption.startswith("/setwelcomegif"))
async def cmd_set_welcome_gif(message: Message):
    """Admin: set the GIF shown on /start.

    Use: send a GIF with caption /setwelcomegif, or reply to a GIF with
    /setwelcomegif, or /setwelcomegif <direct https GIF/MP4 URL>.
    /setwelcomegif none  -> remove the GIF (plain text start screen).
    """
    if not is_admin_user(message.from_user.id):
        return
    media = message.animation
    if not media and message.reply_to_message:
        media = message.reply_to_message.animation
    if media:
        set_setting("start_welcome_gif", media.file_id)
        return await message.answer("✅ Welcome GIF saved. Ab /start karke dekho.")
    parts = (message.text or message.caption or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg.lower() in ("none", "off", "clear"):
        set_setting("start_welcome_gif", "None")
        return await message.answer("✅ Welcome GIF removed. Start screen ab sirf text mein aayegi.")
    if arg.startswith(("http://", "https://")):
        set_setting("start_welcome_gif", arg)
        return await message.answer("✅ Welcome GIF URL saved. Ab /start karke dekho.")
    await message.answer(
        "🎞 <b>Set Welcome GIF</b>\n\n"
        "• GIF bhejo caption <code>/setwelcomegif</code> ke saath\n"
        "• ya kisi GIF ko reply karke <code>/setwelcomegif</code> likho\n"
        "• ya <code>/setwelcomegif https://...gif</code>\n"
        "• hatane ke liye <code>/setwelcomegif none</code>",
        parse_mode='HTML',
    )

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    log_activity(call.from_user.id, "RETURN_MAIN_MENU")
    await send_main_menu(call)

# ==============================================================================
# 11. ADD BALANCE
# ==============================================================================
def _has_setting(key: str) -> bool:
    value = str(get_setting(key, "") or "").strip()
    return bool(value) and value.lower() != "none"

def zapupi_ready() -> bool:
    """ZapUPI is switched ON in /admin AND its API key is actually saved."""
    return gateway_enabled("zapupi") and _has_setting("zapupi_api")

def fampay_ready() -> bool:
    """FamPay (FamGateway) is ON in /admin AND its UPI ID + API key are saved."""
    return gateway_enabled("fampay") and bool(fampay_upi_id()) and _has_setting("fampay_api_key")

def _single_inr_gateway() -> Optional[str]:
    """Return the only usable INR gateway, or None when the chooser screen is needed.

    When exactly one gateway is usable, "Add Balance" skips the chooser and opens
    that gateway's amount screen directly. The amount screen's Back button must
    know this, otherwise Back -> menu_add_balance -> amount screen loops forever.
    """
    zapupi_ok = zapupi_ready()
    fampay_ok = fampay_ready()
    if zapupi_ok and not fampay_ok:
        return "zapupi"
    if fampay_ok and not zapupi_ok:
        return "fampay"
    return None

@dp.callback_query(F.data == "menu_add_balance")
async def select_gateway_menu(call: CallbackQuery, state: FSMContext):
    # Leaving any half-finished deposit step (keypad / TxID entry) must reset the state,
    # otherwise the next text the user sends is still treated as a deposit input.
    await state.clear()
    log_activity(call.from_user.id, "VIEW_ADD_BALANCE")
    zapupi_ok = zapupi_ready()
    fampay_ok = fampay_ready()

    if not zapupi_ok and not fampay_ok:
        return await call.message.edit_text(
            "⚠️ <b>Payment abhi available nahi hai.</b>\nPlease thodi der baad try karo ya support se contact karo.",
            reply_markup=back_kb("back_main"), parse_mode='HTML')

    # If exactly one INR gateway is usable, skip the "choose a gateway" step
    # entirely and land straight on the amount screen — that's the whole
    # point of picking a single gateway from /admin.
    if zapupi_ok and not fampay_ok:
        return await show_inr_amount_menu(call, "zapupi", state)
    if fampay_ok and not zapupi_ok:
        return await show_inr_amount_menu(call, "fampay", state)

    text = get_ui_text("add_balance_menu")
    await premium_click_animation(call.message, "Opening secure payment center")
    rows = []
    if zapupi_ok:
        rows.append([InlineKeyboardButton(
            text="⚡ SECURE QR — Auto Verify", callback_data="gateway_inr",
            icon_custom_emoji_id=get_emoji_icon("upi"), style="primary"
        )])
    if fampay_ok:
        rows.append([InlineKeyboardButton(
            text="💳 EXACT QR — Auto Verify", callback_data="gateway_fampay",
            icon_custom_emoji_id=get_emoji_icon("upi"), style="success"
        )])
    rows.append([InlineKeyboardButton(
        text="BACK", callback_data="back_main",
        icon_custom_emoji_id=get_emoji_icon("back"), style="danger"
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 12. UPI & CRYPTO PAYMENT FLOWS
# ==============================================================================
def get_direct_qr_url(
    amount: float,
    gateway_qr_url: str = "",
    payment_url: str = "",
    order_id: str = "",
) -> str:
    """Return the best exact-amount QR without making UPI setup mandatory.

    Gateways that return a QR image are preferred. If a gateway only returns a
    hosted payment URL, that URL is encoded into a QR automatically so the
    user still sees a QR before tapping Pay. A configured UPI ID is a true
    last-resort fallback, used ONLY when the gateway gave us nothing at all —
    it must never override a live, order-linked QR/payment_url, since money
    sent to a static UPI ID can't be matched back to an order for
    verification.
    """
    fallback = str(gateway_qr_url or "").strip()
    if fallback.startswith(("http://", "https://")):
        return fallback

    hosted_payment_url = str(payment_url or "").strip()
    if hosted_payment_url.startswith(("http://", "https://")):
        return (
            "https://api.qrserver.com/v1/create-qr-code/?"
            + urllib.parse.urlencode({
                "size": "640x640",
                "data": hosted_payment_url,
            })
        )

    configured_qr = fampay_upi_id()
    if configured_qr and "@" in configured_qr:
        upi_payload = "upi://pay?" + urllib.parse.urlencode({
            "pa": configured_qr,
            "pn": "Store Wallet",
            "am": f"{float(amount):.2f}",
            "cu": "INR",
            "tn": order_id or "Wallet Deposit",
        })
        return (
            "https://api.qrserver.com/v1/create-qr-code/?"
            + urllib.parse.urlencode({
                "size": "640x640",
                "data": upi_payload,
            })
        )
    return ""


async def show_payment_invoice(
    message_obj: Message,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    qr_url: str = "",
) -> Optional[Message]:
    """Put the QR at the top of the chat, with a text fallback if unconfigured.

    ``reply_markup`` may be None/empty: fully automatic payment screens have no buttons.
    """
    markup = None
    if reply_markup is not None and reply_markup.inline_keyboard:
        markup = apply_button_theme(reply_markup, "shop")
    if qr_url:
        sent = await message_obj.answer_photo(
            photo=qr_url,
            caption=caption,
            reply_markup=markup,
            parse_mode="HTML",
        )
        try:
            await message_obj.delete()
        except Exception:
            pass
        return sent
    return await message_obj.edit_text(caption, reply_markup=markup, parse_mode="HTML")


# The QR message of every open product order, so it can be removed the moment the order ends.
_QR_MESSAGES: Dict[str, Tuple[int, int]] = {}

def _remember_qr_message(order_id: str, msg: Optional[Message]) -> None:
    if msg is None:
        return
    _QR_MESSAGES[order_id] = (msg.chat.id, msg.message_id)
    while len(_QR_MESSAGES) > 1000:
        _QR_MESSAGES.pop(next(iter(_QR_MESSAGES)))

async def _drop_qr_message(order_id: str, keep: Optional[Message] = None) -> None:
    """Delete the QR photo of an order that is paid, expired or replaced."""
    ref = _QR_MESSAGES.pop(order_id, None)
    if not ref:
        return
    if keep is not None and (keep.chat.id, keep.message_id) == ref:
        return
    try:
        await bot.delete_message(ref[0], ref[1])
    except Exception:
        pass

async def _safe_answer(call: CallbackQuery, *args, **kwargs) -> None:
    """A callback can only be answered once; later attempts must not crash the flow."""
    try:
        await call.answer(*args, **kwargs)
    except Exception:
        pass


async def show_inr_amount_menu(call: CallbackQuery, gateway: str, state: FSMContext, show_binance_option: bool = False):
    """Add Balance landing screen: the keypad ("ADD BALANCE - ENTER AMOUNT") opens directly."""
    await state.set_state(UserStates.custom_amount_input)
    await state.update_data(amount_str="0", gateway=gateway)
    await show_keypad(call.message, "0", gateway)

@dp.callback_query(F.data == "gateway_inr")
async def add_balance_inr(call: CallbackQuery, state: FSMContext):
    if not gateway_enabled("zapupi"):
        return await call.answer("Secure payment is currently unavailable.", show_alert=True)
    await show_inr_amount_menu(call, "zapupi", state)

@dp.callback_query(F.data == "gateway_zapupi")
async def back_to_zapupi_amount_menu(call: CallbackQuery, state: FSMContext):
    """Return from the custom keypad to the ZapUPI amount screen."""
    if not gateway_enabled("zapupi"):
        return await call.answer("Secure payment is currently unavailable.", show_alert=True)
    await show_inr_amount_menu(call, "zapupi", state)

@dp.callback_query(F.data == "gateway_fampay")
async def add_balance_fampay(call: CallbackQuery, state: FSMContext):
    if not gateway_enabled("fampay"):
        return await call.answer("Secure payment is currently unavailable.", show_alert=True)
    if not fampay_upi_id():
        return await call.message.edit_text(
            "⚠️ <b>QR payment is temporarily unavailable.</b>\nPlease try again later.",
            reply_markup=back_kb("menu_add_balance"), parse_mode="HTML"
        )
    await show_inr_amount_menu(call, "fampay", state)

@dp.callback_query(F.data.in_({"custom_deposit_keypad", "custom_deposit_keypad_zapupi", "custom_deposit_keypad_fampay"}))
async def show_custom_keypad(call: CallbackQuery, state: FSMContext):
    gateway = "fampay" if call.data.endswith("fampay") else "zapupi"
    await state.set_state(UserStates.custom_amount_input)
    await state.update_data(amount_str="0", gateway=gateway)
    await show_keypad(call.message, gateway=gateway)

def _keypad_text(amount_str: str) -> str:
    line = "━━━━━━━━━━━━━━━━━━"
    return (
        f"<blockquote>{get_emoji('money_icon')} <b>ADD BALANCE — ENTER AMOUNT</b></blockquote>\n"
        f"{line}\n\n"
        f"{get_emoji('welcome')} <b>Amount: ₹{amount_str}</b>\n\n"
        f"{line}\n\n"
        f"<i>{get_emoji('check_icon')} Instant Auto-Credit\n"
        f"{get_emoji('shield_icon')} 100% Secure UPI Payment\n"
        f"{get_emoji('check_icon')} Verified in Seconds</i>\n\n"
        f"<i>{get_emoji('point_down')} Use the keypad below to enter amount</i>"
    )

async def show_keypad(message: Message, amount_str: str = "0", gateway: str = "zapupi"):
    def num(n: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=n, callback_data=f"kp_{n}", style="primary")

    # Single-gateway setups have no chooser screen, so Back returns to the main menu.
    back_target = "back_main" if _single_inr_gateway() else "menu_add_balance"
    rows = [
        [num("1"), num("2"), num("3")],
        [num("4"), num("5"), num("6")],
        [num("7"), num("8"), num("9")],
        [
            InlineKeyboardButton(text="Clear", callback_data="kp_clear", style="danger"),
            num("0"),
            InlineKeyboardButton(text="Confirm", callback_data="kp_confirm",
                                 icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success"),
        ],
    ]
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_target,
                                      icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    try:
        await message.edit_text(_keypad_text(amount_str), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML')
    except TelegramBadRequest as exc:
        # e.g. pressing Clear while the amount is already 0 -> nothing to update.
        if "not modified" not in str(exc).lower():
            raise

@dp.callback_query(F.data.startswith("kp_"), UserStates.custom_amount_input)
async def keypad_handler(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount_str = data.get("amount_str", "0")
    action = call.data.split("_")[1]
    if action == "confirm":
        if amount_str == "0":
            await call.answer("Amount cannot be zero.", show_alert=True)
            return
        try:
            amount = float(amount_str)
            if amount < 10:
                await call.answer("Minimum deposit is ₹10.", show_alert=True)
                return
            await state.clear()
            await call.answer("Generating secure payment link…")
            await call.message.edit_text("⏳ <b>Generating Secure Link...</b>", parse_mode='HTML')
            gateway = data.get("gateway", "zapupi")
            if gateway == "fampay":
                await generate_fampay_order(call.from_user.id, amount, call.message)
            else:
                await generate_zapupi_order(call.from_user.id, amount, call.message)
        except (ValueError, TypeError):
            await call.answer("Invalid amount.", show_alert=True)
        return
    if action == "backspace":
        if len(amount_str) > 1: amount_str = amount_str[:-1]
        else: amount_str = "0"
    elif action == "clear":
        amount_str = "0"
    else:
        if amount_str == "0": amount_str = action
        else: amount_str += action
        if len(amount_str) > 6: amount_str = amount_str[:6]
    await state.update_data(amount_str=amount_str)
    await show_keypad(call.message, amount_str, data.get("gateway", "zapupi"))
    await call.answer()

@dp.callback_query(F.data.startswith("kp_"))
async def keypad_without_state(call: CallbackQuery, state: FSMContext):
    """Old keypad message after a restart: reopen a fresh keypad instead of a dead button."""
    gateway = _single_inr_gateway() or ("fampay" if fampay_ready() else "zapupi")
    await call.answer()
    await show_inr_amount_menu(call, gateway, state)

@dp.callback_query(F.data.startswith("pay_"))
async def process_zapupi_payment_callback(call: CallbackQuery):
    inr_amount = float(call.data.split("_")[1])
    # Acknowledge before the network request so Telegram does not show the
    # callback spinner for the entire duration of the gateway call.
    await call.answer("Generating secure payment link…")
    await call.message.edit_text("⏳ <b>Preparing your exact-amount QR...</b>", parse_mode='HTML')
    await generate_zapupi_order(call.from_user.id, inr_amount, call.message)

@dp.callback_query(F.data.startswith("fpay_"))
async def process_fampay_payment_callback(call: CallbackQuery):
    if not gateway_enabled("fampay"):
        return await call.answer("Secure payment is currently unavailable.", show_alert=True)
    inr_amount = float(call.data.split("_")[1])
    await call.answer("✨ Creating your exact-amount QR…")
    await call.message.edit_text("✨ <b>Preparing Exact Payment QR...</b>", parse_mode="HTML")
    await generate_fampay_order(call.from_user.id, inr_amount, call.message)

async def generate_zapupi_order(user_id: int, inr_amount: float, message_obj: Message) -> None:
    if not gateway_enabled("zapupi"):
        return await message_obj.edit_text(
            "⚠️ Secure payment is currently unavailable.",
            reply_markup=back_kb("menu_add_balance"), parse_mode='HTML'
        )
    api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
    if not api_key_check or not api_key_check[0]:
        return await message_obj.edit_text("⚠️ Secure payment is currently unavailable. Please try again later.", reply_markup=back_kb("gateway_inr"), parse_mode='HTML')
        
    api_key = api_key_check[0]
    current_time = int(time.time())
    order_id = f"NXT{user_id}{current_time}"
    user_phone = db_query("SELECT phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    mobile = user_phone[0] if user_phone and user_phone[0] else "9999999999"
    bot_deep_link = f"https://t.me/{BOT_USERNAME}?start=v_{order_id}"
    
    db_query("INSERT INTO transactions (order_id, user_id, amount_inr, status, timestamp, purpose, quantity, payment_method) VALUES (?, ?, ?, 'pending', ?, 'wallet', 1, 'upi')", (order_id, user_id, inr_amount, current_time))
    payment_url = ""
    gateway_qr_url = ""
    
    async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
        try:
            url = "https://pay.zapupi.com/api/create-order"
            payload = {"zap_key": api_key, "order_id": order_id, "amount": str(inr_amount), "customer_mobile": mobile, "remark": "Wallet Topup", "success_url": bot_deep_link, "failed_url": f"https://t.me/{BOT_USERNAME}", "timeout_url": f"https://t.me/{BOT_USERNAME}", "webhook_url": f"https://t.me/{BOT_USERNAME}"}
            async with session.post(url, json=payload) as resp:
                try:
                    res_data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    res_data = {}
                if resp.status < 200 or resp.status >= 300:
                    raise RuntimeError(f"Gateway returned HTTP {resp.status}")
                if str(res_data.get("status", "")).lower() != "success":
                    return await message_obj.edit_text(
                        f"❌ <b>Gateway Data Error:</b> {html.escape(str(res_data.get('message', 'Unknown structure.')))}",
                        reply_markup=back_kb("gateway_inr"),
                        parse_mode='HTML',
                    )
                response_data = res_data.get("data") if isinstance(res_data.get("data"), dict) else res_data
                payment_url = response_data.get("payment_url") or response_data.get("payment_link") or ""
                gateway_qr_url = response_data.get("qr_url") or response_data.get("qr") or ""
                if not payment_url and not gateway_qr_url:
                    raise RuntimeError("Gateway did not return a payment URL or QR")
        except asyncio.TimeoutError:
            return await message_obj.edit_text(
                "❌ <b>QR request timed out.</b>\nPlease try again in a moment.",
                reply_markup=back_kb("gateway_inr"),
                parse_mode='HTML',
            )
        except aiohttp.ClientError as e:
            logger.warning("Payment create-order connection failed: %s", e)
            return await message_obj.edit_text(
                "❌ <b>Could not prepare the payment QR.</b>\nPlease try again in a moment.",
                reply_markup=back_kb("gateway_inr"),
                parse_mode='HTML',
            )
        except Exception as e:
            logger.exception("ZapUPI wallet order creation failed")
            return await message_obj.edit_text(
                f"❌ <b>API Error:</b> {html.escape(str(e))}",
                reply_markup=back_kb("gateway_inr"),
                parse_mode='HTML',
            )

    buttons = []
    if payment_url:
        # A Web App button opens ZapUPI's real hosted payment page inside
        # Telegram itself. That page is where the genuine, live UPI QR is
        # rendered by ZapUPI's own server — this is NOT a lookalike image,
        # it's the actual gateway page, just embedded instead of redirected.
        buttons.append([InlineKeyboardButton(text="💳 OPEN PAYMENT QR", web_app=WebAppInfo(url=payment_url))])
        buttons.append([InlineKeyboardButton(text="🌐 Open Payment Page", url=payment_url, style="success")])
    buttons.extend([
        [InlineKeyboardButton(text="Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = (
        f"💳 <b>EXACT PAYMENT QR</b>\n\n"
        f"💰 <b>Pay exactly:</b> <code>₹{inr_amount:.2f}</code>\n\n"
        "1️⃣ Open the QR and scan it with any UPI app.\n"
        "2️⃣ Pay the exact amount shown above.\n"
        "3️⃣ Payment successful hote hi balance automatically verify aur credit ho jayega. ✨"
    )
    # No fake/lookalike QR image is sent — the Pay Now button above opens
    # ZapUPI's real hosted page, which is the only place the genuine QR lives.
    log_activity(user_id, "GENERATE_INVOICE", f"Amount: {inr_amount}, Order ID: {order_id}")
    qr_display_url = get_direct_qr_url(
        inr_amount, gateway_qr_url=gateway_qr_url,
        payment_url=payment_url, order_id=order_id,
    )
    await show_payment_invoice(message_obj, text, kb, qr_display_url)
    schedule_auto_verification(user_id, order_id, "upi")

def split_wallet_and_qr(balance: float, total: float) -> Tuple[float, float]:
    """Return (wallet_part, qr_amount) for a product that costs ``total``.

    The wallet pays as much as it can and the QR covers only the rest.
    The QR is never below Rs 1 (gateway minimum) unless the product itself is cheaper.
    """
    total = round(float(total), 2)
    remaining = round(total - max(0.0, float(balance)), 2)
    qr_amount = round(min(total, max(remaining, 1.0)), 2)
    wallet_part = round(total - qr_amount, 2)
    return wallet_part, qr_amount

def wallet_share_for_purchase(balance: float) -> float:
    """Wallet money usable for a key purchase (0 when key purchases are QR-only)."""
    return max(0.0, float(balance or 0)) if PRODUCT_PAY_FROM_WALLET else 0.0

def refund_reserved_wallet(order_id: str) -> float:
    """Give back the wallet part of a product order that will never be delivered.

    Safe to call more than once: the amount is zeroed atomically before crediting.
    """
    row = db_query("SELECT user_id, wallet_used FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
    if not row:
        return 0.0
    user_id, used = row[0], float(row[1] or 0)
    if used <= 0:
        return 0.0
    if not db_update_count("UPDATE transactions SET wallet_used=0 WHERE order_id=? AND wallet_used=?", (order_id, used)):
        return 0.0
    db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (used, user_id))
    return used

def _direct_purchase_caption(label: str, total: float, order_id: str, wallet_used: float = 0.0, pay_amount: Optional[float] = None) -> str:
    line = "━━━━━━━━━━━━━━━━━━"
    minutes = max(1, PAYMENT_TIMEOUT_SECONDS // 60)
    pay = total if pay_amount is None else pay_amount
    text = (
        f"<blockquote>{get_emoji('product_store')} <b>DIRECT PURCHASE QR</b> {get_emoji('product_store')}</blockquote>\n\n"
        f"📦 <b>Product:</b> {html.escape(label)}\n"
        f"🏷 <b>Price:</b> {fmt_curr(total)}\n"
    )
    if wallet_used > 0:
        text += f"{get_emoji('wallet_left')} <b>Wallet Used:</b> −{fmt_curr(wallet_used)}\n"
    text += (
        f"{get_emoji('money_icon')} <b>{'Pay Remaining' if wallet_used > 0 else 'Pay Amount'}:</b> <code>{fmt_curr(pay)}</code>\n"
        f"🆔 <b>Order ID:</b>\n<code>{html.escape(order_id)}</code>\n"
        f"{line}\n\n"
        "⏳ <b>Wait!</b> <i>Scanning for payment automatically...</i>\n"
        f"⚠️ <b>Note:</b> This QR will expire in {minutes} minutes.\n"
        "📦 <i>Product will be delivered here instantly on payment.</i>"
    )
    if wallet_used > 0:
        text += f"\n💳 <i>If this QR expires unpaid, {fmt_curr(wallet_used)} returns to your wallet automatically.</i>"
    return text

async def generate_fampay_order(
    user_id: int,
    inr_amount: float,
    message_obj: Message,
    product_id: int = 0,
    quantity: int = 1,
    product_label: str = "",
    wallet_part: float = 0.0,
    product_total: float = 0.0,
) -> None:
    """Create a FamGateway QR order whose status can be auto-verified.

    With ``product_id`` set, the QR pays for that product directly (purpose='product'):
    once the payment is verified the key is delivered instead of crediting the wallet.
    """
    back_target = "menu_shop" if product_id else "menu_add_balance"

    async def _fail_edit(text: str, **kwargs):
        # A failed product QR must free the purchase lock, or the user is blocked from retrying.
        if product_id:
            release_purchase_lock(user_id)
        return await message_obj.edit_text(text, **kwargs)

    if not gateway_enabled("fampay"):
        return await _fail_edit(
            "⚠️ Secure payment is currently unavailable.",
            reply_markup=back_kb(back_target), parse_mode="HTML"
        )
    receiver = fampay_upi_id()
    api_key = get_setting("fampay_api_key", "")
    if not receiver or "@" not in receiver or not api_key:
        return await _fail_edit(
            "⚠️ <b>QR payment is temporarily unavailable.</b>\n"
            "Please try again later.",
            reply_markup=back_kb(back_target), parse_mode="HTML"
        )

    internal_id = f"{'FPR' if product_id else 'FAM'}{user_id}{int(time.time())}{random.randint(100, 999)}"
    provider_id = ""
    qr_url = ""
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            async with session.post(
                "https://famgateway.in/api/create-order.php",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "amount": round(float(inr_amount), 2),
                    "customer_name": f"Telegram user {user_id}",
                    "redirect_url": f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "",
                },
            ) as resp:
                raw_text = await resp.text()
                try:
                    data = json.loads(raw_text)
                except (ValueError, TypeError):
                    logger.warning("FamGateway create-order returned non-JSON (status %s): %s", resp.status, raw_text[:500])
                    raise RuntimeError(f"FamGateway sent an unexpected response (HTTP {resp.status}).")
                payload = data.get("data") if isinstance(data.get("data"), dict) else data
                if resp.status < 200 or resp.status >= 300 or str(data.get("status", "")).lower() != "success":
                    logger.warning("FamGateway create-order failed (status %s): %s", resp.status, data)
                    raise RuntimeError(data.get("message", "FamGateway QR creation failed"))
                provider_id = str(
                    payload.get("order_id") or payload.get("id") or payload.get("orderId") or ""
                ).strip()
                qr_url = str(
                    payload.get("qr_url") or payload.get("qr_code") or payload.get("qr")
                    or payload.get("upi_qr") or payload.get("payment_url") or ""
                ).strip()
                if not provider_id or not qr_url:
                    logger.warning("FamGateway create-order missing order_id/qr_url. Raw payload: %s", data)
                    raise RuntimeError("FamGateway did not return order_id or qr_url")
    except asyncio.TimeoutError:
        return await _fail_edit("❌ <b>QR request timed out.</b>\nPlease try again.", reply_markup=back_kb(back_target), parse_mode="HTML")
    except (aiohttp.ClientError, ValueError, RuntimeError) as exc:
        logger.warning("FamGateway QR creation failed: %s", exc)
        return await _fail_edit(
            "❌ <b>Could not prepare the payment QR.</b>\nPlease try again.",
            reply_markup=back_kb(back_target), parse_mode="HTML",
        )


    # Reserve the wallet part only now that the QR really exists, so a gateway failure never touches the wallet.
    if product_id and wallet_part > 0:
        if not db_update_count("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (wallet_part, user_id, wallet_part)):
            return await _fail_edit(
                "❌ <b>Wallet balance changed.</b>\nPlease tap the product again.",
                reply_markup=back_kb(back_target), parse_mode="HTML")
    saved_rows = db_update_count(
        "INSERT INTO transactions(order_id,user_id,amount_inr,status,timestamp,purpose,product_id,quantity,payment_method,provider_order_id,wallet_used) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (internal_id, user_id, inr_amount, "pending", int(time.time()),
         "product" if product_id else "wallet", product_id or None, quantity if product_id else 1,
         "fampay", provider_id, wallet_part if product_id else 0.0),
    )
    if not saved_rows and product_id and wallet_part > 0:
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (wallet_part, user_id))
    if not saved_rows:
        # The FamGateway order was created, but we couldn't save it locally
        # (DB write failed/DB not persisted). Showing the QR anyway would
        # let the user pay for an order "Verify" could never find — so we
        # stop here instead of producing a QR nobody can confirm.
        logger.error(
            "FamPay order %s (provider %s) was created but NOT saved to the local DB — refusing to show QR.",
            internal_id, provider_id,
        )
        return await _fail_edit(
            "❌ <b>Could not save your order.</b>\n"
            "Please try again in a few seconds. Agar yeh baar baar ho raha hai, "
            "to bot ke database (DB_PATH / Railway Volume) ko check karo — "
            "restarts ke beech data persist nahi ho raha hoga.",
            reply_markup=back_kb(back_target), parse_mode="HTML",
        )
    if product_id:
        kb = None  # fully automatic: no Verify / Cancel buttons
        text = _direct_purchase_caption(product_label, product_total or inr_amount, internal_id, wallet_used=wallet_part, pay_amount=inr_amount)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        ])
        text = (
            f"💳 <b>EXACT PAYMENT QR — {fmt_curr(inr_amount)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Pay exact amount:</b> <code>{fmt_curr(inr_amount)}</code>\n"
            "📱 Scan the QR with any UPI app.\n\n"
            "Payment status auto-check ho raha hai; payment successful hote hi balance khud add hoga."
        )
    log_activity(user_id, "GENERATE_FAMPAY_QR", f"Amount: {inr_amount}, Order ID: {internal_id}")
    qr_display_url = get_direct_qr_url(
        inr_amount, gateway_qr_url=qr_url, order_id=internal_id,
    )
    sent = await show_payment_invoice(message_obj, text, kb, qr_display_url)
    if product_id:
        _remember_qr_message(internal_id, sent)
    schedule_auto_verification(user_id, internal_id, "fampay")

async def verify_fampay_order(user_id: int, internal_id: str, reply_target: Any = None, background: bool = False) -> bool:
    txn = db_query(
        "SELECT amount_inr,status,timestamp,provider_order_id,purpose FROM transactions "
        "WHERE order_id=? AND user_id=? AND payment_method='fampay'",
        (internal_id, user_id), fetchone=True, commit=False,
    )
    if not txn:
        if isinstance(reply_target, CallbackQuery):
            await reply_target.answer("❌ Payment request not found.", show_alert=True)
        return False
    amount, status, timestamp, provider_id, purpose = txn
    if status in ("paid", "processing", "completed"):
        if isinstance(reply_target, CallbackQuery):
            note = "⏳ Payment received; your key is being prepared." if purpose == "product" else "✅ Payment already credited."
            await reply_target.answer(note, show_alert=True)
        return True
    if time.time() - timestamp > 900 and status == "pending":
        if db_update_count("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (internal_id,)) and purpose == "product":
            refund_reserved_wallet(internal_id)
        if purpose == "product":
            release_purchase_lock(user_id)
        if isinstance(reply_target, CallbackQuery):
            await reply_target.answer("⏳ This payment QR expired.", show_alert=True)
        return False
    api_key = get_setting("fampay_api_key", "")
    if not api_key or not provider_id:
        if isinstance(reply_target, CallbackQuery):
            await reply_target.answer("⚠️ Payment service is temporarily unavailable.", show_alert=True)
        return False
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            async with session.get(
                "https://famgateway.in/api/verify-order.php",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"order_id": provider_id, "api_key": api_key},
            ) as resp:
                result = await resp.json(content_type=None)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        received = float(data.get("amount") or 0)
        success = str(result.get("status", "")).lower() == "success" and received >= float(amount) - 0.01
        if success:
            claimed = db_update_count(
                "UPDATE transactions SET status='paid', utr=? WHERE order_id=? AND status='pending'",
                (str(data.get("utr") or data.get("transaction_id") or ""), internal_id),
            )
            if claimed and purpose == "product":
                # Direct product payment: deliver the key(s), do NOT credit the wallet.
                if background or not isinstance(reply_target, CallbackQuery):
                    target = await bot.send_message(
                        user_id, "✨ <b>AUTO-VERIFIED!</b>\n\nPayment received. Preparing your keys...", parse_mode="HTML")
                else:
                    target = reply_target.message
                await fulfill_product_transaction(internal_id, user_id, target)
                return True
            if claimed:
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
                await send_advanced_notification(user_id, "DEPOSIT", amount, product=internal_id, gateway="FamPay Auto")
                log_activity(user_id, "DEPOSIT_AUTO_SUCCESS", f"Amount: {amount}, Gateway: FamPay, Order: {internal_id}")
                message = f"✅ <b>PAYMENT VERIFIED!</b>\n\n{fmt_curr(amount)} has been added to your wallet."
                if background:
                    await bot.send_message(user_id, message, reply_markup=main_menu_kb(user_id), parse_mode="HTML")
                elif isinstance(reply_target, CallbackQuery):
                    try:
                        await reply_target.message.edit_caption(message, reply_markup=back_kb(), parse_mode="HTML")
                    except Exception:
                        await reply_target.message.answer(message, reply_markup=back_kb(), parse_mode="HTML")
                elif reply_target:
                    await reply_target.answer(message, reply_markup=back_kb(), parse_mode="HTML")
            return True
        if isinstance(reply_target, CallbackQuery):
            await reply_target.answer("⏳ Payment abhi pending hai. Auto-check chalta rahega.", show_alert=True)
        return False
    except Exception:
        logger.exception("FamGateway verification error for %s", internal_id)
        if isinstance(reply_target, CallbackQuery):
            await reply_target.answer("⚠️ Payment verification is temporarily unavailable.", show_alert=True)
        return False

@dp.callback_query(F.data.startswith("verify_fampay_"))
async def manual_fampay_verify_callback(call: CallbackQuery):
    await call.answer("🔄 Verifying payment…")
    await verify_fampay_order(call.from_user.id, call.data.split("verify_fampay_", 1)[1], call)

@dp.callback_query(F.data.startswith("verify_"))
async def manual_verify_callback(call: CallbackQuery):
    await call.answer("🔄 Verifying payment…")
    order_id = call.data.split("_", 1)[1]
    await run_payment_verification(call.from_user.id, order_id, call)

@dp.callback_query(F.data == "gateway_crypto")
async def add_balance_crypto(call: CallbackQuery, state: FSMContext):
    address_check = db_query("SELECT value FROM settings WHERE key='binance_address'", fetchone=True)
    if not address_check or not address_check[0]:
        return await call.message.edit_text("⚠️ Binance Gateway is currently offline. Admin has not set a deposit address.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    deposit_address = address_check[0]
    msg = (f"🪙 <b>— BINANCE USDT DEPOSIT —</b> 🪙\n\n💵 <b>Exchange Rate:</b> 1 USDT = ₹{USDT_TO_INR}\n⚠️ <b>Network:</b> Please send via <b>TRC20</b> or <b>BEP20</b>.\n\n👇 <b>Send your USDT to this exact address:</b>\n<code>{deposit_address}</code>\n\n━━━━━━━━━━━━━━━━━━\n✅ <b>After sending the USDT, reply to this message with your exact TxID (Transaction Hash) to instantly claim your balance.</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await call.message.edit_text(msg, reply_markup=kb, parse_mode='HTML')
    await state.set_state(UserStates.wait_for_crypto_txid)

@dp.message(UserStates.wait_for_crypto_txid)
async def process_crypto_txid(m: Message, state: FSMContext):
    txid = m.text.strip()
    user_id = m.from_user.id
    if len(txid) < 10: return await m.answer("❌ That doesn't look like a valid TxID. Please try again.")
    if db_query("SELECT txid FROM crypto_txns WHERE txid=?", (txid,), fetchone=True):
        return await m.answer("⚠️ This Transaction ID has already been claimed in the system!", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    api_key_check = db_query("SELECT value FROM settings WHERE key='binance_api'", fetchone=True)
    secret_key_check = db_query("SELECT value FROM settings WHERE key='binance_secret'", fetchone=True)
    if not api_key_check or not secret_key_check:
        return await m.answer("⚠️ Binance API is missing on the server. Contact Support.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    await m.answer("🔄 <b>Verifying your TxID with Binance Blockchain...</b>\n<i>This may take up to 30 seconds...</i>", parse_mode='HTML')
    api_key = api_key_check[0]; secret_key = secret_key_check[0]
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {'X-MBX-APIKEY': api_key}
    url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{query_string}&signature={signature}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    try: history = await resp.json(content_type=None)
                    except: history = []
                    found = False
                    for deposit in history:
                        if deposit.get("txId") == txid and deposit.get("status") == 1:
                            found = True
                            usdt_amount = float(deposit.get("amount"))
                            inr_amount = usdt_amount * USDT_TO_INR
                            db_query("INSERT INTO crypto_txns (txid, user_id, amount_usdt, timestamp) VALUES (?, ?, ?, ?)", (txid, user_id, usdt_amount, int(time.time())))
                            db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (inr_amount, user_id))
                            await m.answer(f"🎉 <b>CRYPTO DEPOSIT SUCCESSFUL!</b>\n\n✅ We safely received <b>{usdt_amount} USDT</b>.\n💰 <b>{fmt_curr(inr_amount)}</b> has been added to your balance!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
                            await send_advanced_notification(user_id, "DEPOSIT", inr_amount, product=txid, gateway="Binance Crypto")
                            log_activity(user_id, "CRYPTO_DEPOSIT", f"TxID: {txid}, Amount: {inr_amount}")
                            await state.clear()
                            break
                    if not found: await m.answer("❌ <b>TxID Not Found or Still Pending!</b>\nMake sure the transaction is fully confirmed. Try again in 5 mins.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
                else: await m.answer(f"⚠️ <b>Binance Server Error:</b> HTTP {resp.status}.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
        except Exception as e: await m.answer(f"⚠️ <b>Connection Error:</b> {str(e)}", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')

# ==============================================================================
# 13. SHOP – with uppercase categories and new point_down emoji
# ==============================================================================
# ==============================================================================
# STORE SCREEN MEDIA (GIF / video shown above "Enter Premium Store")
# ==============================================================================
_STORE_MEDIA_MSGS: set = set()   # (chat_id, message_id) of messages that carry the store media

def _store_media() -> Tuple[str, str]:
    """(kind, file_id_or_url) from the admin setting, e.g. ('animation', 'AgAC...')."""
    raw = get_setting("store_media", "").strip()
    if not raw:
        legacy = get_setting("buy_loading_gif", "").strip()   # saved earlier via the old button
        if legacy and legacy.lower() not in ("none", "off"):
            return "animation", legacy
        return "", ""
    if raw.lower() in ("none", "off"):
        return "", ""
    kind, _, value = raw.partition("|")
    return (kind, value) if value else ("", "")

async def _send_store_media(target: Message, caption: str, kb: InlineKeyboardMarkup) -> Optional[Message]:
    kind, value = _store_media()
    if not kind:
        return None
    try:
        if kind == "video":
            sent = await target.answer_video(video=value, caption=caption, reply_markup=kb, parse_mode="HTML", supports_streaming=True)
        elif kind == "document":
            sent = await target.answer_document(document=value, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:  # animation / url
            sent = await target.answer_animation(animation=value, caption=caption, reply_markup=kb, parse_mode="HTML")
    except Exception:
        logger.warning("Store GIF/video failed to send, using plain text.", exc_info=True)
        return None
    _STORE_MEDIA_MSGS.add((sent.chat.id, sent.message_id))
    while len(_STORE_MEDIA_MSGS) > 500:
        _STORE_MEDIA_MSGS.pop()
    return sent

@dp.callback_query(F.data == "menu_shop")
async def view_shop_panels(call: CallbackQuery):
    log_activity(call.from_user.id, "VIEW_SHOP")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = get_ui_text("panel_select_menu", **get_user_profile_fields(call.from_user))
    for category_id, cat in get_shop_categories():
        count = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ? AND is_active=1", (cat + '%',), fetchone=True)[0]
        emoji_id = get_category_emoji(cat)
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✦ {cat.upper()} ✦", callback_data=f"catid_{category_id}", icon_custom_emoji_id=emoji_id, style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    kb = apply_button_theme(kb, "shop")

    # GIF / video on top of the store screen (set from admin: "Store GIF / Video").
    sent = await _send_store_media(call.message, text, kb)
    if sent:
        try:
            await call.message.delete()
        except Exception:
            pass
        return
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("catid_"))
async def view_panel_names(call: CallbackQuery):
    try:
        category_id = int(call.data.split("catid_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category selection.", show_alert=True)
    category_row = db_query(
        "SELECT name FROM product_categories WHERE id=? AND is_active=1",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not category_row:
        return await call.message.edit_text(
            "❌ <b>This category is no longer available.</b>",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    category = str(category_row[0])
    panel_names = db_query("SELECT DISTINCT panel_name FROM products WHERE category LIKE ? AND is_active=1 AND panel_name != ''", (category + '%',), fetchall=True)
    panel_names = sorted(panel_names or [], key=lambda row: natural_sort_key(row[0]))
    if not panel_names:
        prods = db_query("SELECT id, name, price_inr, stock, reseller_price, validity, device_limit, external_enabled FROM products WHERE category LIKE ? AND is_active=1", (category + '%',), fetchall=True)
        if not prods:
            await call.answer()
            return await call.message.edit_text(
                f"{get_emoji('product_store')} <b>{html.escape(category.upper())}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                "❌ <b>No products available in this category yet.</b>\nPlease check back later.",
                reply_markup=back_kb("menu_shop"), parse_mode='HTML')
        await show_products_for_panel(call, prods, category, panel_name=None)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"{get_emoji('product_store')} <b><u>{category.upper()} PANELS</u></b>\n━━━━━━━━━━━━━━━━━━\n\n{get_emoji('point_down')} <b>Choose a panel name:</b>"
    for pn in panel_names:
        panel = pn[0]
        emoji_id = get_panel_emoji(panel) or get_emoji_icon("product_store")
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✦ {panel} ✦", callback_data=f"pnl_{category[:30]}_{panel[:30]}", icon_custom_emoji_id=emoji_id, style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK TO PANELS", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=apply_button_theme(kb, "shop"), parse_mode='HTML')

@dp.callback_query(F.data.startswith("pnl_"))
async def view_products_for_panel(call: CallbackQuery):
    parts = call.data.split("pnl_", 1)[1].split("_", 1)
    if len(parts) != 2: return await call.answer("Invalid selection.", show_alert=True)
    category, panel_name = parts[0], parts[1]
    prods = db_query("SELECT id, name, price_inr, stock, reseller_price, validity, device_limit, external_enabled FROM products WHERE category LIKE ? AND panel_name LIKE ? AND is_active=1", (category + '%', panel_name + '%'), fetchall=True)
    if not prods:
        await call.answer()
        return await call.message.edit_text(
            f"{get_emoji('product_store')} <b>{html.escape(category.upper())} - {html.escape(panel_name)}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "❌ <b>No products available in this panel yet.</b>\nPlease check back later.",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    await show_products_for_panel(call, prods, f"{category} - {panel_name}", panel_name=panel_name, category=category)

def _build_panel_view(user_id: int, prods: List[Tuple], header: str, category: Optional[str] = None, panel_name: Optional[str] = None) -> Tuple[str, InlineKeyboardMarkup]:
    """Builds the (text, keyboard) for a panel's package list. Shared by the
    callback (pnl_) path and the direct /start deep-link path."""
    user = db_query("SELECT is_reseller, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    is_reseller = bool(user[0]) if user else False
    is_vip = bool(user[1]) if user else False
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"{get_emoji('product_store')} <b><u>{header.upper()} PACKAGES</u></b>\n━━━━━━━━━━━━━━━━━━\n\n"
    # Always show packages in natural A-Z / 1-2-10 order, independent of add time.
    prods = sorted(prods or [], key=lambda row: natural_sort_key(row[1]))
    for p in prods:
        prod_id, package_name, normal_price, stock, reseller_price, validity, device, external_enabled = p
        normal_price = float(normal_price) if normal_price is not None else 0.0
        reseller_price = float(reseller_price) if reseller_price is not None else 0.0
        base_price = reseller_price if is_reseller else normal_price
        if is_vip: display_price = base_price - (base_price * (VIP_DISCOUNT_PERCENTAGE / 100))
        else: display_price = base_price
        stock_status = "♾️ API Available" if external_enabled else ("✅ In Stock" if stock > 0 else "❌ Out of Stock")
        text += f"{get_emoji('product_store')} ⏱ <b>Validity: {package_name}</b>\n"
        if is_reseller or is_vip:
            text += f"💰 Regular Price: <s>{fmt_curr(normal_price)}</s>\n"
            if is_reseller and not is_vip: text += f"👑 <b>Reseller Price: {fmt_curr(display_price)}</b>\n"
            elif is_vip and not is_reseller: text += f"🌟 <b>VIP Price: {fmt_curr(display_price)}</b>\n"
            else: text += f"👑🌟 <b>Super Price: {fmt_curr(display_price)}</b>\n"
        else: text += f"💰 Price: {fmt_curr(normal_price)}\n"
        text += f"📱 Limit: {device} | 📦 {stock_status}\n\n"
        if external_enabled or stock > 0:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"✦ BUY {package_name} — {fmt_curr(display_price)} ✦", callback_data=f"buy_{prod_id}", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success")])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {package_name} (Out of Stock)", callback_data="ignore_stock_click", style="danger")])
    text += f"{get_emoji('point_down')} <b>Select package below to instantly purchase:</b>"
    if category and panel_name and is_admin_user(user_id):
        kb.inline_keyboard.append([InlineKeyboardButton(text="🔗 SHARE DIRECT LINK", callback_data=_share_callback(category, panel_name), style="secondary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK TO PANELS", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    kb = apply_button_theme(kb, "shop")
    return text, kb

def _share_callback(category: str, panel: str) -> str:
    """callback_data for the 'share direct link' button, max 64 bytes."""
    cat, pan = category[:25], panel[:25]
    data = f"shr_{cat}_{pan}"
    while len(data.encode("utf-8")) > 64 and pan:
        pan = pan[:-1]
        data = f"shr_{cat}_{pan}"
    return data

async def show_products_for_panel(call: CallbackQuery, prods: List[Tuple], header: str, panel_name: Optional[str] = None, category: Optional[str] = None):
    text, kb = _build_panel_view(call.from_user.id, prods, header, category=category, panel_name=panel_name)

    video_id = get_panel_video(panel_name) if panel_name else ""
    if video_id:
        msg = call.message
        # Already showing this panel's video (e.g. refresh after a qty screen): just swap caption/buttons.
        if getattr(msg, "video", None):
            try:
                await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
                return
            except TelegramBadRequest as exc:
                if "not modified" in str(exc).lower():
                    return
        # A text/GIF screen can't turn into a video, so send a fresh video and remove the old message.
        try:
            await msg.answer_video(video=video_id, caption=text, reply_markup=kb, parse_mode="HTML", supports_streaming=True)
            try:
                await msg.delete()
            except Exception:
                pass
            return
        except Exception:
            logger.warning("Panel gameplay video send failed, falling back to text.", exc_info=True)

    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

async def send_panel_products_message(message: Message, prods: List[Tuple], header: str, panel_name: Optional[str] = None, category: Optional[str] = None):
    """Same package-list view as show_products_for_panel, but sent as a fresh
    message (used by the /start deep-link path, where there's no callback to edit)."""
    text, kb = _build_panel_view(message.from_user.id, prods, header, category=category, panel_name=panel_name)
    video_id = get_panel_video(panel_name) if panel_name else ""
    if video_id:
        try:
            await message.answer_video(video=video_id, caption=text, reply_markup=kb, parse_mode="HTML", supports_streaming=True)
            return
        except Exception:
            logger.warning("Panel gameplay video send failed, falling back to text.", exc_info=True)
    await message.answer(text, reply_markup=kb, parse_mode='HTML')

async def open_panel_by_tag(message: Message, category: str, panel_name: str) -> bool:
    """Looks up products for a category+panel and sends the package list directly.
    Returns False (and sends a fallback) if the tagged product no longer exists."""
    prods = db_query(
        "SELECT id, name, price_inr, stock, reseller_price, validity, device_limit, external_enabled "
        "FROM products WHERE category LIKE ? AND panel_name LIKE ? AND is_active=1",
        (category + '%', panel_name + '%'), fetchall=True)
    if not prods:
        await message.answer(
            f"❌ <b>This product link is no longer available.</b>\nOpening the shop menu instead.",
            parse_mode='HTML')
        return False
    await send_panel_products_message(message, prods, f"{category} - {panel_name}", panel_name=panel_name, category=category)
    return True

@dp.callback_query(F.data.startswith("shr_"))
async def share_panel_link(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ This is an admin-only feature.", show_alert=True)
    parts = call.data.split("shr_", 1)[1].split("_", 1)
    if len(parts) != 2:
        return await call.answer("Could not build link.", show_alert=True)
    category, panel_name = parts[0], parts[1]
    # Look up the full (untruncated) category/panel from a matching product,
    # since callback_data may have been shortened to fit 64 bytes.
    row = db_query("SELECT category, panel_name FROM products WHERE category LIKE ? AND panel_name LIKE ? LIMIT 1",
                    (category + '%', panel_name + '%'), fetchone=True)
    if row:
        category, panel_name = row[0], row[1]
    link = panel_deep_link(category, panel_name)
    await call.answer()
    await call.message.answer(
        f"🔗 <b>Direct link for this product:</b>\n<code>{html.escape(link)}</code>\n\n"
        f"Anyone who opens this link will land straight on the <b>{html.escape(panel_name)}</b> packages.",
        parse_mode='HTML')

@dp.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery):
    await call.answer()


@dp.callback_query(F.data == "ignore_stock_click")
async def ignore_stock_click(call: CallbackQuery):
    await call.answer("⚠️ This duration is completely Out of Stock! Admins have been notified to refill.", show_alert=True)

PRODUCT_QTY_OPTIONS = (1, 2, 4, 6)

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(call: CallbackQuery, state: FSMContext = None):
    """Tap a product -> choose how many keys (1/2/4/6) -> automatic wallet + QR checkout."""
    try:
        prod_id = int(call.data.split("_", 1)[1])
    except (ValueError, IndexError):
        return await call.answer("❌ Invalid product.", show_alert=True)

    # If this product has an active coupon and the buyer hasn't already been
    # offered it in this session, show a one-time "apply coupon?" screen
    # before going into qty-selection/checkout. Products with no coupon
    # configured skip this entirely (unchanged old behaviour).
    coupon_row = db_query(
        "SELECT code, discount_percent, uses_left FROM product_coupons WHERE product_id=? AND uses_left > 0",
        (prod_id,), fetchone=True, commit=False,
    )
    fsm_data = await state.get_data() if state else {}
    already_offered = fsm_data.get('coupon_offered_prod') == prod_id
    if coupon_row and not already_offered and state:
        pricing_preview = get_purchase_pricing(call.from_user.id, prod_id)
        if not pricing_preview:
            return await call.answer("❌ Product not found.", show_alert=True)
        prod_preview, _, unit_price_preview = pricing_preview
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏷 Have a Coupon Code?", callback_data=f"buycoupon_enter_{prod_id}", style="success")],
            [InlineKeyboardButton(text="➡️ Continue Without Coupon", callback_data=f"buycoupon_skip_{prod_id}", style="primary")],
            [InlineKeyboardButton(text="BACK", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        ])
        await call.answer()
        await call.message.edit_text(
            f"📦 <b>{html.escape(str(prod_preview[0]))}</b>\n"
            f"💰 Price: {fmt_curr(unit_price_preview)}\n\n"
            "🎉 Is product pe ek discount coupon available hai! Code hai to daalo, warna bina coupon ke aage badho.",
            reply_markup=kb, parse_mode='HTML',
        )
        return

    unit_price_override = None
    if fsm_data.get('coupon_applied_prod') == prod_id:
        unit_price_override = float(fsm_data.get('coupon_unit_price', 0) or 0) or None

    pricing = get_purchase_pricing(call.from_user.id, prod_id)
    if not pricing:
        return await call.answer("❌ Product not found.", show_alert=True)
    prod, user, unit_price = pricing
    if unit_price_override is not None:
        unit_price = unit_price_override
    external = bool(prod[8])
    max_qty = max(PRODUCT_QTY_OPTIONS) if external else min(max(PRODUCT_QTY_OPTIONS), int(prod[3] or 0))
    options = [q for q in PRODUCT_QTY_OPTIONS if q <= max_qty]
    if not options:
        return await call.answer("❌ This product is out of stock.", show_alert=True)
    if options == [1]:
        return await start_product_checkout(call, prod_id, 1, unit_price_override=unit_price_override)

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    row = []
    for qty in options:
        row.append(InlineKeyboardButton(
            text=f"{qty} Key{'s' if qty != 1 else ''} — {fmt_curr(unit_price * qty)}",
            callback_data=f"qty_{prod_id}_{qty}", style="primary"))
        if len(row) == 2:
            kb.inline_keyboard.append(row); row = []
    if row:
        kb.inline_keyboard.append(row)
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    text = (
        f"🛒 <b>SELECT QUANTITY</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>{html.escape(str(prod[0]))}</b>\n"
        f"⏱ <b>Validity:</b> {html.escape(str(prod[4] or ''))}\n"
        f"💰 <b>Price per key:</b> {fmt_curr(unit_price)}\n\n"
        "👇 Choose how many keys you want:"
    )
    await call.message.edit_text(text, reply_markup=apply_button_theme(kb, "shop"), parse_mode='HTML')


@dp.callback_query(F.data.startswith("buycoupon_skip_"))
async def buy_coupon_skip(call: CallbackQuery, state: FSMContext):
    prod_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(coupon_offered_prod=prod_id)
    await process_buy(call, state)


@dp.callback_query(F.data.startswith("buycoupon_enter_"))
async def buy_coupon_enter(call: CallbackQuery, state: FSMContext):
    prod_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(coupon_offered_prod=prod_id, coupon_target_prod=prod_id)
    await call.message.edit_text("🏷 Apna coupon code bhejo:", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_buy_coupon_code)
    await call.answer()


@dp.message(AdminStates.wait_for_buy_coupon_code)
async def buy_coupon_code_entered(m: Message, state: FSMContext):
    data = await state.get_data()
    prod_id = data.get('coupon_target_prod')
    code = (m.text or "").strip().upper()
    row = db_query(
        "SELECT discount_percent, uses_left FROM product_coupons WHERE code=? AND product_id=?",
        (code, prod_id), fetchone=True, commit=False,
    )
    if not row or int(row[1]) <= 0:
        await state.set_state(None)
        return await m.answer(
            "❌ Invalid ya expired coupon code hai is product ke liye.",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML',
        )
    pct = float(row[0])
    pricing = get_purchase_pricing(m.from_user.id, prod_id)
    if not pricing:
        await state.clear()
        return await m.answer("❌ Product not found.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    _, _, unit_price = pricing
    discounted_price = round(unit_price * (1 - pct / 100.0), 2)
    # Reserved on entry rather than on final payment success, so an
    # abandoned/failed order after this point still consumes one use.
    db_query("UPDATE product_coupons SET uses_left = uses_left - 1 WHERE code=? AND product_id=? AND uses_left > 0",
              (code, prod_id))
    await state.update_data(coupon_applied_prod=prod_id, coupon_unit_price=discounted_price)
    await state.set_state(None)
    await m.answer(
        f"✅ Coupon <b>{html.escape(code)}</b> applied! {pct:g}% off — naya price: {fmt_curr(discounted_price)}\n\n"
        "👉 Ab <b>product par dobara tap karo</b> (Shop se) — discount automatically lag jayega.",
        parse_mode='HTML',
    )


def get_purchase_pricing(user_id: int, prod_id: int):
    row = db_query("""
        SELECT p.name, p.price_inr, p.reseller_price, p.stock, p.validity, p.device_limit,
               p.category, p.panel_name, p.external_enabled, p.external_product_id, p.external_duration, p.apk_link
        FROM products p WHERE p.id=? AND p.is_active=1
    """, (prod_id,), fetchone=True)
    user = db_query("SELECT balance, referred_by, is_reseller, total_saved, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not row or not user:
        return None
    normal = float(row[1] or 0)
    reseller = float(row[2] or 0)
    base = reseller if bool(user[2]) else normal
    unit = base - (base * VIP_DISCOUNT_PERCENTAGE / 100) if bool(user[4]) else base
    return row, user, unit


def acquire_purchase_lock(user_id: int, product_id: int, quantity: int, ttl: int = 1200) -> bool:
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM purchase_locks WHERE expires_at < ?", (now,))
        existing = conn.execute("SELECT user_id FROM purchase_locks WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO purchase_locks(user_id, product_id, quantity, created_at, expires_at) VALUES(?,?,?,?,?)",
            (user_id, product_id, quantity, now, now + ttl)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        logger.exception("Could not acquire purchase lock")
        return False
    finally:
        conn.close()


def release_purchase_lock(user_id: int) -> None:
    db_query("DELETE FROM purchase_locks WHERE user_id=?", (user_id,))


async def _api_key_with_timer(product_id: str, duration: str, message: Message, index: int, quantity: int, panel_name: str = ""):
    """Run one non-idempotent API BUY while showing a live wait timer."""
    task = asyncio.create_task(fetch_external_key(product_id, duration, "", panel_name))
    started = time.monotonic()
    while not task.done():
        elapsed = int(time.monotonic() - started)
        mm, ss = divmod(elapsed, 60)
        try:
            await message.edit_text(
                f"⏳ <b>WAIT FOR KEY</b>\n\nGenerating key <b>{index}/{quantity}</b>\n"
                f"🕐 Time: <b>{mm:02d}:{ss:02d}</b>\n"
                "🔒 Please do not click again — this order is locked.", parse_mode='HTML'
            )
        except Exception:
            pass
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except asyncio.TimeoutError:
            continue
    return await task


async def generate_product_keys(prod_id: int, quantity: int, external_enabled: bool, external_product_id: str, api_duration: str, update_message: Message, panel_name: str = ""):
    """Generate exactly `quantity` keys. This function is only called after a purchase is locked."""
    keys = []
    for index in range(1, quantity + 1):
        await update_message.edit_text(
            f"⏳ <b>WAIT FOR KEY</b>\n\nGenerating key <b>{index}/{quantity}</b>...\n"
            "🔒 Duplicate clicks are disabled until this process finishes.", parse_mode='HTML'
        )
        if external_enabled:
            response = await _api_key_with_timer(external_product_id, api_duration, update_message, index, quantity, panel_name)
            if response.get("status") != "success":
                return (keys if keys else None), response.get("msg", "Unknown API error")
            key = response.get("key")
            if isinstance(key, list):
                key = "\n".join(str(x) for x in key)
            if key is None or str(key).strip() in ("", "KEY_NOT_FOUND"):
                return None, "API returned no key"
            keys.append(str(key))
        else:
            key_data = db_query("SELECT id, key_text FROM product_keys WHERE product_id=? AND is_used=0 LIMIT 1", (prod_id,), fetchone=True)
            if not key_data:
                return (keys if keys else None), "No manual key is available"
            keys.append(str(key_data[1]))
            db_query("UPDATE product_keys SET is_used=1 WHERE id=? AND is_used=0", (key_data[0],))
            db_query("UPDATE products SET stock=CASE WHEN stock>0 THEN stock-1 ELSE 0 END WHERE id=?", (prod_id,))
        # Small UI pause so the user sees the per-key progress instead of a frozen screen.
        if index < quantity:
            await asyncio.sleep(1)
    return keys, None


async def fulfill_product_transaction(order_id: str, user_id: int, reply_message: Message = None) -> bool:
    """Atomically claim a paid product transaction so it can only deliver once."""
    txn = db_query("SELECT amount_inr, status, timestamp, product_id, quantity, payment_method, wallet_used FROM transactions WHERE order_id=? AND user_id=?", (order_id, user_id), fetchone=True)
    if not txn:
        return False
    amount, status, ts, prod_id, quantity, payment_method, wallet_used = txn
    # Direct-QR orders can be paid partly from the wallet (reserved at order time) and partly by QR.
    wallet_used = float(wallet_used or 0)
    paid_total = float(amount) + wallet_used
    if status == 'completed':
        return True
    if status != 'paid':
        return False
    claimed = db_update_count("UPDATE transactions SET status='processing' WHERE order_id=? AND user_id=? AND status='paid'", (order_id, user_id))
    check = db_query("SELECT status FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
    if not claimed and (not check or check[0] != 'processing'):
        return False
    if check and check[0] == 'processing' and not claimed:
        return False

    await _drop_qr_message(order_id, keep=reply_message)
    pricing = get_purchase_pricing(user_id, int(prod_id))
    if not pricing:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (paid_total, user_id))
        release_purchase_lock(user_id)
        return False
    prod, user, unit_price = pricing
    expected = unit_price * int(quantity or 1)
    if abs(paid_total - expected) > 0.01:
        # Price changed while the user was paying: never keep the money, return it to the wallet.
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (paid_total, user_id))
        release_purchase_lock(user_id)
        try:
            if reply_message:
                await reply_message.edit_text(
                    f"⚠️ <b>Price changed while you were paying.</b>\n\n💰 {fmt_curr(paid_total)} has been credited to your wallet. Please try again.",
                    reply_markup=back_kb("menu_shop"), parse_mode='HTML')
        except Exception:
            pass
        return False

    external_enabled = bool(prod[8])
    external_product_id = str(prod[9] or '').strip()
    panel_name = str(prod[7] or '').strip()
    # Keep the exact duration configured for the product first.  Some reseller
    # APIs use the package label verbatim (for example ``3 HOUR``) rather than
    # the normalized ``3 Hours`` spelling.  The old code normalized every value
    # before sending it, which could make the API reply ``Price not found`` even
    # though the matching price existed under the original label.
    duration_candidates = []

    def add_duration_candidate(value: Any) -> None:
        value = re.sub(r"\s+", " ", str(value or "").strip())
        if value and value not in duration_candidates:
            duration_candidates.append(value)

    raw_external_duration = str(prod[10] or "").strip()
    raw_validity = str(prod[4] or "").strip()
    raw_product_name = str(prod[0] or "").strip()

    # Build every common spelling used by reseller_v1.php.  In particular,
    # some panels store the tier as ``3 HOUR`` while others use ``3 Hours``.
    # A BUY request is non-idempotent, so these fallbacks are ONLY used after
    # the API explicitly says that the price tier was not found.
    for raw in (raw_product_name, raw_external_duration, raw_validity):
        add_duration_candidate(raw)
        normalized = normalize_api_duration(raw)
        add_duration_candidate(normalized)
        m = re.match(r"^(\d+)\s*(hours?|hrs?|h)$", raw, re.IGNORECASE)
        if m:
            n = m.group(1)
            for unit in ("HOUR", "HOURS", "Hour", "Hours", "hour", "hours", "HR", "Hrs", "hr"):
                add_duration_candidate(f"{n} {unit}")
        m = re.match(r"^(\d+)\s*(days?|d)$", raw, re.IGNORECASE)
        if m:
            n = m.group(1)
            for unit in ("DAY", "DAYS", "Day", "Days", "DaYs", "day", "days", "d"):
                add_duration_candidate(f"{n} {unit}")

    # If the saved duration is numeric, explicitly create hour/day aliases
    # from the product name too (e.g. name=3 HOUR, duration=3).
    m = re.match(r"^(\d+)\s*(hour|hours|hr|hrs|h)$", raw_product_name, re.IGNORECASE)
    if m:
        n = m.group(1)
        for unit in ("HOUR", "HOURS", "Hour", "Hours"):
            add_duration_candidate(f"{n} {unit}")
    m = re.match(r"^(\d+)\s*(day|days|d)$", raw_product_name, re.IGNORECASE)
    if m:
        n = m.group(1)
        for unit in ("DAY", "DAYS", "Day", "Days", "DaYs"):
            add_duration_candidate(f"{n} {unit}")
    if external_enabled and not external_product_id:
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (paid_total, user_id))
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        release_purchase_lock(user_id)
        return False

    if reply_message is None:
        reply_message = await bot.send_message(user_id, "⏳ <b>Preparing your keys...</b>", parse_mode='HTML')
    # For API products, a duration mismatch is safe to try with the next configured
    # candidate. Any other API error stops immediately because BUY is non-idempotent.
    keys = []
    error = None
    if external_enabled:
        for candidate_duration in duration_candidates:
            one_keys, one_error = await generate_product_keys(int(prod_id), int(quantity), True, external_product_id, candidate_duration, reply_message, panel_name)
            if one_keys:
                keys, error = one_keys, one_error
                # Never retry another duration after any key was actually generated.
                # The BUY endpoint is non-idempotent and a retry could consume extra keys.
                break
            error = one_error
            if 'price not found' not in str(one_error).lower() and 'price_not_found' not in str(one_error).lower():
                break
    else:
        keys, error = await generate_product_keys(int(prod_id), int(quantity), False, external_product_id, '', reply_message)
    if not keys:
        # If a direct payment succeeded but key generation fails, preserve the money
        # by crediting it to the user's wallet rather than silently losing it.
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (float(amount), user_id))
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        release_purchase_lock(user_id)
        logger.warning("Key generation failed | panel=%s prod_id=%s order=%s error=%s", panel_name or "(default)", prod_id, order_id, error)
        try:
            await bot.send_message(
                get_owner_id(),
                f"⚠️ <b>Upstream key generation failed</b>\n"
                f"Panel: <code>{html.escape(panel_name or 'Default/Fallback')}</code>\n"
                f"Product ID: <code>{prod_id}</code> (internal)\n"
                f"External PID sent: <code>{html.escape(external_product_id or '(empty)')}</code>\n"
                f"Order: <code>{order_id}</code>\n"
                f"Error: {html.escape(str(error or 'Unknown error'))}\n\n"
                "Check that panel's API URL/Key/Master in 🔗 External Key API — "
                "buyer's money was refunded to their wallet.",
                parse_mode='HTML',
            )
        except Exception:
            pass
        try:
            await reply_message.edit_text(f"❌ <b>Key generation failed:</b> {html.escape(error or 'Unknown error')}\n\n💰 The paid amount has been credited to your wallet.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
        except Exception:
            pass
        return False

    generated_qty = len(keys)
    delivered_key = "\n".join(f"{i+1}. {k}" for i, k in enumerate(keys))
    actual_amount = unit_price * generated_qty
    remainder = max(0.0, paid_total - actual_amount)
    if remainder > 0.01:
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (remainder, user_id))
    savings = float(prod[1] or 0) * generated_qty - actual_amount
    db_query("UPDATE users SET spent=spent+?, orders_count=orders_count+1, total_saved=total_saved+? WHERE user_id=?", (actual_amount, savings, user_id))
    if user[1]:
        commission = actual_amount * 0.15
        db_query("UPDATE users SET balance=balance+?, referral_earned=referral_earned+? WHERE user_id=?", (commission, commission, user[1]))
        try:
            await bot.send_message(user[1], f"🎁 <b>Referral Bonus Added!</b>\nYou earned {fmt_curr(commission)} from a successful purchase.", parse_mode='HTML')
        except Exception:
            pass

    product_full_name = f"{prod[6]} - {prod[7]} ({prod[0]}) x{quantity}"
    db_query("INSERT INTO orders (user_id, product_name, price_paid, delivered_key, purchase_date) VALUES (?, ?, ?, ?, ?)",
             (user_id, product_full_name, actual_amount, delivered_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db_query("UPDATE transactions SET status='completed' WHERE order_id=? AND status='processing'", (order_id,))
    release_purchase_lock(user_id)
    log_activity(user_id, "PURCHASE_SUCCESS", f"Product: {product_full_name}, Paid: {actual_amount}, Keys: {generated_qty}, Payment: {payment_method}")
    await send_advanced_notification(user_id, "ORDER", actual_amount, product=product_full_name, key=delivered_key)

    msg = (f"✅ <b>PURCHASE SUCCESSFUL!</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"📦 <b>Panel:</b> {html.escape(str(prod[6]))}\n📁 <b>Panel Name:</b> {html.escape(str(prod[7]))}\n"
           f"⏱ <b>Package:</b> {html.escape(str(prod[0]))}\n🔢 <b>Quantity:</b> {generated_qty}/{quantity}\n"
           f"💰 <b>Amount Used:</b> {fmt_curr(actual_amount)}\n📱 <b>Device Limit:</b> {html.escape(str(prod[5]))}\n"
           "━━━━━━━━━━━━━━━━━━\n")
    if prod[11] and str(prod[11]).startswith('http'):
        msg += f"📥 <b>APK Link:</b> <a href='{html.escape(str(prod[11]), quote=True)}'>Click Here to Download</a>\n\n"
    msg += f"🔑 <b>Your Keys:</b>\n<code>{html.escape(delivered_key)}</code>\n"
    if remainder > 0.01:
        msg += f"\n💰 <b>Unused amount credited to wallet:</b> {fmt_curr(remainder)}\n"
    msg += f"\n<i>For any issues, tap Support or contact: {ADMIN_CONTACT}</i>"
    try:
        await reply_message.edit_text(msg, reply_markup=back_kb("menu_shop"), disable_web_page_preview=True, parse_mode='HTML')
    except Exception:
        await bot.send_message(user_id, msg, reply_markup=back_kb("menu_shop"), disable_web_page_preview=True, parse_mode='HTML')
    return True


def _product_qr_gateway() -> Optional[str]:
    """Gateway used for a direct product QR. FamPay gives a real QR image, so it comes first."""
    if fampay_ready():
        return "fampay"
    if zapupi_ready():
        return "zapupi"
    return None

async def _supersede_pending_product_orders(user_id: int) -> bool:
    """Tapping a product again replaces the user's old unpaid QR (there is no Cancel button).

    Returns False when the old QR turned out to be already paid, in which case no new
    order must be started: the key of the paid order is being delivered.
    """
    rows = db_query(
        "SELECT order_id, payment_method FROM transactions WHERE user_id=? AND purpose='product' AND status='pending'",
        (user_id,), fetchall=True) or []
    cancelled_any = False
    for order_id, method in rows:
        # Last-second check so a QR the user just paid is never thrown away.
        try:
            if method == "fampay":
                await verify_fampay_order(user_id, order_id, background=True)
            else:
                await run_payment_verification(user_id, order_id, background=True)
        except Exception:
            logger.exception("Pre-cancel verification failed for %s", order_id)
        latest = db_query("SELECT status FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
        if latest and latest[0] != "pending":
            if latest[0] in ("paid", "processing", "completed"):
                return False
            continue
        if db_update_count("UPDATE transactions SET status='cancelled' WHERE order_id=? AND status='pending'", (order_id,)):
            refund_reserved_wallet(order_id)
            await _drop_qr_message(order_id)
            cancelled_any = True
    if cancelled_any:
        release_purchase_lock(user_id)
    return True

async def create_product_qr_order(user_id: int, prod_id: int, quantity: int, message_obj: Message, unit_price_override: float = None) -> None:
    """Direct payment QR for exactly the product price (wallet is not touched)."""
    pricing = get_purchase_pricing(user_id, prod_id)
    if not pricing:
        return await message_obj.edit_text("❌ Product not found.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    prod, user, unit_price = pricing
    if unit_price_override is not None:
        unit_price = unit_price_override
    total = round(unit_price * quantity, 2)
    gateway = _product_qr_gateway()
    if gateway == "zapupi":
        return await create_product_upi_order(user_id, prod_id, quantity, message_obj, unit_price_override=unit_price_override)
    if gateway != "fampay":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="BACK", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        ])
        return await message_obj.edit_text(
            "⚠️ <b>Payment abhi available nahi hai.</b>\n"
            "Please thodi der baad try karo ya support se contact karo.",
            reply_markup=apply_button_theme(kb, "shop"), parse_mode='HTML')
    if not acquire_purchase_lock(user_id, prod_id, quantity):
        return await message_obj.edit_text(
            "⏳ <b>An order is already processing.</b>\nPehle purana payment complete ya cancel karo.",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    label = f"{prod[6]} - {prod[7]} ({prod[0]})" if prod[7] else f"{prod[6]} ({prod[0]})"
    if quantity > 1:
        label += f" x{quantity}"
    wallet_part, qr_amount = split_wallet_and_qr(wallet_share_for_purchase(user[0]), total)
    await generate_fampay_order(
        user_id, qr_amount, message_obj, product_id=prod_id, quantity=quantity,
        product_label=label, wallet_part=wallet_part, product_total=total)

async def pay_product_with_wallet(call: CallbackQuery, prod_id: int, quantity: int, unit_price_override: float = None) -> None:
    pricing = get_purchase_pricing(call.from_user.id, prod_id)
    if not pricing:
        return await _safe_answer(call, "❌ Product not found.", show_alert=True)
    prod, user, unit_price = pricing
    if unit_price_override is not None:
        unit_price = unit_price_override
    total = unit_price * quantity
    if float(user[0] or 0) < total:
        return await _safe_answer(call, f"❌ Insufficient wallet balance. Need {fmt_curr(total)}.", show_alert=True)
    if not acquire_purchase_lock(call.from_user.id, prod_id, quantity):
        return await _safe_answer(call, "⏳ This order is already processing. Please wait for the key.", show_alert=True)

    order_id = f"WAL{call.from_user.id}{int(time.time())}{random.randint(100,999)}"
    deducted = db_update_count("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (total, call.from_user.id, total))
    if not deducted:
        release_purchase_lock(call.from_user.id)
        return await _safe_answer(call, "❌ Balance changed. Please try again.", show_alert=True)
    db_query("INSERT INTO transactions(order_id,user_id,amount_inr,status,timestamp,purpose,product_id,quantity,payment_method) VALUES(?,?,?,?,?,?,?,?,?)",
             (order_id,call.from_user.id,total,'paid',int(time.time()),'product',prod_id,quantity,'wallet'))
    await _safe_answer(call, "⏳ Payment received. Generating keys...", show_alert=False)
    await fulfill_product_transaction(order_id, call.from_user.id, call.message)

@dp.callback_query(F.data.startswith("qty_"))
async def choose_quantity(call: CallbackQuery, state: FSMContext):
    try:
        _, prod_id_s, qty_s = call.data.split("_", 2)
        prod_id, quantity = int(prod_id_s), int(qty_s)
    except (ValueError, TypeError):
        return await call.answer("❌ Invalid quantity.", show_alert=True)
    if quantity < 1 or quantity > max(PRODUCT_QTY_OPTIONS):
        return await call.answer("❌ Invalid quantity.", show_alert=True)
    fsm_data = await state.get_data()
    unit_price_override = None
    if fsm_data.get('coupon_applied_prod') == prod_id:
        unit_price_override = float(fsm_data.get('coupon_unit_price', 0) or 0) or None
    await start_product_checkout(call, prod_id, quantity, unit_price_override=unit_price_override)

async def start_product_checkout(call: CallbackQuery, prod_id: int, quantity: int = 1, unit_price_override: float = None):
    """Automatic checkout: wallet first (fully or partly), the QR only covers what is left."""
    if quantity < 1 or quantity > max(PRODUCT_QTY_OPTIONS):
        return await call.answer("❌ Invalid quantity.", show_alert=True)

    # No Cancel button exists, so tapping again replaces the user's old unpaid QR.
    answered = False
    if db_query("SELECT 1 FROM transactions WHERE user_id=? AND purpose='product' AND status='pending'",
                (call.from_user.id,), fetchone=True):
        await _safe_answer(call)
        answered = True
        if not await _supersede_pending_product_orders(call.from_user.id):
            return await call.message.edit_text(
                "✅ <b>Aapka pichla payment mil gaya hai.</b>\nKey abhi bhej raha hoon, thoda wait karo.",
                reply_markup=back_kb("menu_shop"), parse_mode='HTML')

    pricing = get_purchase_pricing(call.from_user.id, prod_id)
    if not pricing:
        return await _safe_answer(call, "❌ Product or user not found.", show_alert=True)
    prod, user, unit_price = pricing
    if unit_price_override is not None:
        unit_price = unit_price_override
    if not bool(prod[8]) and int(prod[3] or 0) < quantity:
        return await _safe_answer(call, "❌ Not enough keys in stock.", show_alert=True)
    total = round(unit_price * quantity, 2)
    balance = wallet_share_for_purchase(user[0])

    if balance >= total:
        return await pay_product_with_wallet(call, prod_id, quantity, unit_price_override=unit_price_override)

    wallet_part, qr_amount = split_wallet_and_qr(balance, total)
    if not answered:
        if wallet_part > 0:
            await _safe_answer(call, f"👛 Wallet se {fmt_curr(wallet_part)} + QR se {fmt_curr(qr_amount)}")
        else:
            await _safe_answer(call, f"💳 {fmt_curr(qr_amount)} ka direct QR ban raha hai…")
    await premium_click_animation(call.message, "Preparing your direct QR")
    await create_product_qr_order(call.from_user.id, prod_id, quantity, call.message, unit_price_override=unit_price_override)


async def create_product_upi_order(user_id: int, prod_id: int, quantity: int, message_obj: Message, unit_price_override: float = None):
    if not gateway_enabled("zapupi"):
        return await message_obj.edit_text(
            "⚠️ ZapUPI Gateway is currently OFF from /admin.",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML'
        )
    pricing = get_purchase_pricing(user_id, prod_id)
    if not pricing:
        return await message_obj.edit_text("❌ Product not found.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    prod, user, unit_price = pricing
    if unit_price_override is not None:
        unit_price = unit_price_override
    total = round(unit_price * quantity, 2)
    # Wallet pays what it can; the QR only covers the remaining amount.
    wallet_part, qr_amount = split_wallet_and_qr(wallet_share_for_purchase(user[0]), total)
    if not acquire_purchase_lock(user_id, prod_id, quantity):
        return await message_obj.edit_text("⏳ <b>An order is already processing.</b> Please finish or wait for the current order.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

    api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
    if not api_key_check or not api_key_check[0]:
        release_purchase_lock(user_id)
        return await message_obj.edit_text("⚠️ UPI Gateway is currently offline.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

    order_id = f"PRD{user_id}{int(time.time())}{random.randint(100,999)}"
    phone_row = db_query("SELECT phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    mobile = phone_row[0] if phone_row and phone_row[0] else "9999999999"
    now = int(time.time())
    db_query("INSERT INTO transactions(order_id,user_id,amount_inr,status,timestamp,purpose,product_id,quantity,payment_method) VALUES(?,?,?,?,?,?,?,?,?)",
             (order_id,user_id,qr_amount,'pending',now,'product',prod_id,quantity,'upi'))
    gateway_qr_url = ""
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            payload = {"zap_key": api_key_check[0], "order_id": order_id, "amount": f"{qr_amount:.2f}", "customer_mobile": mobile,
                       "remark": f"Product {prod_id} x{quantity}", "success_url": f"https://t.me/{BOT_USERNAME}?start=v_{order_id}",
                       "failed_url": f"https://t.me/{BOT_USERNAME}", "timeout_url": f"https://t.me/{BOT_USERNAME}", "webhook_url": f"https://t.me/{BOT_USERNAME}"}
            async with session.post("https://pay.zapupi.com/api/create-order", json=payload) as resp:
                data = await resp.json(content_type=None)
                if resp.status < 200 or resp.status >= 300 or str(data.get('status', '')).lower() != 'success':
                    raise RuntimeError(data.get('message', f'HTTP {resp.status}'))
                response_data = data.get("data") if isinstance(data.get("data"), dict) else data
                payment_url = response_data.get('payment_url') or response_data.get('payment_link') or ""
                gateway_qr_url = response_data.get('qr_url') or response_data.get('qr') or ""
                if not payment_url and not gateway_qr_url:
                    raise RuntimeError('Gateway did not return a payment URL or QR')
    except asyncio.TimeoutError:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        release_purchase_lock(user_id)
        return await message_obj.edit_text(
            "❌ <b>ZapUPI timed out.</b>\nPlease try again in a moment.",
            reply_markup=back_kb("menu_shop"),
            parse_mode='HTML',
        )
    except aiohttp.ClientError:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        release_purchase_lock(user_id)
        return await message_obj.edit_text(
            "❌ <b>Could not connect to ZapUPI.</b>\nPlease try again in a moment.",
            reply_markup=back_kb("menu_shop"),
            parse_mode='HTML',
        )
    except Exception as exc:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
        release_purchase_lock(user_id)
        return await message_obj.edit_text(f"❌ <b>UPI order failed:</b> {html.escape(str(exc))}", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

    # The QR exists now, so reserve the wallet part (a gateway failure above never touches the wallet).
    if wallet_part > 0:
        if not db_update_count("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (wallet_part, user_id, wallet_part)):
            db_query("UPDATE transactions SET status='failed' WHERE order_id=?", (order_id,))
            release_purchase_lock(user_id)
            return await message_obj.edit_text(
                "❌ <b>Wallet balance changed.</b>\nPlease tap the product again.",
                reply_markup=back_kb("menu_shop"), parse_mode='HTML')
        db_query("UPDATE transactions SET wallet_used=? WHERE order_id=?", (wallet_part, order_id))

    buttons = []
    if payment_url:
        # Real ZapUPI hosted page (live UPI QR), opened inside Telegram.
        buttons.append([InlineKeyboardButton(text="💳 PAY NOW", web_app=WebAppInfo(url=payment_url))])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    label = f"{prod[6]} - {prod[7]} ({prod[0]})" if prod[7] else f"{prod[6]} ({prod[0]})"
    if quantity > 1:
        label += f" x{quantity}"
    text = _direct_purchase_caption(label, total, order_id, wallet_used=wallet_part, pay_amount=qr_amount)
    # QR image sent straight in chat: the gateway's own QR when it gives one,
    # otherwise a QR that opens the gateway's live payment page for this order.
    qr_display_url = get_direct_qr_url(qr_amount, gateway_qr_url=gateway_qr_url, payment_url=payment_url, order_id=order_id)
    try:
        sent = await show_payment_invoice(message_obj, text, kb, qr_display_url)
    except Exception:
        logger.warning("Could not send the QR image; falling back to a text invoice.", exc_info=True)
        sent = await show_payment_invoice(message_obj, text, kb, "")
    _remember_qr_message(order_id, sent)
    schedule_auto_verification(user_id, order_id, "upi")


@dp.callback_query(F.data.startswith("walletpay_"))
async def wallet_product_payment(call: CallbackQuery):
    try:
        _, prod_id_s, qty_s = call.data.split("_", 2)
        prod_id, quantity = int(prod_id_s), int(qty_s)
    except (ValueError, TypeError):
        return await call.answer("❌ Invalid order.", show_alert=True)
    await pay_product_with_wallet(call, prod_id, quantity)


@dp.callback_query(F.data.startswith("upipay_"))
async def upi_product_payment(call: CallbackQuery):
    try:
        _, prod_id_s, qty_s = call.data.split("_", 2)
        prod_id, quantity = int(prod_id_s), int(qty_s)
    except (ValueError, TypeError):
        return await call.answer("❌ Invalid order.", show_alert=True)
    await call.answer("⏳ Creating UPI payment...", show_alert=False)
    await create_product_upi_order(call.from_user.id, prod_id, quantity, call.message)


@dp.callback_query(F.data.startswith("binpay_"))
async def binance_product_payment(call: CallbackQuery, state: FSMContext):
    try:
        _, prod_id_s, qty_s = call.data.split("_", 2)
        prod_id, quantity = int(prod_id_s), int(qty_s)
    except (ValueError, TypeError):
        return await call.answer("❌ Invalid order.", show_alert=True)
    pricing = get_purchase_pricing(call.from_user.id, prod_id)
    if not pricing:
        return await call.answer("❌ Product not found.", show_alert=True)
    _, user, unit_price = pricing
    total = unit_price * quantity
    address = get_setting('binance_address', '')
    if not address:
        return await call.answer("⚠️ Binance payment is not configured.", show_alert=True)
    if not acquire_purchase_lock(call.from_user.id, prod_id, quantity):
        return await call.answer("⏳ This order is already processing.", show_alert=True)
    order_id = f"BPR{call.from_user.id}{int(time.time())}{random.randint(100,999)}"
    db_query("INSERT INTO transactions(order_id,user_id,amount_inr,status,timestamp,purpose,product_id,quantity,payment_method) VALUES(?,?,?,?,?,?,?,?,?)",
             (order_id,call.from_user.id,total,'pending',int(time.time()),'product',prod_id,quantity,'binance'))
    await state.set_state(UserStates.wait_for_product_binance_txid)
    await state.update_data(product_order_id=order_id)
    await call.message.edit_text(
        f"🪙 <b>DIRECT BINANCE PAYMENT</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"Amount: <b>₹{total:.2f}</b>\nUSDT: <b>{total / USDT_TO_INR:.4f}</b>\n\n"
        f"Send USDT to:\n<code>{html.escape(address)}</code>\n\n"
        "After payment, send the exact TxID here. The bot will verify it before generating your keys.\n\n"
        "🔒 Only one active order is allowed at a time to prevent duplicate keys.", parse_mode='HTML')


@dp.message(UserStates.wait_for_product_binance_txid)
async def process_product_binance_txid(m: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('product_order_id')
    txid = (m.text or '').strip()
    if not order_id or len(txid) < 10:
        return await m.answer("❌ Please send a valid Binance TxID.")
    txn = db_query("SELECT amount_inr,status,user_id FROM transactions WHERE order_id=? AND purpose='product'", (order_id,), fetchone=True)
    if not txn or txn[2] != m.from_user.id:
        return await m.answer("❌ Product payment order not found.")
    if txn[1] != 'pending':
        return await m.answer("⏳ This payment order is no longer pending.")
    if db_query("SELECT txid FROM crypto_txns WHERE txid=?", (txid,), fetchone=True):
        return await m.answer("⚠️ This TxID has already been used.")
    api_key_check = db_query("SELECT value FROM settings WHERE key='binance_api'", fetchone=True)
    secret_key_check = db_query("SELECT value FROM settings WHERE key='binance_secret'", fetchone=True)
    if not api_key_check or not secret_key_check:
        release_purchase_lock(m.from_user.id)
        return await m.answer("⚠️ Binance verification is not configured.")
    await m.answer("🔄 <b>Verifying payment...</b> Please wait.", parse_mode='HTML')
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(secret_key_check[0].encode(), query_string.encode(), hashlib.sha256).hexdigest()
    headers = {'X-MBX-APIKEY': api_key_check[0]}
    url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{query_string}&signature={signature}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                history = await resp.json(content_type=None) if resp.status == 200 else []
        expected_usdt = float(txn[0]) / USDT_TO_INR
        found = next((d for d in history if d.get('txId') == txid and d.get('status') == 1 and abs(float(d.get('amount',0)) - expected_usdt) < 0.0001), None)
        if not found:
            return await m.answer("❌ TxID not found, not confirmed, or amount does not match.")
        claimed = db_update_count("UPDATE transactions SET status='paid' WHERE order_id=? AND status='pending'", (order_id,))
        if not claimed:
            return await m.answer("⏳ This order is already being processed.")
        db_query("INSERT INTO crypto_txns(txid,user_id,amount_usdt,timestamp) VALUES(?,?,?,?)", (txid,m.from_user.id,float(found.get('amount')),int(time.time())))
        await state.clear()
        await fulfill_product_transaction(order_id, m.from_user.id, m)
    except Exception as exc:
        await m.answer(f"⚠️ Binance verification error: {html.escape(str(exc))}")


@dp.callback_query(F.data.startswith("cancel_product_"))
async def cancel_product_order(call: CallbackQuery, state: FSMContext):
    order_id = call.data.split("_", 2)[2]
    txn = db_query("SELECT status,user_id FROM transactions WHERE order_id=? AND purpose='product'", (order_id,), fetchone=True)
    if not txn or txn[1] != call.from_user.id:
        return await call.answer("❌ Order not found.", show_alert=True)
    if txn[0] == 'pending':
        if not db_update_count("UPDATE transactions SET status='cancelled' WHERE order_id=? AND status='pending'", (order_id,)):
            return await call.answer("⏳ This order can no longer be cancelled.", show_alert=True)
        refunded = refund_reserved_wallet(order_id)
        release_purchase_lock(call.from_user.id)
        note = f"\n\n💰 {fmt_curr(refunded)} wallet amount returned to your balance." if refunded > 0 else ""
        return await call.message.edit_text(f"❌ <b>Product order cancelled.</b>{note}", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    return await call.answer("⏳ This order can no longer be cancelled.", show_alert=True)

# ==============================================================================
# 14. USER DASHBOARD, FILES, VIP, RESELLER, ORDERS, PROFILE, REFERRAL
# ==============================================================================
@dp.callback_query(F.data == "menu_all_files")
async def all_files_handler(call: CallbackQuery):
    link_q = db_query("SELECT value FROM settings WHERE key='all_files_link'", fetchone=True)
    link = link_q[0] if link_q and link_q[0] != 'None' else None
    if link:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Access Download Channel ↗️", url=link, icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
            [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
        ])
        text = get_ui_text("download_files")
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    else:
        await call.answer("⚠️ Admin has not configured the private download channel link yet.", show_alert=True)

@dp.callback_query(F.data == "menu_vip_dash")
async def vip_dashboard(call: CallbackQuery):
    u = db_query("SELECT balance, is_vip, vip_since FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    is_vip = bool(u[1])
    status_str = "🟢 Active (Lifetime)" if is_vip else "🔴 Not Subscribed"
    text = get_ui_text("vip_menu", vip_status=status_str)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if is_vip:
        text += f"\n📅 <b>Member Since:</b> {u[2]}\n\nEnjoy your permanent 15% discount!"
    else:
        text += f"\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n"
        if u[0] >= VIP_PRICE_INR: 
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Purchase VIP for {fmt_curr(VIP_PRICE_INR)}", callback_data="execute_vip_upgrade", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Need {fmt_curr(VIP_PRICE_INR)} to Upgrade", callback_data="ignore_stock_click", style="danger")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="💳 Add Balance Now", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_vip_upgrade")
async def execute_vip_upgrade(call: CallbackQuery):
    u = db_query("SELECT balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a VIP Member!", show_alert=True)
    if u[0] < VIP_PRICE_INR: return await call.answer(f"❌ Your balance dropped below {VIP_PRICE_INR}.", show_alert=True)
    new_balance = u[0] - VIP_PRICE_INR
    now_date = datetime.now().strftime("%Y-%m-%d")
    db_query("UPDATE users SET balance=?, is_vip=1, vip_since=? WHERE user_id=?", (new_balance, now_date, call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_VIP")
    try: await bot.send_message(get_owner_id(), f"🌟 <b>NEW VIP UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>", parse_mode='HTML')
    except: pass
    await call.answer("🎉 Upgrade Successful! You are now a VIP Member.", show_alert=True)
    await vip_dashboard(call)

@dp.callback_query(F.data == "menu_reseller_dash")
async def reseller_dashboard(call: CallbackQuery):
    u = db_query("SELECT balance, is_reseller, reseller_since, total_saved FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    system_status = status_check[0] if status_check else "ON"
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    if u[1]: 
        text = (f"{get_emoji('shield_icon')} <b><u>— RESELLER DASHBOARD —</u></b> {get_emoji('shield_icon')}\n\n🟢 <b>Status:</b> Active\n📅 <b>Since:</b> {u[2]}\n{get_emoji('money_icon')} <b>Total Saved:</b> {fmt_curr(u[3])}\n\n🎉 You are enjoying exclusive wholesale prices on all products!")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
        return
    if system_status == "OFF": return await call.answer("⚠️ Wholesale / Reseller registrations are currently closed by Admin.", show_alert=True)
    text = (f"⚡ <b><u>— BECOME A RESELLER —</u></b> ⚡\n\nUpgrade your account to access wholesale <b>Reseller Prices</b>!\n\n📋 <b>Requirements to Upgrade:</b>\n1️⃣ Must have a minimum balance of <b>{fmt_curr(min_balance)}</b>.\n2️⃣ A one-time setup fee of <b>{fmt_curr(setup_fee)}</b> will be deducted.\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if u[0] >= min_balance: 
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Pay {fmt_curr(setup_fee)} & Become Reseller", callback_data="execute_reseller_upgrade", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success")])
    else:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Insufficient Balance (Need {fmt_curr(min_balance)})", callback_data="ignore_stock_click", style="danger")])
        kb.inline_keyboard.append([InlineKeyboardButton(text="💳 Add Balance", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_reseller_upgrade")
async def execute_reseller_upgrade(call: CallbackQuery):
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    u = db_query("SELECT balance, is_reseller FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a Reseller!", show_alert=True)
    if u[0] < min_balance: return await call.answer(f"❌ Your balance dropped below {fmt_curr(min_balance)}. Please top up.", show_alert=True)
    new_balance = u[0] - setup_fee
    db_query("UPDATE users SET balance=?, is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (new_balance, datetime.now().strftime("%Y-%m-%d"), call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_RESELLER")
    try: await bot.send_message(get_owner_id(), f"👑 <b>NEW RESELLER UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>", parse_mode='HTML')
    except: pass
    await call.answer("🎉 Upgrade Successful! Welcome to the Reseller tier.", show_alert=True)
    await reseller_dashboard(call)

@dp.callback_query(F.data == "menu_orders")
async def my_orders(call: CallbackQuery):
    orders = db_query("SELECT product_name, delivered_key, purchase_date, price_paid FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (call.from_user.id,), fetchall=True)
    if not orders: return await call.message.edit_text("🧾 You haven't made any purchases yet. Your vault is empty.", reply_markup=back_kb(), parse_mode='HTML')
    text = "🧾 <b><u>— YOUR RECENT ORDERS (LAST 10) —</u></b> 🧾\n\n"
    for o in orders: text += f"📦 <b>{o[0]}</b> ({fmt_curr(o[3])})\n🔑 <code>{o[1]}</code>\n📅 <i>{o[2]}</i>\n━━━━━━━━━━━━━━━━\n"
    await call.message.edit_text(text, reply_markup=back_kb(), parse_mode='HTML')

async def _get_profile_photo_id(user_id: int) -> Optional[str]:
    """Return the file_id of the user's current Telegram profile photo (or None)."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            return photos.photos[0][-1].file_id  # biggest size of the newest photo
    except Exception:
        logger.warning("Could not fetch profile photo for %s", user_id, exc_info=True)
    return None

@dp.callback_query(F.data == "menu_profile")
async def show_profile(call: CallbackQuery):
    uid = call.from_user.id
    tg_username = call.from_user.username or ""
    # Keep the stored username fresh so the profile always shows the current one.
    db_query("UPDATE users SET username=? WHERE user_id=?", (tg_username, uid))

    u = db_query(
        "SELECT user_id, first_name, account_type, balance, orders_count, spent, "
        "referrals_count, joined_date, is_reseller, reseller_since, total_saved, is_vip "
        "FROM users WHERE user_id=?", (uid,), fetchone=True)
    if not u:
        return await call.answer("❌ Profile not found. Send /start once.", show_alert=True)

    acc_type_display = []
    if u[8]: acc_type_display.append(f"{get_emoji('reseller')} Reseller")
    if u[11]: acc_type_display.append(f"{get_emoji('vip')} VIP")
    type_str = " | ".join(acc_type_display) if acc_type_display else "Regular User"

    name = html.escape(str(u[1] or call.from_user.full_name or "N/A"))
    username_str = f"@{html.escape(tg_username)}" if tg_username else "N/A"
    joined = html.escape(str(u[7])) if u[7] else "N/A"

    photo_id = await _get_profile_photo_id(uid)
    footer = "Profile photo fetched from Telegram." if photo_id else "No Telegram profile photo found."

    text = (
        f"<blockquote>{get_emoji('wallet_left')} <b>USER PROFILE</b> {get_emoji('wallet_right')}</blockquote>\n\n"
        f"{get_emoji('grid_id')} <b>ID:</b> <code>{u[0]}</code>\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Username:</b> {username_str}\n"
        f"{get_emoji('joined_grid')} <b>Joined:</b> {joined}\n"
        f"{get_emoji('account_level')} <b>Account Type:</b> {type_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{get_emoji('current_balance')} <b>Balance:</b> {fmt_curr(u[3])}\n"
        f"{get_emoji('total_spent')} <b>Spent:</b> {fmt_curr(u[5])}\n"
        f"{get_emoji('total_orders')} <b>Keys:</b> {u[4]}\n"
    )
    if u[8]:
        text += f"{get_emoji('money_icon')} <b>Saved via Reseller:</b> {fmt_curr(u[10])}\n"
    text += f"\n<i>{footer}</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="My Keys", callback_data="menu_orders",
                                 icon_custom_emoji_id=get_emoji_icon("history"), style="primary"),
            InlineKeyboardButton(text="Add Fund", callback_data="menu_add_balance",
                                 icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"),
        ],
        [InlineKeyboardButton(text="Redeem Promo Code", callback_data="redeem_coupon",
                              icon_custom_emoji_id=get_emoji_icon("redeem_icon"), style="success")],
        [InlineKeyboardButton(text="Back", callback_data="back_main",
                              icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
    ])

    await call.answer()

    if photo_id:
        msg = call.message
        # Already a photo screen (refresh): just swap caption/buttons.
        if getattr(msg, "photo", None):
            try:
                await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
                return
            except TelegramBadRequest as exc:
                if "not modified" in str(exc).lower():
                    return
        # A text/GIF screen can't turn into a photo, so send a fresh photo and remove the old one.
        try:
            await msg.answer_photo(photo=photo_id, caption=text, reply_markup=kb, parse_mode="HTML")
            try:
                await msg.delete()
            except Exception:
                pass
            return
        except Exception:
            logger.warning("Profile photo send failed, falling back to text.", exc_info=True)

    # No profile photo (or sending failed): same details as a normal text screen.
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "redeem_coupon")
async def redeem_coupon_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎟 <b>Please enter your VIP / Promo redeem code below:</b>", reply_markup=back_kb("menu_profile"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_redeem)

@dp.message(UserStates.wait_for_redeem)
async def process_redeem(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    user_id = m.from_user.id
    if db_query("SELECT * FROM redeemed WHERE user_id=? AND code=?", (user_id, code), fetchone=True):
        await m.answer("❌ Anti-Fraud Alert: You already redeemed this unique code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        await state.clear()
        return
    coupon = db_query("SELECT amount, uses_left FROM coupons WHERE code=?", (code,), fetchone=True)
    if not coupon: await m.answer("❌ Invalid or Expired Code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    elif coupon[1] <= 0: await m.answer("❌ This code's usage limit has been fully claimed by other users.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    else:
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (coupon[0], user_id))
        db_query("UPDATE coupons SET uses_left = uses_left - 1 WHERE code=?", (code,))
        db_query("INSERT INTO redeemed (user_id, code) VALUES (?, ?)", (user_id, code))
        log_activity(user_id, "PROMO_REDEEMED", f"Code: {code}, Amount: {coupon[0]}")
        await m.answer(f"🎉 <b>Success!</b>\nSafely added {fmt_curr(coupon[0])} to your balance!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        try:
            user_info = db_query("SELECT first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
            uname = user_info[0] if user_info else "Unknown User"
            await bot.send_message(get_owner_id(), f"🎟 <b>PROMO CODE REDEEMED!</b>\n👤 User: {uname} (<code>{user_id}</code>)\n🔖 Code: <b>{code}</b>\n💵 Amount: {fmt_curr(coupon[0])}", parse_mode='HTML')
        except Exception: pass
    await state.clear()

@dp.callback_query(F.data == "menu_referral")
async def show_referral(call: CallbackQuery):
    u = db_query("SELECT referrals_count, referral_earned FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{call.from_user.id}"
    text = (f"{get_emoji('referral')} <b><u>AFFILIATE PROGRAM</u></b> {get_emoji('referral')}\n\n✅ <b>Status:</b> ACTIVE\n💰 Earn <b>2% flat commission</b> on every successful purchase made by your referred friends!\n\n📊 <b>YOUR STATS:</b>\n👥 Total Invited: {u[0]}\n💵 Life-time Earned: {fmt_curr(u[1])}\n\n🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n<i>Simply copy and share this link to start earning!</i>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 🏆 LEADERBOARD
# ==============================================================================

def _build_leaderboard_text() -> str:
    """Build leaderboard combining real user data + admin-pinned overrides."""
    # Real top buyers from DB (top 15 to have room for merging)
    real_rows = db_query(
        "SELECT first_name, username, orders_count FROM users WHERE orders_count > 0 ORDER BY orders_count DESC LIMIT 15",
        fetchall=True
    ) or []

    # Admin pinned overrides
    pins = db_query(
        "SELECT rank_position, display_name, orders_count FROM leaderboard_pins WHERE is_pinned=1 ORDER BY rank_position",
        fetchall=True
    ) or []
    pinned = {int(r[0]): (r[1], int(r[2])) for r in pins}

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    real_idx = 0
    shown = 0
    rank = 1

    while shown < 10:
        if rank in pinned:
            name, count = pinned[rank]
            medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
            lines.append(f"{medal} <b>{html.escape(name)}</b>  —  <code>{count}</code> keys")
        else:
            # skip real entries whose slot was taken by a pin
            while real_idx < len(real_rows):
                rname = real_rows[real_idx][0] or real_rows[real_idx][1] or "Anonymous"
                rcount = int(real_rows[real_idx][2])
                real_idx += 1
                medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
                lines.append(f"{medal} <b>{html.escape(rname)}</b>  —  <code>{rcount}</code> keys")
                break
            else:
                break  # no more real users
        shown += 1
        rank += 1

    if not lines:
        return "🏆 <b>Top Buyers Leaderboard</b>\n\n<i>Abhi tak koi purchase nahi hua. Pehle buyer bano!</i>"

    text = (
        "<blockquote>🏆 <b>TOP BUYERS LEADERBOARD</b> 🏆\n"
        "✦━━━━━━━━━━━━━━━━━━━✦</blockquote>\n\n"
        + "\n".join(lines) +
        "\n\n<i>Har purchase ke saath rank upar jaata hai — shop karo aur leaderboard pe aao!</i>"
    )
    return text

@dp.callback_query(F.data == "menu_leaderboard")
async def show_leaderboard(call: CallbackQuery):
    await call.answer()
    text = _build_leaderboard_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Refresh", callback_data="menu_leaderboard", style="primary"),
        InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ]])
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await call.message.answer(text, reply_markup=kb, parse_mode="HTML")

# ── Admin: Leaderboard Override Panel ──
@dp.callback_query(F.data == "admin_leaderboard_override")
async def admin_leaderboard_override(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    await call.answer()
    pins = db_query(
        "SELECT rank_position, display_name, orders_count FROM leaderboard_pins WHERE is_pinned=1 ORDER BY rank_position",
        fetchall=True
    ) or []

    text = "<blockquote>🏆 <b>Leaderboard Override</b></blockquote>\n\n"
    if pins:
        text += "<b>Current Pinned Entries:</b>\n"
        for p in pins:
            text += f"  #{p[0]} — <b>{html.escape(p[1])}</b> ({p[2]} keys)\n"
        text += "\n"
    text += (
        "📌 <b>Kisi ko bhi kisi bhi rank par pin karo</b>\n"
        "Real purchases continue unaffected.\n\n"
        "Button chunein:"
    )

    kb_rows = []
    for pos in range(1, 6):
        label = f"📌 Set #{pos}"
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"admin_lb_set_{pos}", style="primary")])
    kb_rows.append([InlineKeyboardButton(text="🗑 Clear All Pins", callback_data="admin_lb_clear_all", style="danger")])
    kb_rows.append([InlineKeyboardButton(text="👁 Preview Leaderboard", callback_data="admin_lb_preview", style="success")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Back to Admin", callback_data="admin_panel_back", style="danger")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin_lb_set_"))
async def admin_lb_set_rank(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    await call.answer()
    rank_pos = int(call.data.replace("admin_lb_set_", ""))
    await state.set_state("lb_set_name")
    await state.update_data(lb_rank=rank_pos, lb_msg_id=call.message.message_id)
    await call.message.edit_text(
        f"<blockquote>📌 <b>Set Rank #{rank_pos}</b></blockquote>\n\n"
        f"Yeh entry #{rank_pos} position par dikhegi.\n\n"
        f"<b>Display Name bhejein</b> (jaise: <code>Ravi ✨</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Cancel", callback_data="admin_leaderboard_override", style="danger")
        ]]),
        parse_mode="HTML"
    )

@dp.message(StateFilter("lb_set_name"))
async def admin_lb_got_name(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    await state.set_state("lb_set_count")
    await state.update_data(lb_name=message.text.strip())
    await message.answer(
        f"✅ Name: <b>{html.escape(message.text.strip())}</b>\n\n"
        f"Ab <b>Keys count</b> bhejein (jaise: <code>142</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Cancel", callback_data="admin_leaderboard_override", style="danger")
        ]])
    )

@dp.message(StateFilter("lb_set_count"))
async def admin_lb_got_count(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    txt = message.text.strip()
    if not txt.isdigit():
        return await message.answer("⚠️ Sirf number bhejein (jaise: <code>142</code>)", parse_mode="HTML")

    rank_pos = data.get("lb_rank", 1)
    lb_name = data.get("lb_name", "User")
    lb_count = int(txt)
    await state.clear()

    db_query(
        "INSERT OR REPLACE INTO leaderboard_pins (rank_position, display_name, orders_count, is_pinned) VALUES (?,?,?,1)",
        (rank_pos, lb_name, lb_count)
    )

    await message.answer(
        f"✅ <b>Rank #{rank_pos} Pin Set!</b>\n\n"
        f"📌 Name: <b>{html.escape(lb_name)}</b>\n"
        f"🔑 Keys: <code>{lb_count}</code>\n\n"
        f"<i>Yeh ab leaderboard par #{rank_pos} position par dikhega.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏆 Preview Leaderboard", callback_data="admin_lb_preview", style="success")],
            [InlineKeyboardButton(text="⬅️ Back to Override Panel", callback_data="admin_leaderboard_override", style="danger")]
        ])
    )

@dp.callback_query(F.data == "admin_lb_clear_all")
async def admin_lb_clear_all(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    db_query("DELETE FROM leaderboard_pins")
    await call.answer("✅ Sab pins clear ho gaye!", show_alert=True)
    await admin_leaderboard_override(call)

@dp.callback_query(F.data == "admin_lb_preview")
async def admin_lb_preview(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    await call.answer()
    text = _build_leaderboard_text()
    await call.message.edit_text(
        "<blockquote>👁 <b>Leaderboard Preview (Admin View)</b></blockquote>\n\n" + text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Back", callback_data="admin_leaderboard_override", style="danger")
        ]]),
        parse_mode="HTML"
    )

# ==============================================================================
# 🛠 PRODUCT MAINTENANCE — quick per-product ON/OFF toggle so a product can be
# instantly hidden from purchase (maintenance) and re-enabled later, without
# digging through the full "Manage Products" edit screen. Reuses the same
# products.is_active column that the buy-flow already filters on.
# ==============================================================================

@dp.callback_query(F.data == "admin_maint_categories")
async def admin_maint_categories(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    await call.answer()
    cats = db_query(
        "SELECT category, COUNT(*), SUM(CASE WHEN COALESCE(is_active,1)=0 THEN 1 ELSE 0 END) FROM products GROUP BY category ORDER BY category",
        fetchall=True
    ) or []
    if not cats:
        return await call.message.edit_text(
            "❌ Koi product nahi mila.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb_rows = []
    for cat, total, maint_count in cats:
        label = f"🗂 {cat} ({total} products" + (f", {maint_count} 🔴)" if maint_count else ")")
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"admin_maint_cat_{cat}", style="primary")])
    kb_rows.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        "🛠 <b>Product Maintenance</b>\n\n"
        "Ek category chuno, phir jis product ko chaho ek tap me 🟢 Live ↔ 🔴 Maintenance kar sakte ho.\n\n"
        "🔴 Maintenance wala product turant khareedne ke liye disappear ho jaata hai — koi khareed nahi paayega, jab tak wapas 🟢 na karo.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode='HTML')

@dp.callback_query(F.data.startswith("admin_maint_cat_"))
async def admin_maint_products_list(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    await call.answer()
    category = call.data.split("admin_maint_cat_", 1)[1]
    rows = db_query(
        "SELECT id, name, panel_name, COALESCE(is_active,1) FROM products WHERE category=? ORDER BY panel_name, name",
        (category,), fetchall=True
    ) or []
    if not rows:
        return await call.message.edit_text("❌ Is category me koi product nahi mila.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb_rows = []
    for pid, name, panel, active in rows:
        icon = "🟢" if int(active) == 1 else "🔴"
        label = f"{icon} {name}" + (f" ({panel})" if panel else "")
        kb_rows.append([InlineKeyboardButton(text=label[:64], callback_data=f"admin_maint_toggle_{pid}", style="success" if active else "danger")])
    kb_rows.append([InlineKeyboardButton(text="Back to Categories", callback_data="admin_maint_categories", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        f"🛠 <b>Product Maintenance — {html.escape(category)}</b>\n\n"
        "🟢 = Live (customers khareed sakte hain)\n🔴 = Maintenance (khareed nahi kar sakte)\n\nTap karke turant switch karo:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode='HTML')

@dp.callback_query(F.data.startswith("admin_maint_toggle_"))
async def admin_maint_toggle(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("❌ Admin only.", show_alert=True)
    try:
        p_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("❌ Invalid product.", show_alert=True)
    row = db_query("SELECT COALESCE(is_active,1), category, name FROM products WHERE id=?", (p_id,), fetchone=True)
    if not row:
        return await call.answer("❌ Product not found.", show_alert=True)
    new_val = 0 if int(row[0]) == 1 else 1
    db_query("UPDATE products SET is_active=? WHERE id=?", (new_val, p_id))
    await call.answer(f"🔴 '{row[2]}' ab Maintenance me hai." if new_val == 0 else f"🟢 '{row[2]}' wapas Live ho gaya.", show_alert=True)
    call.data = f"admin_maint_cat_{row[1]}"
    await admin_maint_products_list(call)


@dp.callback_query(F.data == "menu_spin_landing")
async def lucky_spin_landing(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    spin_status = status_check[0] if status_check else "ON"
    if spin_status == "OFF": return await call.answer("⚠️ Lucky Ludo Spin is currently disabled by Admin.", show_alert=True)
    await call.message.edit_text(f"{get_emoji('ludo_spin')} <b><u>— LUDO SPIN —</u></b> {get_emoji('ludo_spin')}\n\nTest your luck! You can spin once every 24 hours.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Spin Dice Now!", callback_data="execute_spin", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="success")], 
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ]), parse_mode='HTML')

@dp.callback_query(F.data == "execute_spin")
async def execute_spin(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    if status_check and status_check[0] == "OFF": return await call.answer("⚠️ Lucky Spin is disabled.", show_alert=True)
    u = db_query("SELECT last_spin, balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    now = datetime.now()
    if u[0] and now < datetime.strptime(u[0], "%Y-%m-%d %H:%M:%S") + timedelta(hours=24):
        return await call.message.edit_text("❌ <b>Cooldown Active!</b>\nYou already played today. Come back tomorrow.", reply_markup=back_kb(), parse_mode='HTML')
    await call.message.delete()
    dice_msg = await bot.send_dice(chat_id=call.message.chat.id, emoji="🎲")
    await asyncio.sleep(SPIN_DELAY_SECONDS) 
    dice_val = dice_msg.dice.value
    limit_check = db_query("SELECT value FROM settings WHERE key='daily_spin_limit'", fetchone=True)
    limit = float(limit_check[0]) if limit_check else 50.0
    rewards_db = db_query("SELECT amount FROM spin_rewards WHERE amount <= ?", (limit,), fetchall=True)
    rewards_list = [r[0] for r in rewards_db] if rewards_db else [0.0]
    reward = random.choice(rewards_list)
    if bool(u[2]) and reward > 0: reward = reward * 2.0
    new_bal = u[1] + reward
    db_query("UPDATE users SET balance=?, last_spin=? WHERE user_id=?", (new_bal, now.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id))
    log_activity(call.from_user.id, "PLAYED_SPIN", f"Reward: {reward}, Dice: {dice_val}")
    msg = get_ui_text("lucky_dice_result", dice_value=dice_val, won_amount=fmt_curr(reward), new_balance=fmt_curr(new_bal))
    if bool(u[2]) and reward > 0: msg += "\n\n<i>🌟 VIP Bonus: 2x Multiplier Applied!</i>"
    await dice_msg.reply(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📚 BACK TO MENU", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="success")]]), parse_mode='HTML')

# ==============================================================================
# 16. TUTORIALS & SUPPORT
# ==============================================================================
@dp.callback_query(F.data == "menu_how_to")
async def tutorial_system(call: CallbackQuery):
    video_link_query = db_query("SELECT value FROM settings WHERE key='how_to_video'", fetchone=True)
    video_link = video_link_query[0] if video_link_query and video_link_query[0] != 'None' else None
    text = (f"{get_emoji('tutorial')} <b><u>— TUTORIALS & GUIDE —</u></b> {get_emoji('tutorial')}\n\n1️⃣ Add funds via <b>Add Balance</b>\n2️⃣ Navigate to <b>Product Store</b>\n3️⃣ Choose your desired Panel and Package validity.\n4️⃣ The Key and Installation APK link will be instantly provided.")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if video_link: kb.inline_keyboard.append([InlineKeyboardButton(text="Watch Full Video Tutorial", url=video_link, icon_custom_emoji_id=get_emoji_icon("tutorial"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "menu_support")
async def support_center(call: CallbackQuery):
    telegram_link = get_setting("support_telegram", "https://t.me/YOUR_SUPPORT")
    whatsapp_link = get_setting("support_whatsapp", "https://wa.me/YOUR_NUMBER")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Contact on Telegram", url=telegram_link, icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="Contact on WhatsApp", url=whatsapp_link, icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")],
        [InlineKeyboardButton(text="🎫 Open New Ticket", callback_data="open_ticket", icon_custom_emoji_id=get_emoji_icon("support"), style="success"), 
         InlineKeyboardButton(text="📋 My Open Tickets", callback_data="my_tickets", icon_custom_emoji_id=get_emoji_icon("history"), style="success")], 
    ])
    if get_setting("ai_support_status", "OFF") == "ON":
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="🤖 AI Support", callback_data="ai_support_start", icon_custom_emoji_id=get_emoji_icon("support"), style="primary")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(f"{get_emoji('telegram')}{get_emoji('whatsapp')} <b><u>— PREMIUM SUPPORT CENTER —</u></b>\n\nContact us via Telegram or WhatsApp for instant help, or open a support ticket for admin assistance.", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "my_tickets")
async def view_my_tickets(call: CallbackQuery):
    tickets = db_query("SELECT id, message, status, created_at FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 5", (call.from_user.id,), fetchall=True)
    if not tickets: return await call.message.edit_text("📋 You do not have any active or previous support tickets.", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    text = "📋 <b><u>— Your Recent Tickets —</u></b> 📋\n\n"
    for t in tickets:
        status_icon = "🟢" if t[2] == 'Open' else "🔴"
        text += f"🎫 <b>Ticket #{t[0]}</b> | Status: {status_icon} <b>{t[2]}</b>\n📅 <i>{t[3]}</i>\n📝 <i>{t[1][:80]}...</i>\n\n"
    await call.message.edit_text(text, reply_markup=back_kb("menu_support"), parse_mode='HTML')

@dp.callback_query(F.data == "open_ticket")
async def open_ticket_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("📝 <b>Please type your issue/message below in detail:</b>", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_ticket)

@dp.message(UserStates.wait_for_ticket)
async def process_ticket(m: Message, state: FSMContext):
    db_query("INSERT INTO tickets (user_id, message, created_at) VALUES (?, ?, ?)", (m.from_user.id, m.text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await m.answer("✅ <b>Ticket Submitted Successfully!</b> Admins will reply soon.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    try: await bot.send_message(get_owner_id(), f"🚨 <b>NEW SUPPORT TICKET</b>\nFrom: <code>{m.from_user.id}</code>\nMsg: {m.text}", parse_mode='HTML')
    except: pass
    log_activity(m.from_user.id, "OPENED_TICKET")
    await state.clear()

# ==============================================================================
# 17. ADMIN PANEL
# ==============================================================================
@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id): return
    await state.clear()
    await _show_admin_panel(message)

@dp.message(Command("version"))
async def version_check(message: Message):
    if not is_admin_user(message.from_user.id): return
    await message.answer(f"🏷 <b>Running build:</b>\n<code>{BOT_CODE_VERSION}</code>", parse_mode='HTML')

@dp.callback_query(F.data == "admin_panel_back")
async def back_to_admin(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_admin_panel(call)

# ==============================================================================
# CO-ADMINS & OWNERSHIP TRANSFER (Owner-only)
# Co-admins get full /admin panel access everywhere else in this file (see
# is_admin_user), but managing co-admins and transferring ownership stay
# restricted to whoever currently holds the "owner_id" setting.
# ==============================================================================

def _owner_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Co-Admin", callback_data="admin_add_coadmin", style="success")],
        [InlineKeyboardButton(text="➖ Remove Co-Admin", callback_data="admin_remove_coadmin_menu", style="danger")],
        [InlineKeyboardButton(text="🔁 Transfer Ownership", callback_data="admin_transfer_ownership", style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
    ])
    return apply_button_theme(kb)

def _owner_menu_text() -> str:
    co_admins = get_co_admin_ids()
    listing = "\n".join(f"• <code>{uid}</code>" for uid in co_admins) if co_admins else "<i>Koi co-admin nahi hai.</i>"
    return (
        "🛡️ <b>Co-Admins & Ownership</b>\n\n"
        f"👑 <b>Owner:</b> <code>{get_owner_id()}</code>\n\n"
        f"<b>Co-Admins</b> (full /admin access, but can't manage co-admins or transfer ownership):\n{listing}"
    )

@dp.callback_query(F.data == "admin_owner_menu")
async def admin_owner_menu(call: CallbackQuery):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf Owner hi ye section use kar sakta hai!", show_alert=True)
    await call.message.edit_text(_owner_menu_text(), reply_markup=_owner_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_add_coadmin")
async def admin_add_coadmin_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf Owner hi co-admins add kar sakta hai!", show_alert=True)
    await call.message.edit_text(
        "➕ <b>Add Co-Admin</b>\n\nUnka numeric Telegram User ID bhejein.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_coadmin_add)

@dp.message(AdminStates.wait_for_coadmin_add)
async def admin_add_coadmin_save(m: Message, state: FSMContext):
    if not is_owner_user(m.from_user.id):
        await state.clear()
        return
    raw = (m.text or "").strip()
    if not raw.lstrip("-").isdigit():
        return await m.answer("❌ Valid numeric User ID bhejein.")
    new_id = int(raw)
    if new_id == get_owner_id():
        return await m.answer("❌ Owner khud ko co-admin nahi bana sakta.")
    co_admins = get_co_admin_ids()
    if new_id in co_admins:
        await m.answer("⚠️ Ye user pehle se hi co-admin hai.", reply_markup=admin_kb())
    else:
        co_admins.append(new_id)
        set_co_admin_ids(co_admins)
        await m.answer(f"✅ <code>{new_id}</code> ab co-admin hai — full /admin panel access mil gaya.", reply_markup=admin_kb(), parse_mode="HTML")
        try:
            await bot.send_message(new_id, "👑 <b>Aapko is store ka Co-Admin bana diya gaya hai!</b>\n\nFull /admin panel access ke liye /admin command use karein.", parse_mode="HTML")
        except Exception:
            pass
    await state.clear()

@dp.callback_query(F.data == "admin_remove_coadmin_menu")
async def admin_remove_coadmin_menu(call: CallbackQuery):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf Owner hi co-admins remove kar sakta hai!", show_alert=True)
    co_admins = get_co_admin_ids()
    if not co_admins:
        return await call.answer("Koi co-admin nahi hai.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ Remove {uid}", callback_data=f"admin_remove_coadmin_{uid}", style="danger")]
        for uid in co_admins
    ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back", callback_data="admin_owner_menu", style="danger")])
    await call.message.edit_text("➖ <b>Remove Co-Admin</b>\n\nRemove karne ke liye tap karein:", reply_markup=apply_button_theme(kb), parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin_remove_coadmin_"))
async def admin_remove_coadmin_do(call: CallbackQuery):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf Owner hi co-admins remove kar sakta hai!", show_alert=True)
    target_id = int(call.data.rsplit("_", 1)[1])
    co_admins = [uid for uid in get_co_admin_ids() if uid != target_id]
    set_co_admin_ids(co_admins)
    await call.answer(f"✅ {target_id} ka co-admin role remove kar diya gaya.", show_alert=True)
    try:
        await bot.send_message(target_id, "⚠️ Aapka Co-Admin role remove kar diya gaya hai.")
    except Exception:
        pass
    await call.message.edit_text(_owner_menu_text(), reply_markup=_owner_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_transfer_ownership")
async def admin_transfer_ownership_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf current Owner hi ownership transfer kar sakta hai!", show_alert=True)
    await call.message.edit_text(
        "🔁 <b>Transfer Ownership</b>\n\n"
        "⚠️ Ye action aapko store ka Owner nahi rehne dega — naye Owner ko full /admin access mil jaayega, "
        "aur sirf woh hi future me co-admins manage ya ownership transfer kar payega.\n\n"
        "Naye Owner ka numeric Telegram User ID bhejein.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_new_owner_id)

@dp.message(AdminStates.wait_for_new_owner_id)
async def admin_transfer_ownership_confirm_prompt(m: Message, state: FSMContext):
    if not is_owner_user(m.from_user.id):
        await state.clear()
        return
    raw = (m.text or "").strip()
    if not raw.lstrip("-").isdigit():
        return await m.answer("❌ Valid numeric User ID bhejein.")
    new_owner_id = int(raw)
    if new_owner_id == get_owner_id():
        await state.clear()
        return await m.answer("❌ Ye user pehle se hi Owner hai.", reply_markup=admin_kb())
    set_setting("pending_ownership_transfer_to", str(new_owner_id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Transfer", callback_data="admin_confirm_ownership_transfer", style="danger")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_cancel_ownership_transfer", style="success")],
    ])
    await m.answer(
        f"⚠️ <b>Confirm Ownership Transfer</b>\n\n"
        f"Naya Owner: <code>{new_owner_id}</code>\n\n"
        f"<b>Ye undo nahi ho sakta jab tak naya owner khud wapas transfer na kare.</b> Confirm karein?",
        reply_markup=apply_button_theme(kb), parse_mode="HTML",
    )
    await state.clear()

@dp.callback_query(F.data == "admin_confirm_ownership_transfer")
async def admin_confirm_ownership_transfer(call: CallbackQuery):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Restricted!", show_alert=True)
    new_owner_raw = get_setting("pending_ownership_transfer_to", "")
    if not new_owner_raw:
        return await call.answer("❌ Koi pending transfer nahi mila. Dubara /admin se try karein.", show_alert=True)
    new_owner_id = int(new_owner_raw)
    old_owner_id = get_owner_id()
    set_setting("owner_id", str(new_owner_id))
    set_setting("pending_ownership_transfer_to", "")
    await call.message.edit_text(
        f"✅ <b>OWNERSHIP TRANSFERRED!</b>\n\nNaya Owner: <code>{new_owner_id}</code>",
        parse_mode="HTML",
    )
    try:
        await bot.send_message(new_owner_id, "👑 <b>YOU ARE NOW THE OWNER OF THIS STORE!</b>\n\nFull admin panel access ke liye /admin command use karein.", parse_mode="HTML")
    except Exception:
        pass
    try:
        await bot.send_message(old_owner_id, f"ℹ️ Ownership <code>{new_owner_id}</code> ko transfer ho chuki hai.", parse_mode="HTML")
    except Exception:
        pass

@dp.callback_query(F.data == "admin_cancel_ownership_transfer")
async def admin_cancel_ownership_transfer(call: CallbackQuery):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Restricted!", show_alert=True)
    set_setting("pending_ownership_transfer_to", "")
    await call.message.edit_text(
        "❌ <b>OWNERSHIP TRANSFER CANCELLED</b>\n\nKuch bhi change nahi hua, aap abhi bhi Owner hain.",
        reply_markup=_owner_menu_kb(), parse_mode="HTML",
    )

# ==============================================================================
# 🔑 CHANGE BOT TOKEN (Owner-only)
# Lets the Owner swap which Telegram bot account this codebase runs as —
# useful when reselling this bot: buyer's own token takes over and this
# token stops responding, while all data (users/products/keys/balance/
# owner/co-admins) stays exactly the same, since it lives in the same DB.
# ==============================================================================

@dp.callback_query(F.data == "admin_change_bot_token")
async def admin_change_bot_token_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Sirf Owner hi bot token change kar sakta hai!", show_alert=True)
    await call.message.edit_text(
        "🔑 <b>Change Bot Token</b>\n\n"
        "⚠️ Naya token bhejte hi <b>ye bot (current token) turant band ho jaayega</b> aur naye token "
        "wala Telegram bot turant chalu ho jaayega. Sab data — users, products, keys, balance, "
        "owner/co-admins — bilkul same rahega, sirf Telegram bot account badlega.\n\n"
        "BotFather se mila naya <b>Bot Token</b> bhejein:",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_new_bot_token)

@dp.message(AdminStates.wait_for_new_bot_token)
async def admin_change_bot_token_save(m: Message, state: FSMContext):
    if not is_owner_user(m.from_user.id):
        await state.clear()
        return
    new_token = (m.text or "").strip()
    if ":" not in new_token or len(new_token) < 20:
        return await m.answer("❌ Ye valid Telegram bot token nahi lagta. BotFather se mila poora token bhejein.")

    checking = await m.answer("⏳ Token verify kar raha hoon...")
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"https://api.telegram.org/bot{new_token}/getMe") as resp:
                body = await resp.json(content_type=None)
        if not body.get("ok"):
            await state.clear()
            return await checking.edit_text("❌ Telegram ne ye token reject kiya. Sahi token bhejein.", reply_markup=admin_back_kb())
        new_username = body.get("result", {}).get("username", "unknown")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await state.clear()
        return await checking.edit_text("❌ Token verify nahi ho paya (network error). Dobara try karein.", reply_markup=admin_back_kb())
    except Exception:
        logger.exception("Bot token verification failed")
        await state.clear()
        return await checking.edit_text("❌ Unexpected error. Dobara try karein.", reply_markup=admin_back_kb())

    set_setting("override_bot_token", new_token)
    await state.update_data(pending_token_switch_username=new_username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆔 Naya Owner ID Set Karo", callback_data="tokenchg_set_owner", style="primary")],
        [InlineKeyboardButton(text="▶️ Same Owner Rakho & Restart", callback_data="tokenchg_restart", style="success")],
    ])
    await checking.edit_text(
        f"✅ <b>Token verified!</b> Naya bot: @{new_username}\n\n"
        f"Agar ye ek <b>alag/naya bot</b> hai (jaise kisi ko bech rahe ho ya khud dusra store chala rahe ho), "
        f"to niche button se uska Owner (numeric Telegram ID) set kar sakte ho.\n\n"
        f"Warna current Owner (<code>{get_owner_id()}</code>) hi is naye bot ka bhi Owner rahega.",
        reply_markup=kb, parse_mode="HTML",
    )


async def _finish_bot_token_switch(chat_target: Message, state: FSMContext, triggered_by: int):
    data = await state.get_data()
    new_username = data.get("pending_token_switch_username", "the new bot")
    await state.clear()
    logger.info(f"Owner {triggered_by} triggered a bot-token switch to @{new_username}. Restarting process...")
    await chat_target.answer(
        f"🔄 <b>Restart ho raha hai...</b> Kuch second me ye bot band hoga aur @{new_username} chalu ho jaayega.",
        parse_mode="HTML",
    )
    await asyncio.sleep(2)
    os.execv(sys.executable, [sys.executable] + sys.argv)


@dp.callback_query(F.data == "tokenchg_restart")
async def admin_token_change_restart(call: CallbackQuery, state: FSMContext):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Restricted!", show_alert=True)
    await call.answer()
    await _finish_bot_token_switch(call.message, state, call.from_user.id)


@dp.callback_query(F.data == "tokenchg_set_owner")
async def admin_token_change_set_owner_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_user(call.from_user.id):
        return await call.answer("❌ Restricted!", show_alert=True)
    await call.answer()
    await call.message.edit_text(
        "🆔 <b>Naye Bot Ka Owner</b>\n\nJis numeric Telegram User ID ko is naye bot ka Owner banana hai, "
        "wo ID bhejein.\n\n<i>Pata nahi? @userinfobot se apni ID le sakte ho.</i>",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_token_change_owner_id)


@dp.message(AdminStates.wait_for_token_change_owner_id)
async def admin_token_change_set_owner_save(m: Message, state: FSMContext):
    if not is_owner_user(m.from_user.id):
        await state.clear()
        return
    raw = (m.text or "").strip()
    if not raw.lstrip("-").isdigit():
        return await m.answer("❌ Valid numeric User ID bhejein.")
    new_owner_id = int(raw)
    set_setting("owner_id", str(new_owner_id))
    set_co_admin_ids([])
    await m.answer(f"✅ Naya Owner set ho gaya: <code>{new_owner_id}</code>", parse_mode="HTML")
    await _finish_bot_token_switch(m, state, m.from_user.id)

@dp.callback_query(F.data == "admin_toggle_vip_sys")
async def toggle_vip_sys(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    current = res[0] if res else 'OFF'
    new_status = 'ON' if current == 'OFF' else 'OFF'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('vip_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_advanced_management")
async def admin_advanced_management(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗂 Manage Categories", callback_data="admin_manage_categories", style="primary"),
            InlineKeyboardButton(text="🔗 External Key API", callback_data="admin_setup_external_api", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🔗 Multi API Manager", callback_data="admin_multi_api_manager", style="primary"),
            InlineKeyboardButton(text="🤖 AI API Paste", callback_data="admin_ai_api_paste", style="primary"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit UI Texts", callback_data="admin_edit_ui_menu", style="primary"),
            InlineKeyboardButton(text="🎨 Edit All Emojis", callback_data="admin_edit_emojis", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📞 Support Links", callback_data="admin_set_support_links", style="primary"),
            InlineKeyboardButton(text="🎨 Category Emojis", callback_data="admin_set_category_emojis", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🖼 Panel Emojis", callback_data="admin_set_panel_emojis", style="primary"),
            InlineKeyboardButton(text="💬 AI Support Setup", callback_data="admin_ai_support_setup", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🎬 Panel Gameplay Videos", callback_data="admin_set_panel_videos", style="primary"),
            InlineKeyboardButton(text="🔊 Welcome Voice", callback_data="admin_set_welcome_voice", style="primary"),
        ],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await call.message.edit_text(
        "🛡️ <b>Advanced Management</b>\n\n"
        "Choose an advanced configuration section:",
        reply_markup=kb,
        parse_mode="HTML",
    )

@dp.callback_query(F.data == "admin_bot_system_tools")
async def admin_bot_system_tools(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add Product", callback_data="admin_add_prod", style="success"),
            InlineKeyboardButton(text="🗑 Remove Product", callback_data="admin_manage_prods", style="danger"),
        ],
        [
            InlineKeyboardButton(text="🔑 Add / Remove Keys", callback_data="admin_manage_prods", style="primary"),
            InlineKeyboardButton(text="📦 Stock Overview", callback_data="admin_manage_prods", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🗂 Add / Remove Category", callback_data="admin_manage_categories", style="primary"),
            InlineKeyboardButton(text="💰 User Wallet Controls", callback_data="admin_user_control_start", style="success"),
        ],
        [
            InlineKeyboardButton(text="👥 Total Users / Stats", callback_data="admin_view_stats", style="primary"),
            InlineKeyboardButton(text="🎟 Coupons", callback_data="admin_create_coupon", style="success"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast_btn", style="primary"),
            InlineKeyboardButton(text="🎰 Daily Gift / Spin", callback_data="admin_spin_menu", style="success"),
        ],
        [
            InlineKeyboardButton(text="👑 Reseller System", callback_data="admin_reseller_menu", style="primary"),
            InlineKeyboardButton(text="🔗 Download Link", callback_data="admin_set_all_files", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🛠 Maintenance Mode", callback_data="admin_toggle_bot", style="danger"),
            InlineKeyboardButton(text="✏️ Start/Menu Text", callback_data="admin_edit_ui_menu", style="primary"),
        ],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await call.message.edit_text(
        "🧰 <b>BOT SYSTEM Tools</b>\n\n"
        "These controls match the useful admin actions from the reference bot "
        "and open the DOC bot's working handlers.",
        reply_markup=kb,
        parse_mode="HTML",
    )

@dp.callback_query(F.data == "admin_user_control_start")
async def admin_user_control_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Download Full User List", callback_data="admin_download_userlist", icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("💻 <b>User Control Terminal</b>\n\n✏️ Enter the <b>User ID</b> or <b>@Username</b> you want to investigate or manage:\n\n👇 <b>OR</b> download the full user CSV format list:", reply_markup=kb, parse_mode='HTML')
    await state.set_state(AdminStates.manage_target_user)

# ==============================================================================
# ADMIN: DEDUCT BALANCE FROM ALL USERS AT ONCE
# ==============================================================================
def _bulk_balance_stats(amount: Optional[float]) -> Tuple[int, float, float]:
    """(users with balance, total balance now, total that would be deducted).

    amount=None means "reset everyone to 0". A user can never go below Rs 0,
    so a user holding less than ``amount`` simply loses what they have.
    """
    row = db_query("SELECT COUNT(*), COALESCE(SUM(balance),0) FROM users WHERE balance > 0", fetchone=True) or (0, 0)
    users_n, total_now = int(row[0] or 0), float(row[1] or 0)
    if amount is None:
        return users_n, total_now, total_now
    cut = db_query("SELECT COALESCE(SUM(MIN(balance, ?)),0) FROM users WHERE balance > 0", (amount,), fetchone=True)
    return users_n, total_now, float(cut[0] or 0) if cut else 0.0

def _bulk_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ YES, DEDUCT NOW", callback_data="admin_bulk_deduct_confirm", style="danger")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="success")],
    ])

def _bulk_confirm_text(amount: Optional[float]) -> str:
    users_n, total_now, cut = _bulk_balance_stats(amount)
    what = "reset to <b>₹0.00</b>" if amount is None else f"reduced by <b>{fmt_curr(amount)}</b> each (never below ₹0)"
    return (
        "⚠️ <b>CONFIRM BULK DEDUCTION</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users with balance: <b>{users_n}</b>\n"
        f"💰 Total balance now: <b>{fmt_curr(total_now)}</b>\n"
        f"➖ Total that will be deducted: <b>{fmt_curr(cut)}</b>\n\n"
        f"Every user's wallet will be {what}.\n\n"
        "🚨 <b>This cannot be undone.</b> Press YES only if you are sure."
    )

@dp.callback_query(F.data == "admin_bulk_deduct_start")
async def admin_bulk_deduct_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await state.clear()
    users_n, total_now, _ = _bulk_balance_stats(None)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Reset ALL balances to ₹0", callback_data="admin_bulk_deduct_all", style="danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")],
    ])
    await call.message.edit_text(
        "💸 <b>DEDUCT ALL USERS BALANCE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users with balance: <b>{users_n}</b>\n"
        f"💰 Total balance: <b>{fmt_curr(total_now)}</b>\n\n"
        "✏️ Send the <b>amount</b> to deduct from <b>every</b> user (example: <code>10</code>).\n"
        "Users with less than that lose only what they have.\n\n"
        "👇 Or use the button below to set every balance to ₹0.",
        reply_markup=kb, parse_mode="HTML")
    await state.set_state(AdminStates.wait_bulk_deduct_amount)

@dp.message(AdminStates.wait_bulk_deduct_amount)
async def admin_bulk_deduct_amount(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    raw = (m.text or "").strip().replace("₹", "").replace(",", "")
    try:
        amount = round(float(raw), 2)
    except (ValueError, TypeError):
        return await m.answer("❌ Send a number like <code>10</code> or <code>25.5</code>.", parse_mode="HTML")
    if amount <= 0 or amount > 10_000_000:
        return await m.answer("❌ Amount must be greater than 0.", parse_mode="HTML")
    await state.update_data(bulk_amount=amount)
    await m.answer(_bulk_confirm_text(amount), reply_markup=_bulk_confirm_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_bulk_deduct_all")
async def admin_bulk_deduct_all(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await state.update_data(bulk_amount="ALL")
    await call.message.edit_text(_bulk_confirm_text(None), reply_markup=_bulk_confirm_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_bulk_deduct_confirm")
async def admin_bulk_deduct_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    data = await state.get_data()
    chosen = data.get("bulk_amount")
    # Clear first: a second tap on YES (or a stale button) can never run the deduction twice.
    await state.clear()
    if chosen is None:
        return await call.answer("⚠️ This request expired. Start again from the admin panel.", show_alert=True)
    amount = None if chosen == "ALL" else float(chosen)

    users_n, total_now, cut = _bulk_balance_stats(amount)
    if amount is None:
        changed = db_update_count("UPDATE users SET balance=0 WHERE balance > 0")
    else:
        changed = db_update_count("UPDATE users SET balance = MAX(balance - ?, 0) WHERE balance > 0", (amount,))
    after = db_query("SELECT COALESCE(SUM(balance),0) FROM users", fetchone=True)
    log_activity(call.from_user.id, "ADMIN_BULK_DEDUCT",
                 f"Mode: {'RESET_ALL' if amount is None else amount}, Users: {changed}, Deducted: {cut:.2f}")
    await call.message.edit_text(
        "✅ <b>BULK DEDUCTION DONE</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Users changed: <b>{changed}</b>\n"
        f"➖ Total deducted: <b>{fmt_curr(cut)}</b>\n"
        f"💰 Total balance before: {fmt_curr(total_now)}\n"
        f"💰 Total balance now: <b>{fmt_curr(float(after[0] or 0) if after else 0)}</b>",
        reply_markup=admin_back_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_download_userlist")
async def admin_download_userlist(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    users = db_query("SELECT username, user_id, phone, balance, orders_count, is_vip, is_reseller FROM users", fetchall=True)
    if not users: return await call.answer("❌ No users found in the database.", show_alert=True)
    file_content = "FULL DATABASE DUMP\n" + "="*100 + "\n"
    for u in users:
        uname = u[0] if u[0] else "No_Username"
        uid = u[1]
        phone = u[2] if u[2] else "No_Phone"
        bal = u[3]
        orders = u[4]
        vip_status = "YES" if u[5] else "NO"
        res_status = "YES" if u[6] else "NO"
        file_content += f"UID: {uid} | UNAME: {uname} | PHONE: {phone} | BAL: ₹{bal:.2f} | BUY: {orders} | VIP: {vip_status} | RES: {res_status}\n"
    doc = BufferedInputFile(file_content.encode('utf-8'), filename=f"DB_{datetime.now().strftime('%Y%m%d')}.txt")
    await call.message.answer_document(document=doc, caption="📋 <b>Database export complete.</b>", parse_mode='HTML')
    await call.answer()

@dp.message(AdminStates.manage_target_user)
async def process_user_lookup(m: Message, state: FSMContext):
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    loader_msg = await hacker_loading(m, "Querying User Database")
    user_q = db_query("SELECT user_id, first_name, username, balance, is_reseller, orders_count, spent, joined_date, is_banned, warnings, is_vip FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await loader_msg.edit_text("❌ Target not found in the grid. Check ID/Username syntax.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name, u_user, bal, is_res, orders, spent, joined, is_banned, warnings, is_vip = user_q
    await state.update_data(target_u_id=u_id)
    status_emoji = "🔴 BANNED" if is_banned else "🟢 ACTIVE"
    tags = []
    if is_res: tags.append("👑 Reseller")
    if is_vip: tags.append("🌟 VIP")
    type_str = " | ".join(tags) if tags else "👤 Regular"
    text = (f"🛡 <b><u>USER CONTROL TERMINAL</u></b> 🛡\n━━━━━━━━━━━━━━━━━━\n📛 <b>Name:</b> {u_name} (@{u_user})\n🆔 <b>ID:</b> <code>{u_id}</code>\n📊 <b>Status:</b> {status_emoji}\n🔰 <b>Type:</b> {type_str}\n⚠️ <b>Warnings Issued:</b> {warnings}\n━━━━━━━━━━━━━━━━━━\n💰 <b>Wallet Balance:</b> {fmt_curr(bal)}\n📦 <b>Orders:</b> {orders} | 💸 <b>Total Spent:</b> {fmt_curr(spent)}\n📅 <b>Joined:</b> {joined}")
    ban_btn_text = "Unban ✅" if is_banned else "Ban 🚫"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add Funds ➕", callback_data=f"usrctrl_add_{u_id}", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="Minus Funds ➖", callback_data=f"usrctrl_min_{u_id}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="danger")],
        [InlineKeyboardButton(text=ban_btn_text, callback_data=f"usrctrl_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="danger"), 
         InlineKeyboardButton(text="Warn User ⚠️", callback_data=f"usrctrl_warn_{u_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="danger")],
        [InlineKeyboardButton(text="Give VIP 🌟" if not is_vip else "Remove VIP 🚫", callback_data=f"usrctrl_vip_{u_id}", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await loader_msg.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("usrctrl_"))
async def handle_user_actions(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    action = call.data.split("_")[1]
    u_id = int(call.data.split("_")[2])
    await state.update_data(target_u_id=u_id)
    if action == "ban":
        current_status = db_query("SELECT is_banned FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Yes, Ban", callback_data=f"confirm_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="danger"), 
                 InlineKeyboardButton(text="❌ Cancel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
            ])
            await call.message.edit_text(f"⚠️ Are you sure you want to <b>BAN</b> user <code>{u_id}</code>?", reply_markup=kb, parse_mode='HTML')
            await state.set_state(AdminStates.confirm_ban)
        else:
            db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ User unbanned successfully!", show_alert=True)
            m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "vip":
        current_status = db_query("SELECT is_vip FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 1:
            db_query("UPDATE users SET is_vip=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ VIP Removed!", show_alert=True)
        else:
            db_query("UPDATE users SET is_vip=1, vip_since=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
            await call.answer("✅ VIP Granted!", show_alert=True)
        m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "add":
        await call.message.edit_text("💰 Enter the amount to <b>ADD</b> to this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_add_money)
    elif action == "min":
        await call.message.edit_text("💸 Enter the amount to <b>DEDUCT</b> from this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_minus_money)
    elif action == "warn":
        await call.message.edit_text("⚠️ Type the strict warning message you want to send directly to this user:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_warning)

@dp.callback_query(F.data.startswith("confirm_ban_"))
async def confirm_ban(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    u_id = int(call.data.split("_")[2])
    db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (u_id,))
    await call.answer("🔴 User has been banned!", show_alert=True)
    await state.clear()
    m = call.message; m.text = str(u_id); await process_user_lookup(m, state)

@dp.message(AdminStates.wait_for_add_money)
async def exec_add_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully added {fmt_curr(amt)} to target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        try: await bot.send_message(u_id, f"💰 <b>Wallet Top-up!</b>\nAdmin has manually added {fmt_curr(amt)} to your wallet.", parse_mode='HTML')
        except: pass
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_minus_money)
async def exec_minus_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully deducted {fmt_curr(amt)} from target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_warning)
async def exec_warn_user(m: Message, state: FSMContext):
    data = await state.get_data()
    u_id = data['target_u_id']
    warn_text = m.text
    db_query("UPDATE users SET warnings = warnings + 1 WHERE user_id=?", (u_id,))
    await m.answer(f"✅ Official warning dispatched to <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
    try: await bot.send_message(u_id, f"⚠️ <b>OFFICIAL WARNING FROM SYSTEM ADMIN:</b>\n\n{warn_text}\n\n<i>Subsequent infractions may lead to an automated grid ban.</i>", parse_mode='HTML')
    except: pass
    await state.clear()

# ==============================================================================
# 18. ADMIN STATISTICS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_stats")
async def admin_dashboard_stats(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    t_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    t_resellers = db_query("SELECT COUNT(*) FROM users WHERE is_reseller=1", fetchone=True)[0]
    t_vip = db_query("SELECT COUNT(*) FROM users WHERE is_vip=1", fetchone=True)[0]
    t_prods = db_query("SELECT COUNT(*) FROM products", fetchone=True)[0]
    t_keys = db_query("SELECT COUNT(*) FROM product_keys WHERE is_used=0", fetchone=True)[0]
    t_rev = db_query("SELECT SUM(spent) FROM users", fetchone=True)[0] or 0.0
    today_str = datetime.now().strftime("%Y-%m-%d")
    t_spins = db_query("SELECT COUNT(*) FROM users WHERE last_spin LIKE ?", (f"{today_str}%",), fetchone=True)[0]
    msg = (f"📊 <b><u>GRID INTELLIGENCE DASHBOARD</u></b> 📊\n━━━━━━━━━━━━━━━━━━\n👥 <b>Total Grid Users:</b> {t_users}\n👑 <b>Wholesale Resellers:</b> {t_resellers}\n🌟 <b>Elite VIP Members:</b> {t_vip}\n━━━━━━━━━━━━━━━━━━\n📦 <b>Active Products:</b> {t_prods}\n🔑 <b>Unused Keys in Vault:</b> {t_keys}\n💰 <b>Total Gross Revenue:</b> {fmt_curr(t_rev)}\n🎰 <b>Ludo Spins Today:</b> {t_spins}\n━━━━━━━━━━━━━━━━━━")
    await call.message.edit_text(msg, reply_markup=admin_back_kb(), parse_mode='HTML')

# ==============================================================================
# 19. ADMIN CATEGORY & PRODUCT MANAGEMENT
# ==============================================================================
@dp.callback_query(F.data == "admin_manage_categories")
async def admin_manage_categories(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)

    categories = db_query(
        """SELECT c.id, c.name, COUNT(p.id)
           FROM product_categories c
           LEFT JOIN products p ON p.category = c.name
           WHERE c.is_active=1
           GROUP BY c.id, c.name
           ORDER BY c.sort_order, c.name COLLATE NOCASE""",
        fetchall=True,
        commit=False,
    ) or []
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for category_id, name, product_count in categories:
        label = str(name)
        if len(label) > 36:
            label = label[:33] + "..."
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"✏️ {label}",
                callback_data=f"admin_rename_cat_{int(category_id)}",
                style="primary",
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"admin_delete_cat_{int(category_id)}",
                style="danger",
            ),
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="➕ Add New Category",
            callback_data="admin_add_category",
            icon_custom_emoji_id=get_emoji_icon("product_store"),
            style="success",
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Back to Admin",
            callback_data="admin_panel_back",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger",
        )
    ])
    await call.message.edit_text(
        "🗂 <b>Manage Product Categories</b>\n\n"
        "These names control the buttons shown in Product Store and Add Product.\n"
        "✏️ Rename also updates every product in that category.\n"
        "🗑 Delete works only when the category has no products.",
        reply_markup=kb,
        parse_mode='HTML',
    )

@dp.callback_query(F.data == "admin_add_category")
async def admin_add_category_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "➕ <b>Add Product Category</b>\n\n"
        "Send the button name you want users to see.\n"
        "Example: <code>ANDROID NON ROOT</code>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.add_category_name)

@dp.message(AdminStates.add_category_name)
async def admin_add_category_save(m: Message, state: FSMContext):
    name = " ".join((m.text or "").split()).strip()
    if not name or len(name) > 48:
        return await m.answer(
            "❌ Category name must be between 1 and 48 characters. Try again.",
            reply_markup=admin_back_kb(),
            parse_mode='HTML',
        )
    duplicate = db_query(
        "SELECT id FROM product_categories WHERE name=? COLLATE NOCASE",
        (name,),
        fetchone=True,
        commit=False,
    )
    if duplicate:
        return await m.answer(
            "❌ This category already exists. Send a different name.",
            reply_markup=admin_back_kb(),
            parse_mode='HTML',
        )
    next_order = db_query(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM product_categories",
        fetchone=True,
    )[0]
    db_query(
        "INSERT INTO product_categories (name, sort_order, is_active) VALUES (?, ?, 1)",
        (name, next_order),
    )
    await state.clear()
    await m.answer(
        f"✅ Category <b>{html.escape(name)}</b> added.\n"
        "It is now available in Product Store and Add Product.",
        reply_markup=admin_kb(),
        parse_mode='HTML',
    )

@dp.callback_query(F.data.startswith("admin_rename_cat_"))
async def admin_rename_category_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    try:
        category_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category.", show_alert=True)
    row = db_query(
        "SELECT name FROM product_categories WHERE id=? AND is_active=1",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not row:
        return await call.answer("Category not found.", show_alert=True)
    await state.update_data(rename_category_id=category_id, rename_category_old=str(row[0]))
    await call.message.edit_text(
        f"✏️ <b>Rename Category</b>\n\nCurrent name: <code>{html.escape(str(row[0]))}</code>\n"
        "Send the new button name:",
        reply_markup=admin_back_kb(),
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.rename_category_name)

@dp.message(AdminStates.rename_category_name)
async def admin_rename_category_save(m: Message, state: FSMContext):
    name = " ".join((m.text or "").split()).strip()
    if not name or len(name) > 48:
        return await m.answer("❌ Category name must be between 1 and 48 characters. Try again.", reply_markup=admin_back_kb(), parse_mode='HTML')
    data = await state.get_data()
    category_id = int(data["rename_category_id"])
    old_name = str(data["rename_category_old"])
    duplicate = db_query(
        "SELECT id FROM product_categories WHERE name=? COLLATE NOCASE AND id!=?",
        (name, category_id),
        fetchone=True,
        commit=False,
    )
    if duplicate:
        return await m.answer("❌ This category already exists. Send a different name.", reply_markup=admin_back_kb(), parse_mode='HTML')
    db_query("UPDATE product_categories SET name=? WHERE id=?", (name, category_id))
    db_query("UPDATE products SET category=? WHERE category=?", (name, old_name))
    old_emoji = get_setting(f"cat_emoji_{old_name}", "")
    if old_emoji:
        set_setting(f"cat_emoji_{name}", old_emoji)
        db_query("DELETE FROM settings WHERE key=?", (f"cat_emoji_{old_name}",))
    await state.clear()
    await m.answer(
        f"✅ Category renamed to <b>{html.escape(name)}</b>.\n"
        "Products assigned to the old name were updated too.",
        reply_markup=admin_kb(),
        parse_mode='HTML',
    )

@dp.callback_query(F.data.startswith("admin_delete_cat_"))
async def admin_delete_category_start(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        category_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category.", show_alert=True)
    row = db_query(
        """SELECT c.name, COUNT(p.id)
           FROM product_categories c
           LEFT JOIN products p ON p.category=c.name
           WHERE c.id=? AND c.is_active=1
           GROUP BY c.id, c.name""",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not row:
        return await call.answer("Category not found.", show_alert=True)
    name, product_count = str(row[0]), int(row[1] or 0)
    if product_count:
        return await call.answer(
            f"Cannot delete '{name}' because {product_count} product(s) use it. "
            "Move or delete those products first.",
            show_alert=True,
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Delete Category", callback_data=f"admin_confirm_delete_cat_{category_id}", style="danger")],
        [InlineKeyboardButton(text="Cancel", callback_data="admin_manage_categories", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")],
    ])
    await call.message.edit_text(
        f"⚠️ Delete category <b>{html.escape(name)}</b>?\n\n"
        "This removes its button from the shop. No products are attached.",
        reply_markup=kb,
        parse_mode='HTML',
    )

@dp.callback_query(F.data.startswith("admin_confirm_delete_cat_"))
async def admin_delete_category_confirm(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        category_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category.", show_alert=True)
    row = db_query(
        "SELECT name FROM product_categories WHERE id=? AND is_active=1",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not row:
        return await call.answer("Category not found.", show_alert=True)
    name = str(row[0])
    in_use = db_query("SELECT COUNT(*) FROM products WHERE category=?", (name,), fetchone=True, commit=False)[0]
    if int(in_use or 0):
        return await call.answer("Category now has products; delete cancelled.", show_alert=True)
    db_query("DELETE FROM product_categories WHERE id=?", (category_id,))
    db_query("DELETE FROM settings WHERE key=?", (f"cat_emoji_{name}",))
    await call.answer("✅ Category deleted.", show_alert=True)
    await admin_manage_categories(call)

@dp.callback_query(F.data == "admin_add_prod")
async def add_prod_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for category_id, cat in get_shop_categories():
        emoji_id = get_category_emoji(cat)
        kb.inline_keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"addprod_catid_{category_id}", icon_custom_emoji_id=emoji_id, style="danger")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Cancel", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("<b>Step 1:</b> Choose the <b>Category</b> for this product:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_add_prod_api")
async def add_prod_start_api(call: CallbackQuery, state: FSMContext):
    """Same product wizard as 'Add Product', but skips the Yes/No question and
    always ends by asking for the upstream PID + duration — a shorter, dedicated
    path for products that come from an API panel (ABCD, CTB, etc.)."""
    if not is_admin_user(call.from_user.id): return
    await state.update_data(force_api=1)
    await add_prod_start(call, state)

@dp.callback_query(F.data.startswith("addprod_catid_"))
async def add_prod_category_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    try:
        category_id = int(call.data.split("addprod_catid_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category selection.", show_alert=True)
    category_row = db_query(
        "SELECT name FROM product_categories WHERE id=? AND is_active=1",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not category_row:
        return await call.message.edit_text(
            "❌ <b>This category is no longer available.</b>",
            reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    category = str(category_row[0])
    await state.update_data(cat=category)
    await call.message.edit_text(f"<b>Step 2:</b> Enter <b>PANEL NAME</b>\n(e.g., 'MST PANEL', 'DRIP PANEL'):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_panel_name)

# ==============================================================================
# 📸 AI ADD PRODUCTS FROM SCREENSHOT (API mode)
# Admin sends ONE screenshot of a panel showing multiple products/plans with
# their PID (external product id) + name/duration visible. AI extracts ONLY
# name + PID for each one (prices are almost never reliable from a PID
# screenshot). The bot then asks price + reseller price one product at a
# time, in order, and creates each as an API-enabled product (external
# duration = the plan name, device limit fixed "1", validity fixed "⏳" —
# same defaults as the manual "Add Product (API)" flow).
# ==============================================================================

async def _get_active_ai_config() -> Optional[Tuple[str, str, str]]:
    active_id = int(get_setting("ai_active_api_id", "0") or 0)
    row = None
    if active_id:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs WHERE id=?",
            (active_id,), fetchone=True, commit=False,
        )
    if not row or not row[2]:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs WHERE is_active=1 ORDER BY id DESC LIMIT 1",
            fetchone=True, commit=False,
        )
    if not row or not row[2]:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs ORDER BY id DESC LIMIT 1",
            fetchone=True, commit=False,
        )
    if not row or not row[2]:
        return None
    return row[0], row[1], row[2]

async def _extract_products_from_screenshot(image_bytes: bytes, mime: str, caption: str) -> Tuple[Optional[list], str]:
    """Returns (list_of_{name, external_product_id}_or_None, error_message)."""
    config = await _get_active_ai_config()
    if not config:
        return None, "❌ Koi AI API configure nahi hai. Pehle Admin Panel > 🤖 AI API Paste se ek vision-capable model (jaise gpt-4o / gpt-4o-mini) add karein."
    base_url, model, api_key = config
    endpoint = str(base_url).rstrip("/") + "/chat/completions"
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    instruction = (
        "You will be shown a screenshot of a shop/panel listing multiple products or plans, "
        "each with a PID (product id / external id) and a name or duration label (e.g. '1 Day', "
        "'2 Day', 'Weekly'). Extract EVERY item you can see into a JSON array only — no prose, "
        "no markdown fences. Each item must be an object with exactly these keys: "
        '"name" (the plan/duration label, e.g. "1 Day"), '
        '"external_product_id" (the PID exactly as shown, as a string). '
        "Do NOT invent or guess any prices — only extract name and PID. "
        f"Admin's caption (extra context, ignore if irrelevant): {caption or '(none)'}\n\n"
        "Respond with ONLY the JSON array, nothing else."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You extract structured product data from images and reply with strict JSON only."},
            {"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    logger.warning("AI vision API returned HTTP %s: %s", response.status, body)
                    return None, f"❌ AI API error (HTTP {response.status}). Check your AI API key/model in settings."
        raw = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, "❌ AI API timeout/connection error. Try again in a bit."
    except Exception:
        logger.exception("AI bulk-add vision request failed")
        return None, "❌ Unexpected error calling the AI API."

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list) or not parsed:
            return None, "❌ AI ko is screenshot me koi product samajh nahi aaya. Ek clearer screenshot try karein."
        return parsed, ""
    except Exception:
        logger.warning("AI bulk-add: could not parse JSON: %s", raw[:500])
        return None, "❌ AI ka response samajh nahi aaya. Dobara try karein ya ek clearer screenshot bhejein."

@dp.callback_query(F.data == "admin_ai_bulk_add")
async def ai_bulk_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    if not await _get_active_ai_config():
        return await call.message.edit_text(
            "❌ <b>Koi AI API configured nahi hai.</b>\n\n"
            "Pehle Admin Panel → <b>🤖 AI API Paste</b> se ek vision-capable model "
            "(jaise <code>gpt-4o</code> ya <code>gpt-4o-mini</code>) add karein, phir wapas yahan aayein.",
            reply_markup=admin_back_kb(), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for category_id, cat in get_shop_categories():
        emoji_id = get_category_emoji(cat)
        kb.inline_keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"aiaddprod_catid_{category_id}", icon_custom_emoji_id=emoji_id, style="danger")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Cancel", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        "📸 <b>AI Add Products — Step 1:</b> Choose the <b>Category</b> for these products:",
        reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("aiaddprod_catid_"))
async def ai_bulk_add_category_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    try:
        category_id = int(call.data.split("aiaddprod_catid_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category selection.", show_alert=True)
    category_row = db_query("SELECT name FROM product_categories WHERE id=? AND is_active=1", (category_id,), fetchone=True, commit=False)
    if not category_row:
        return await call.message.edit_text("❌ <b>This category is no longer available.</b>", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
    await state.update_data(ai_cat=str(category_row[0]))
    await call.message.edit_text(
        "📸 <b>Step 2:</b> Enter <b>PANEL NAME</b>\n(e.g., 'MST PANEL', 'DRIP PANEL') — all products from this screenshot will be filed under it:",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.ai_bulk_add_panel_name)

@dp.message(AdminStates.ai_bulk_add_panel_name)
async def ai_bulk_add_panel_name(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    if not m.text or not m.text.strip():
        return await m.answer("❌ Panel name khali nahi ho sakta.")
    await state.update_data(ai_panel_name=m.text.strip())
    await m.answer(
        "📸 <b>Step 3:</b> Ab ek <b>screenshot</b> bhejo jisme saare products/plans apne <b>PID</b> ke saath dikhte hon.\n\n"
        "<i>Price yahan mat likhna — wo agle step me ek-ek product ke liye alag se poochunga.</i>",
        parse_mode='HTML')
    await state.set_state(AdminStates.ai_bulk_add_screenshot)

@dp.message(AdminStates.ai_bulk_add_screenshot, F.photo)
async def ai_bulk_add_screenshot_received(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    processing = await m.answer("⏳ <b>AI screenshot analyse kar raha hai...</b> Thoda intezaar karein.", parse_mode='HTML')
    try:
        photo = m.photo[-1]
        buf = await bot.download(photo.file_id)
        image_bytes = buf.read()
        products, err = await _extract_products_from_screenshot(image_bytes, "image/jpeg", m.caption or "")
    except Exception:
        logger.exception("ai_bulk_add_screenshot_received failed")
        products, err = None, "❌ Screenshot process karte waqt error aaya. Dobara try karein."

    if not products:
        return await processing.edit_text(err or "❌ Kuch samajh nahi aaya.", reply_markup=admin_back_kb(), parse_mode='HTML')

    cleaned = []
    for p in products:
        try:
            name = str(p.get("name", "")).strip()
            pid = str(p.get("external_product_id", "")).strip()
        except AttributeError:
            continue
        if not name or not pid:
            continue
        cleaned.append({"name": name, "external_product_id": pid})

    if not cleaned:
        return await processing.edit_text(
            "❌ AI ne kuch products dhoonde lekin unme name/PID clear nahi tha. Ek clearer screenshot try karein.",
            reply_markup=admin_back_kb(), parse_mode='HTML')

    await state.update_data(ai_products=cleaned, ai_item_index=0)
    listing = "\n".join(f"• <b>{html.escape(p['name'])}</b> — PID <code>{html.escape(p['external_product_id'])}</code>" for p in cleaned)
    await processing.edit_text(
        f"📸 <b>AI ne {len(cleaned)} product(s) detect kiye:</b>\n\n{listing}\n\n"
        "Ab har product ke liye price poochunga, ek-ek karke.",
        parse_mode='HTML')
    await _ask_next_ai_bulk_price(m, state)

async def _ask_next_ai_bulk_price(m: Message, state: FSMContext):
    data = await state.get_data()
    products = data.get("ai_products") or []
    idx = data.get("ai_item_index", 0)
    item = products[idx]
    await m.answer(
        f"💰 <b>[{idx + 1}/{len(products)}] {html.escape(item['name'])}</b>\n\nPublic (normal) price kya rakhna hai? (sirf number, ₹)",
        parse_mode='HTML')
    await state.set_state(AdminStates.ai_bulk_item_price)

@dp.message(AdminStates.ai_bulk_add_screenshot)
async def ai_bulk_add_screenshot_wrong_type(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    await m.answer("📸 Please send a <b>screenshot (photo)</b>, not text.", parse_mode='HTML')

@dp.message(AdminStates.ai_bulk_item_price)
async def ai_bulk_item_price(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    try:
        price = float((m.text or "").strip())
        if price <= 0: raise ValueError
    except (ValueError, TypeError):
        return await m.answer("❌ Valid number bhejein (e.g., 50).")
    data = await state.get_data()
    products = data.get("ai_products") or []
    idx = data.get("ai_item_index", 0)
    products[idx]["price"] = price
    await state.update_data(ai_products=products)
    await m.answer(f"👑 <b>{html.escape(products[idx]['name'])}</b> — Reseller price kya rakhna hai? (sirf number, ₹)", parse_mode='HTML')
    await state.set_state(AdminStates.ai_bulk_item_reseller_price)

@dp.message(AdminStates.ai_bulk_item_reseller_price)
async def ai_bulk_item_reseller_price(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    try:
        reseller_price = float((m.text or "").strip())
        if reseller_price <= 0: raise ValueError
    except (ValueError, TypeError):
        return await m.answer("❌ Valid number bhejein (e.g., 40).")
    data = await state.get_data()
    products = data.get("ai_products") or []
    idx = data.get("ai_item_index", 0)
    products[idx]["reseller_price"] = reseller_price
    idx += 1
    await state.update_data(ai_products=products, ai_item_index=idx)

    if idx < len(products):
        await _ask_next_ai_bulk_price(m, state)
        return

    # All items priced — bulk insert as API-enabled products.
    category = data.get("ai_cat")
    panel_name = data.get("ai_panel_name")
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    created_ids = []
    for p in products:
        api_duration = normalize_api_duration(p["name"])
        c.execute(
            """INSERT INTO products
               (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
                external_enabled, external_product_id, requires_android_id, external_duration)
               VALUES (?, ?, ?, ?, ?, 1, '', ?, ?, 1, ?, 0, ?)""",
            (category, panel_name, p["name"], p["price"], p["reseller_price"], "⏳", "1", p["external_product_id"], api_duration),
        )
        created_ids.append(c.lastrowid)
    conn.commit(); conn.close()

    ids_str = ", ".join(f"<code>{i}</code>" for i in created_ids)
    lines = "\n".join(
        f"• <b>{html.escape(p['name'])}</b> — PID <code>{html.escape(p['external_product_id'])}</code> — ₹{p['price']:.2f} / 👑 ₹{p['reseller_price']:.2f}"
        for p in products
    )
    await m.answer(
        f"✅ <b>{len(created_ids)} API product(s) add ho gaye!</b>\n\n"
        f"🗂 {html.escape(category)} → {html.escape(panel_name)}\n\n{lines}\n\n"
        f"🆔 Product IDs: {ids_str}",
        reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()





@dp.message(AdminStates.add_prod_panel_name)
async def add_prod_panel_name(m: Message, state: FSMContext):
    await state.update_data(panel_name=m.text)
    await m.answer("<b>Step 3:</b> Enter <b>PACKAGE DURATION/DATE NAME</b>\n(e.g., '7 Days', '1 Month'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_name)

@dp.message(AdminStates.add_prod_name)
async def add_prod_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text)
    data = await state.get_data()
    if data.get('force_api'):
        # API products: skip validity + device-limit questions entirely —
        # validity is always stored as just the hourglass emoji, and device
        # limit is always fixed at "1".
        await state.update_data(validity="⏳", device_limit="1")
        await m.answer("💰 Enter standard **User Price** in Rupees (₹) (e.g., 500):", parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_price)
        return
    await m.answer("⏳ Enter Time Validity String (e.g., '24 Hours'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_validity)

@dp.message(AdminStates.add_prod_validity)
async def add_prod_validity(m: Message, state: FSMContext):
    await state.update_data(validity=m.text)
    await m.answer("📱 Enter strict Device Enforcement Limit (e.g., '1 Device HWID'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_device_limit)

@dp.message(AdminStates.add_prod_device_limit)
async def add_prod_device_limit(m: Message, state: FSMContext):
    await state.update_data(device_limit=m.text)
    await m.answer("💰 Enter standard **User Price** in Rupees (₹) (e.g., 500):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_price)

@dp.message(AdminStates.add_prod_price)
async def add_prod_price(m: Message, state: FSMContext):
    try:
        await state.update_data(price=float(m.text))
        await m.answer("👑 Enter wholesale **Reseller Price** in Rupees (₹) (e.g., 300):", parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_reseller_price)
    except (ValueError, TypeError): await m.answer("❌ Invalid input datatype! Must be numerical.")

@dp.message(AdminStates.add_prod_reseller_price)
async def add_prod_reseller_price(m: Message, state: FSMContext):
    try:
        await state.update_data(reseller_price=float(m.text))
        await m.answer("🔗 Enter direct APK/Payload Download Link (or type 'none' to omit):", parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_apk)
    except (ValueError, TypeError): await m.answer("❌ Invalid input datatype! Must be numerical.")

@dp.message(AdminStates.add_prod_apk)
async def add_prod_apk(m: Message, state: FSMContext):
    await state.update_data(apk="" if m.text.lower() == 'none' else m.text)
    data = await state.get_data()
    if data.get('force_api'):
        # Dedicated "Add Product (API)" path: no Yes/No question, straight to PID.
        await state.update_data(external_enabled=1)
        await m.answer("🆔 Enter the <b>External Product ID (PID)</b> used by the API:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_external_product_id)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes — Generate Key via API", callback_data="addprod_ext_yes", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="❌ No — Use Manual Keys", callback_data="addprod_ext_no", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await m.answer("🔗 <b>Does this product use the external API for automatic key generation?</b>", reply_markup=kb, parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_external)

@dp.callback_query(F.data == "addprod_ext_yes", AdminStates.add_prod_external)
async def add_prod_external_yes(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await state.update_data(external_enabled=1)
    await call.message.edit_text("🆔 Enter the <b>External Product ID (PID)</b> used by the API:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_external_product_id)

@dp.message(AdminStates.add_prod_external_product_id)
async def add_prod_external_pid(m: Message, state: FSMContext):
    pid = m.text.strip()
    if not pid:
        return await m.answer("❌ Product PID cannot be empty.")
    data = await state.get_data()
    # The API's duration is NOT necessarily the Telegram package name.
    # Ask for the exact duration/price tier configured on the API.
    await state.update_data(external_product_id=pid, requires_android_id=0)
    await m.answer(
        "⏱ <b>Enter the exact XYZ API duration/price tier.</b>\n\n"
        "Examples: <code>3 Hours</code>, <code>1 Day</code>, <code>7 Days</code>.\n"
        f"Your shop validity is: <code>{data.get('validity', '')}</code>\n\n"
        "If the API uses the same duration, send the same value.",
        reply_markup=admin_back_kb(), parse_mode='HTML'
    )
    await state.set_state(AdminStates.add_prod_external_duration)

@dp.message(AdminStates.add_prod_external_duration)
async def add_prod_external_duration(m: Message, state: FSMContext):
    api_duration = normalize_api_duration(m.text.strip())
    if not api_duration:
        return await m.answer("❌ API duration cannot be empty.")
    data = await state.get_data()
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    c.execute("""INSERT INTO products
        (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
         external_enabled, external_product_id, requires_android_id, external_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['cat'], data['panel_name'], data['name'], data['price'], data['reseller_price'], 1,
         data['apk'], data['validity'], data['device_limit'], 1, data['external_product_id'], 0, api_duration))
    prod_id = c.lastrowid
    conn.commit(); conn.close()
    await m.answer(
        f"✅ <b>API Product Created!</b>\n\nProduct ID: <code>{prod_id}</code>\n"
        f"External Product ID: <code>{data['external_product_id']}</code>\n"
        f"API Duration: <code>{api_duration}</code>\n\n"
        "The API will generate and deliver the key when the customer buys it.",
        reply_markup=admin_kb(), parse_mode='HTML'
    )
    await state.clear()

@dp.callback_query(F.data == "addprod_ext_no", AdminStates.add_prod_external)
async def add_prod_external_no(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await state.update_data(external_enabled=0, external_product_id="", requires_android_id=0)
    await call.message.edit_text("📥 <b>Manual Key Product</b>\n\nNow send the keys, one key per line:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_keys)

@dp.message(AdminStates.add_prod_keys)
async def add_prod_keys(m: Message, state: FSMContext):
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if not keys:
        return await m.answer("❌ No valid keys found. Send at least one key.")
    data = await state.get_data()
    stock = len(keys)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    c.execute("""INSERT INTO products
        (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
         external_enabled, external_product_id, requires_android_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['cat'], data['panel_name'], data['name'], data['price'], data['reseller_price'], stock,
         data['apk'], data['validity'], data['device_limit'], 0, '', 0))
    prod_id = c.lastrowid
    for k in keys:
        c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (prod_id, k))
    conn.commit(); conn.close()
    await m.answer(f"✅ <b>Manual Product Created!</b>\n\n📦 Product ID: <code>{prod_id}</code>\n🔒 Stock: {stock} keys", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_manage_prods")
async def admin_manage_prods(call: CallbackQuery):
    """Open the product manager using explicit DB columns and safe labels.

    The old version relied on SELECT * and positional indexes. That is fragile
    because the products table has had columns added over time. This version
    uses explicit columns, COALESCE for NULL stock, and short button labels so
    Telegram accepts the keyboard reliably.
    """
    if not is_admin_user(call.from_user.id):
        await call.answer("⛔ Admin only.", show_alert=True)
        return

    try:
        prods = db_query(
            """SELECT id, name, category, panel_name, COALESCE(stock, 0),
                      COALESCE(is_active, 1)
               FROM products
               ORDER BY category COLLATE NOCASE, panel_name COLLATE NOCASE, name COLLATE NOCASE, id""",
            fetchall=True,
            commit=False
        ) or []

        if not prods:
            await call.answer("📦 No products found.", show_alert=True)
            await call.message.edit_text(
                "📦 <b>Store Database</b>\n\nNo products are currently available.",
                reply_markup=admin_back_kb(),
                parse_mode='HTML'
            )
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for p_id, name, category, panel_name, stock, is_active in prods:
            status_dot = "🟢" if int(is_active or 0) else "🔴"
            category = str(category or "-")
            panel_name = str(panel_name or "-")
            name = str(name or "-")
            # Keep the label comfortably below Telegram's button-text limits.
            label = f"{status_dot} [{category}] {panel_name} - {name} (Stock: {int(stock or 0)})"
            if len(label) > 60:
                label = label[:57] + "..."
            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin_view_p_{int(p_id)}",
                    style="primary"
                )
            ])

        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text="🗑 Delete ALL Products",
                callback_data="admin_delete_all_products_confirm",
                style="danger",
            )
        ])
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text="🔄 Refresh Products",
                callback_data="admin_manage_prods",
                style="success"
            )
        ])
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text="Back to Admin",
                callback_data="admin_panel_back",
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )
        ])

        await call.answer()
        await call.message.edit_text(
            f"📦 <b>Manage Products</b>\n\nTotal products: <b>{len(prods)}</b>\nSelect a product below to edit, stock-manage, hide/unhide, or delete.",
            reply_markup=kb,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.exception("Manage Products failed")
        await call.answer("❌ Manage Products error. Check Railway logs.", show_alert=True)

@dp.callback_query(F.data == "admin_view_product_ids")
async def admin_view_product_ids(call: CallbackQuery):
    """Plain text list of every product's internal ID — the same ID a
    reseller-API client sends as `product_id` when they call /api/v1/buy."""
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)

    rows = db_query(
        """SELECT id, name, category, panel_name, reseller_price,
                  COALESCE(external_enabled, 0), COALESCE(is_active, 1)
           FROM products
           ORDER BY category COLLATE NOCASE, panel_name COLLATE NOCASE, name COLLATE NOCASE, id""",
        fetchall=True, commit=False,
    ) or []

    if not rows:
        await call.answer()
        return await call.message.edit_text(
            "🆔 <b>Product IDs</b>\n\nNo products found.",
            reply_markup=admin_back_kb(), parse_mode='HTML'
        )

    lines = [
        "🆔 <b>Product IDs</b>",
        "This is the exact <code>product_id</code> a reseller-API client sends to /api/v1/buy.",
        "🌐 = visible to reseller-API clients (Yes/API product) · 🔒 = manual stock only (not exposed)",
        "",
    ]
    for p_id, name, category, panel_name, rprice, ext_enabled, is_active in rows:
        tag = "🌐" if int(ext_enabled or 0) else "🔒"
        dot = "" if int(is_active or 1) else " (inactive)"
        panel = f" [{panel_name}]" if panel_name else ""
        lines.append(
            f"{tag} <code>{int(p_id)}</code> — {html.escape(str(name or ''))}{panel} "
            f"({html.escape(str(category or ''))}) ₹{float(rprice or 0):.2f}{dot}"
        )

    await call.answer()
    kb = admin_back_kb()
    # Telegram caps a single message at 4096 chars; split into chunks if needed.
    chunk = ""
    chunks = []
    for line in lines:
        candidate = (chunk + "\n" + line) if chunk else line
        if len(candidate) > 3800:
            chunks.append(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)

    await call.message.edit_text(chunks[0], reply_markup=(kb if len(chunks) == 1 else None), parse_mode='HTML')
    for extra in chunks[1:-1]:
        await call.message.answer(extra, parse_mode='HTML')
    if len(chunks) > 1:
        await call.message.answer(chunks[-1], reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_delete_all_products_confirm")
async def admin_delete_all_products_confirm(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    total = db_query("SELECT COUNT(*) FROM products", fetchone=True, commit=False)
    count = int(total[0] or 0) if total else 0
    if count == 0:
        return await call.answer("📦 There are no products to remove.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚠️ YES, DELETE {count} PRODUCTS", callback_data="admin_delete_all_products", style="danger")],
        [InlineKeyboardButton(text="Cancel", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")],
    ])
    await call.message.edit_text(
        "⚠️ <b>DELETE ALL PRODUCTS?</b>\n\n"
        f"This will permanently remove <b>{count}</b> products and their unused keys.\n"
        "Users, payments, orders, and transaction history will stay safe.\n\n"
        "<b>This action cannot be undone.</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )

@dp.callback_query(F.data == "admin_delete_all_products")
async def admin_delete_all_products(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM products")
        count = int(cur.fetchone()[0] or 0)
        cur.execute("DELETE FROM product_keys")
        cur.execute("DELETE FROM products")
        cur.execute("DELETE FROM purchase_locks")
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Delete all products failed")
        return await call.answer("❌ Could not remove all products. Check Railway logs.", show_alert=True)
    finally:
        conn.close()
    log_activity(call.from_user.id, "DELETE_ALL_PRODUCTS", f"Products removed: {count}")
    await call.answer(f"✅ Removed {count} products and their unused keys.", show_alert=True)
    await admin_manage_prods(call)

@dp.callback_query(F.data.startswith("admin_view_p_"))
async def admin_view_product(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        await call.answer("⛔ Admin only.", show_alert=True)
        return
    try:
        p_id = int(call.data.rsplit("_", 1)[1])
        prod = db_query(
            """SELECT id, category, panel_name, name, price_inr, reseller_price,
                      COALESCE(stock, 0), apk_link, validity, device_limit,
                      COALESCE(is_active, 1), external_product_id,
                      requires_android_id, external_enabled, api_product_id, external_duration
               FROM products WHERE id=?""",
            (p_id,), fetchone=True, commit=False
        )
        if not prod:
            return await call.answer("❌ Product not found in database.", show_alert=True)

        def esc(v):
            return html.escape(str(v if v is not None else ""))

        price_inr = float(prod[4] or 0.0)
        reseller_price = float(prod[5] or 0.0)
        stock = int(prod[6] or 0)
        external_enabled = bool(prod[13])
        text = (
            "📦 <b><u>PRODUCT DETAILS</u></b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<b>ID:</b> <code>{prod[0]}</code>\n"
            f"<b>Panel Group:</b> {esc(prod[1])}\n"
            f"<b>Panel Name:</b> {esc(prod[2])}\n"
            f"<b>Package:</b> {esc(prod[3])}\n"
            f"<b>Standard Price:</b> ₹{price_inr:.2f}\n"
            f"👑 <b>Wholesale Price:</b> ₹{reseller_price:.2f}\n"
            f"<b>Vault Stock:</b> {stock}\n"
            f"<b>API Stock Mode:</b> {'♾️ Unlimited (External API)' if external_enabled else '🔢 Manual Stock'}\n"
            f"<b>External Product ID:</b> {esc(prod[11]) if prod[11] else 'Not set'}\n"
            f"<b>API Duration:</b> {esc(prod[15]) if prod[15] else 'Not set'}\n"
            f"<b>Payload Link:</b> {esc(prod[7]) if prod[7] else 'None'}\n"
            f"<b>Time Config:</b> {esc(prod[8])}\n"
            f"<b>HWID Limit:</b> {esc(prod[9])}\n"
            f"<b>Visibility:</b> {'Active' if prod[10] else 'Hidden'}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        toggle_btn_text = "Hide Product 👁‍🗨" if prod[10] else "Unhide Product 👁"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Edit Panel Group 🏷️", callback_data=f"edit_p_{p_id}_cat", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Edit Panel Name 🏷️", callback_data=f"edit_p_{p_id}_panel_name", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Package Name ✏️", callback_data=f"edit_p_{p_id}_name", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Price 💰", callback_data=f"edit_p_{p_id}_price", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"),
             InlineKeyboardButton(text="Edit R-Price 👑", callback_data=f"edit_p_{p_id}_rprice", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Validity ⏳", callback_data=f"edit_p_{p_id}_validity", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Edit Device 📱", callback_data=f"edit_p_{p_id}_device", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit API Duration ⏱️", callback_data=f"edit_p_{p_id}_api_duration", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Edit PID 🆔", callback_data=f"edit_p_{p_id}_pid", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit APK Link 🔗", callback_data=f"edit_p_{p_id}_apk", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Add Keys ➕", callback_data=f"edit_p_{p_id}_keys", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")],
            [InlineKeyboardButton(text="Add to Stock 📦➕", callback_data=f"edit_p_{p_id}_stock_add", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")],
            [InlineKeyboardButton(text="Delete Key 🗑", callback_data=f"delkey_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger"),
             InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="primary")],
            [InlineKeyboardButton(text="🎟 Discount Coupon", callback_data=f"prodcoupon_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("redeem_icon"), style="success")],
            [InlineKeyboardButton(text="Delete Product 🗑", callback_data=f"delete_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger"),
             InlineKeyboardButton(text="BACK", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
        ])
        await call.answer()
        await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True, parse_mode='HTML')
    except Exception as e:
        logger.exception("Error in admin_view_product")
        await call.answer("❌ Could not load this product. Check Railway logs.", show_alert=True)

@dp.callback_query(F.data.startswith("prodcoupon_p_"))
async def admin_product_coupon_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    p_id = int(call.data.rsplit("_", 1)[1])
    existing = db_query("SELECT code, discount_percent, uses_left FROM product_coupons WHERE product_id=?", (p_id,), fetchone=True, commit=False)
    if existing:
        code, pct, uses_left = existing
        text = (
            f"🎟 <b>Discount Coupon — Product #{p_id}</b>\n\n"
            f"Code: <code>{html.escape(str(code))}</code>\n"
            f"Discount: <b>{float(pct):g}%</b>\n"
            f"Uses left: <b>{int(uses_left)}</b>\n\n"
            "Kisi ek naya coupon add karne se ye purana replace ho jayega."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Replace Coupon", callback_data=f"prodcoupon_add_{p_id}", style="primary")],
            [InlineKeyboardButton(text="🗑 Remove Coupon", callback_data=f"prodcoupon_del_{p_id}", style="danger")],
            [InlineKeyboardButton(text="BACK", callback_data=f"admin_view_p_{p_id}", style="danger")],
        ])
    else:
        text = f"🎟 <b>Discount Coupon — Product #{p_id}</b>\n\nAbhi koi coupon nahi hai is product ke liye."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Coupon", callback_data=f"prodcoupon_add_{p_id}", style="success")],
            [InlineKeyboardButton(text="BACK", callback_data=f"admin_view_p_{p_id}", style="danger")],
        ])
    await call.answer()
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("prodcoupon_del_"))
async def admin_product_coupon_delete(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    p_id = int(call.data.rsplit("_", 1)[1])
    db_query("DELETE FROM product_coupons WHERE product_id=?", (p_id,))
    await call.answer("✅ Coupon removed.", show_alert=True)
    await admin_product_coupon_menu(call)

@dp.callback_query(F.data.startswith("prodcoupon_add_"))
async def admin_product_coupon_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    p_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(pc_prod_id=p_id)
    await call.message.edit_text(
        "🎟 Coupon <b>code</b> bhejo (jo buyer type karega, e.g. <code>SAVE1</code>):",
        reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.add_prod_coupon_code)
    await call.answer()

@dp.message(AdminStates.add_prod_coupon_code)
async def admin_product_coupon_code(m: Message, state: FSMContext):
    code = (m.text or "").strip().upper()
    if not code:
        return await m.answer("❌ Code empty nahi ho sakta. Dubara bhejo.")
    await state.update_data(pc_code=code)
    await m.answer("💯 Discount kitne <b>%</b> ka ho (0 se 100 ke beech, e.g. <code>1</code> for 1%):", parse_mode="HTML")
    await state.set_state(AdminStates.add_prod_coupon_percent)

@dp.message(AdminStates.add_prod_coupon_percent)
async def admin_product_coupon_percent(m: Message, state: FSMContext):
    try:
        pct = float(m.text)
        if pct <= 0 or pct > 100:
            raise ValueError
    except (ValueError, TypeError):
        return await m.answer("❌ 0 se 100 ke beech ek number bhejo (e.g. 1).")
    await state.update_data(pc_pct=pct)
    await m.answer("🔢 Kitni baar use ho sakta hai ye coupon? (total usage limit, e.g. <code>50</code>):", parse_mode="HTML")
    await state.set_state(AdminStates.add_prod_coupon_uses)

@dp.message(AdminStates.add_prod_coupon_uses)
async def admin_product_coupon_uses(m: Message, state: FSMContext):
    try:
        uses = int(m.text)
        if uses <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return await m.answer("❌ Ek positive whole number bhejo (e.g. 50).")
    data = await state.get_data()
    p_id = data['pc_prod_id']
    code = data['pc_code']
    pct = data['pc_pct']
    db_query("INSERT OR REPLACE INTO product_coupons (code, product_id, discount_percent, uses_left) VALUES (?, ?, ?, ?)",
              (code, p_id, pct, uses))
    await m.answer(
        f"✅ <b>Coupon saved!</b>\n\nProduct: #{p_id}\nCode: <code>{html.escape(code)}</code>\n"
        f"Discount: {pct:g}%\nUses: {uses}",
        parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("toggle_p_"))
async def admin_toggle_product(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    try:
        p_id = int(call.data.rsplit("_", 1)[1])
        row = db_query("SELECT COALESCE(is_active,1) FROM products WHERE id=?", (p_id,), fetchone=True)
        if not row:
            return await call.answer("❌ Product not found.", show_alert=True)
        new_val = 0 if int(row[0]) == 1 else 1
        db_query("UPDATE products SET is_active=? WHERE id=?", (new_val, p_id))
        await call.answer("✅ Visibility updated!", show_alert=True)
        await admin_view_product(call)
    except Exception:
        logger.exception("Toggle product failed")
        await call.answer("❌ Could not update product visibility.", show_alert=True)

# ==============================================================================
# FIX: Edit product field – correctly handle different data types
# ==============================================================================
@dp.callback_query(F.data.startswith("edit_p_"))
async def start_edit_product(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    try:
        parts = call.data.split("_")
        p_id = int(parts[2]); field = "_".join(parts[3:])
        await state.update_data(edit_p_id=p_id, edit_field=field)
        if field == 'keys':
            await call.message.edit_text("📥 <b>Vault Injection</b>\nPaste the <b>NEW KEYS</b> to append to the stock (1 key per line):", reply_markup=admin_back_kb(), parse_mode='HTML')
            await state.set_state(AdminStates.wait_for_add_keys)
        elif field == 'stock_add':
            await call.message.edit_text(
                "📦 <b>Add to Stock</b>\n\nEnter how many stock units to add.\nExample: <code>100</code> or <code>200</code>",
                reply_markup=admin_back_kb(), parse_mode='HTML'
            )
            await state.set_state(AdminStates.wait_for_new_value)
        elif field == 'cat':
            kb = InlineKeyboardMarkup(inline_keyboard=[])
            for category_id, category_name in get_shop_categories():
                kb.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=category_name,
                        callback_data=f"edit_prod_cat_{p_id}_{category_id}",
                        icon_custom_emoji_id=get_category_emoji(category_name),
                        style="primary",
                    )
                ])
            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text="➕ Add New Category",
                    callback_data="admin_add_category",
                    style="success",
                )
            ])
            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text="BACK",
                    callback_data=f"admin_view_p_{p_id}",
                    icon_custom_emoji_id=get_emoji_icon("back"),
                    style="danger",
                )
            ])
            await call.message.edit_text(
                "🏷️ <b>Choose the new Panel Group/Category</b>:",
                reply_markup=kb,
                parse_mode='HTML',
            )
        else:
            field_name_map = {'cat': 'New Panel Group/Category Name', 'panel_name': 'New Panel Name', 'name': 'New Package/Date Name', 'price': 'New Standard Price in ₹', 'rprice': 'New Reseller Price in ₹', 'validity': 'New Time Validity String', 'device': 'New HWID Limit String', 'apk': 'New Payload Link (or type "none")', 'api_duration': 'Exact XYZ API Duration (e.g. 1 Day)', 'pid': 'New External Product ID (PID) — use id:INTERNAL_ID to buy from your own Reseller API', 'stock_add': 'Stock units to add'}
            await call.message.edit_text(f"✏️ Input the required data for: <b>{field_name_map[field]}</b>", reply_markup=admin_back_kb(), parse_mode='HTML')
            await state.set_state(AdminStates.wait_for_new_value)
        await call.answer()
    except Exception:
        logger.exception("start_edit_product failed for data=%s", call.data)
        await call.answer("❌ Could not open the editor for this field. Check the console log.", show_alert=True)

@dp.callback_query(F.data.startswith("edit_prod_cat_"))
async def edit_product_category_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    parts = call.data.split("_")
    try:
        p_id = int(parts[3])
        category_id = int(parts[4])
    except (IndexError, ValueError):
        return await call.answer("Invalid category selection.", show_alert=True)
    row = db_query(
        "SELECT name FROM product_categories WHERE id=? AND is_active=1",
        (category_id,),
        fetchone=True,
        commit=False,
    )
    if not row:
        return await call.answer("Category not found.", show_alert=True)
    db_query("UPDATE products SET category=? WHERE id=?", (str(row[0]), p_id))
    await state.clear()
    await call.answer("✅ Product category updated.", show_alert=True)
    await admin_view_product(call)

@dp.message(AdminStates.wait_for_new_value)
async def process_edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']; field = data['edit_field']; new_val = m.text.strip()
    
    # If field is price or reseller price, convert to float
    if field in ['price', 'rprice']:
        try:
            new_val = float(new_val)
        except (ValueError, TypeError):
            return await m.answer("❌ Invalid number format. Please enter a valid price (e.g., 500).")
    # If field is apk, store as string (don't convert to float!)
    elif field == 'apk':
        new_val = "" if new_val.lower() == 'none' else new_val
    elif field == 'pid':
        if not new_val:
            return await m.answer("❌ PID cannot be empty. Send the new External Product ID.")
    elif field == 'stock_add':
        try:
            add_qty = int(new_val)
            if add_qty <= 0 or add_qty > 100000:
                raise ValueError
        except (ValueError, TypeError):
            return await m.answer("❌ Enter a positive whole number up to 100000, e.g. 100 or 200.")
        db_query("UPDATE products SET stock=COALESCE(stock,0)+? WHERE id=?", (add_qty, p_id))
        new_stock = db_query("SELECT stock FROM products WHERE id=?", (p_id,), fetchone=True)[0]
        await m.answer(f"✅ <b>Stock Added!</b>\n\n➕ Added: <code>{add_qty}</code>\n📦 New Stock: <code>{new_stock}</code>", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
        return
    # For all other fields (cat, panel_name, name, validity, device), keep as string
    
    db_col_map = {'cat': 'category', 'panel_name': 'panel_name', 'name': 'name', 'price': 'price_inr', 'rprice': 'reseller_price', 'validity': 'validity', 'device': 'device_limit', 'apk': 'apk_link', 'api_duration': 'external_duration', 'pid': 'external_product_id'}
    if field not in db_col_map:
        return await m.answer("❌ Unsupported product field.")
    db_query(f"UPDATE products SET {db_col_map[field]}=? WHERE id=?", (new_val, p_id))
    await m.answer("✅ <b>Node updated gracefully!</b>", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.message(AdminStates.wait_for_add_keys)
async def process_add_keys(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if len(keys) == 0: return await m.answer("❌ Protocol breach: Zero valid keys found.", reply_markup=admin_kb(), parse_mode='HTML')
    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    for k in keys: c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (p_id, k))
    c.execute("UPDATE products SET stock = COALESCE(stock,0) + ? WHERE id=?", (len(keys), p_id))
    conn.commit(); conn.close()
    await m.answer(f"✅ <b>Vault Secure!</b> {len(keys)} new keys appended and encrypted.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("delete_p_"))
async def admin_delete_product(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    try:
        p_id = int(call.data.rsplit("_", 1)[1])
        conn = sqlite3.connect(DB_PATH, timeout=20)
        try:
            c = conn.cursor()
            c.execute("SELECT id FROM products WHERE id=?", (p_id,))
            if not c.fetchone():
                await call.answer("❌ Product not found.", show_alert=True)
                return
            c.execute("DELETE FROM product_keys WHERE product_id=?", (p_id,))
            c.execute("DELETE FROM products WHERE id=?", (p_id,))
            conn.commit()
        finally:
            conn.close()
        await call.answer("✅ Product and its unused key records deleted.", show_alert=True)
        await admin_manage_prods(call)
    except Exception:
        logger.exception("Delete product failed")
        await call.answer("❌ Could not delete product. Check Railway logs.", show_alert=True)

@dp.callback_query(F.data.startswith("delkey_p_"))
async def admin_delete_key_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    p_id = int(call.data.split("_")[2])
    await state.update_data(del_p_id=p_id)
    await call.message.edit_text("🗑 Send the <b>exact string match</b> of the key you wish to purge from the vault:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_delete_key)

@dp.message(AdminStates.wait_for_delete_key)
async def process_delete_key(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['del_p_id']
    key_to_delete = m.text.strip()
    key_data = db_query("SELECT id, is_used FROM product_keys WHERE product_id=? AND key_text=?", (p_id, key_to_delete), fetchone=True)
    if not key_data: return await m.answer("❌ Key not found. Check logs and try again.", reply_markup=admin_back_kb(), parse_mode='HTML')
    if key_data[1] == 1: return await m.answer("⚠️ Action Blocked: This key has already been dispatched to a user.", reply_markup=admin_back_kb(), parse_mode='HTML')
    db_query("DELETE FROM product_keys WHERE id=?", (key_data[0],))
    db_query("UPDATE products SET stock = stock - 1 WHERE id=?", (p_id,))
    await m.answer(f"✅ Key <code>{key_to_delete}</code> securely purged from vault.\n📦 Database indices updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 20. ADMIN TICKETS, BROADCAST, COUPONS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_tickets")
async def admin_view_tickets(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    tickets = db_query("SELECT id, user_id, message, created_at FROM tickets WHERE status='Open' LIMIT 1", fetchall=True)
    if not tickets: return await call.answer("✅ Zero pending issues. Grid is clean!", show_alert=True)
    t = tickets[0]
    text = (f"🎫 <b><u>ACTIVE TICKET #{t[0]}</u></b>\n👤 <b>Origin UID:</b> <code>{t[1]}</code>\n📅 <b>Timestamp:</b> {t[3]}\n\n📝 <b>Payload:</b>\n{t[2]}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Formulate Reply", callback_data=f"reply_ticket_{t[0]}_{t[1]}", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="❌ Force Close Ticket", callback_data=f"close_ticket_{t[0]}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("close_ticket_"))
async def close_ticket(call: CallbackQuery):
    ticket_id = call.data.split("_")[2]
    db_query("UPDATE tickets SET status='Closed' WHERE id=?", (ticket_id,))
    await call.answer("✅ Status set to Closed.", show_alert=True)
    await admin_view_tickets(call) 

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(call: CallbackQuery, state: FSMContext):
    data = call.data.split("_")
    ticket_id, user_id = data[2], data[3]
    await state.update_data(ticket_id=ticket_id, user_id=user_id)
    await call.message.edit_text(f"💬 Formulating reply for node <code>{user_id}</code>.\n\nType your message payload:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.ticket_reply_msg)

@dp.message(AdminStates.ticket_reply_msg)
async def send_ticket_reply(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['user_id'], f"📞 <b>Admin Reply (Ref #{data['ticket_id']}):</b>\n\n{m.text}", parse_mode='HTML')
        db_query("UPDATE tickets SET status='Closed' WHERE id=?", (data['ticket_id'],))
        await m.answer("✅ Payload delivered and connection closed successfully.", reply_markup=admin_kb(), parse_mode='HTML')
    except Exception as e: await m.answer(f"❌ Transmission Error: {e}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast_btn")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("📢 <b>Mass Broadcast Protocol</b>\n\nSend the rich message payload you wish to transmit globally across the grid:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.broadcast_msg)

@dp.message(AdminStates.broadcast_msg)
async def admin_broadcast_send(message: Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    sent, failed = 0, 0
    m = await message.answer("⏳ Broadcast protocol initiated... Do not interrupt.", parse_mode='HTML')
    for u in users:
        try:
            await message.send_copy(chat_id=u[0])
            sent += 1
        except Exception: failed += 1
        await asyncio.sleep(0.06) 
    await m.edit_text(f"✅ <b>Global Broadcast Complete!</b>\n\n🟢 Nodes reached: {sent}\n🔴 Nodes failed/blocked: {failed}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_create_coupon")
async def admin_create_coupon_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("🎟 Enter a highly secure alphanumeric sequence for the Promo Code:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_code)

@dp.message(AdminStates.add_coupon_code)
async def admin_coupon_code(m: Message, state: FSMContext):
    await state.update_data(code=m.text.strip().upper())
    await m.answer("💰 Enter the monetary reward payload in <b>RUPEES (₹)</b>:", parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_amount)

@dp.message(AdminStates.add_coupon_amount)
async def admin_coupon_amount(m: Message, state: FSMContext):
    try:
        await state.update_data(amount=float(m.text)) 
        await m.answer("👥 Enter the exact maximum threshold uses for this code:", parse_mode='HTML')
        await state.set_state(AdminStates.add_coupon_uses)
    except (ValueError, TypeError): await m.answer("❌ Non-numerical data detected. Aborting.")

@dp.message(AdminStates.add_coupon_uses)
async def admin_coupon_uses(m: Message, state: FSMContext):
    try:
        uses = int(m.text)
        data = await state.get_data()
        db_query("INSERT OR REPLACE INTO coupons (code, amount, uses_left) VALUES (?, ?, ?)", (data['code'], data['amount'], uses))
        await m.answer(f"✅ Protocol <b>{data['code']}</b> encoded!\nReward Vector: {fmt_curr(data['amount'])}\nThreshold Limit: {uses} executions.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Non-numerical data detected. Aborting.")

# ==============================================================================
# 21. ADMIN RESELLER & SPIN SETTINGS
# ==============================================================================
@dp.callback_query(F.data == "admin_reseller_menu")
async def admin_reseller_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else "ON"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Grant Reseller Rights", callback_data="reseller_make", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success"), 
         InlineKeyboardButton(text="➖ Revoke Reseller", callback_data="reseller_remove", icon_custom_emoji_id=get_emoji_icon("reseller"), style="danger")],
        [InlineKeyboardButton(text="📋 Audit Active Resellers", callback_data="reseller_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if sys_status == 'ON' else '🔴'} Auto-Upgrade System: {sys_status}", callback_data="admin_toggle_reseller_sys", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if sys_status == 'ON' else "danger")], 
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("👑 <b>Wholesale Reseller Protocols</b>\nSelect administrative action:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_toggle_reseller_sys")
async def toggle_reseller_sys(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('reseller_system_status', ?)", (new_status,))
    await admin_reseller_menu(call)

@dp.callback_query(F.data.in_(["reseller_make", "reseller_remove"]))
async def reseller_prompt_id(call: CallbackQuery, state: FSMContext):
    action = call.data
    await state.update_data(reseller_action=action)
    await call.message.edit_text("👤 Identify target node. Input <b>User ID</b> or <b>@username</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.reseller_manage_id)

@dp.message(AdminStates.reseller_manage_id)
async def process_reseller_manage(m: Message, state: FSMContext):
    data = await state.get_data()
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    user_q = db_query("SELECT user_id, first_name FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await m.answer("❌ Target completely ghosted. Not in database.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name = user_q[0], user_q[1]
    if data['reseller_action'] == "reseller_make":
        db_query("UPDATE users SET is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
        await m.answer(f"✅ Credentials upgraded. <b>{u_name}</b> (<code>{u_id}</code>) has reseller rights.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        db_query("UPDATE users SET is_reseller=0, account_type='Regular' WHERE user_id=?", (u_id,))
        await m.answer(f"✅ Credentials revoked. <b>{u_name}</b> (<code>{u_id}</code>) is back to regular user.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "reseller_view")
async def reseller_view(call: CallbackQuery):
    resellers = db_query("SELECT user_id, first_name, username FROM users WHERE is_reseller=1", fetchall=True)
    if not resellers: return await call.message.edit_text("📋 Zero active resellers found.", reply_markup=admin_back_kb(), parse_mode='HTML')
    text = "👑 <b><u>ACTIVE RESELLER AUDIT LOG</u></b> 👑\n━━━━━━━━━━━━━━━━━━\n"
    for r in resellers:
        uname = f"(@{r[2]})" if r[2] else ""
        text += f"👤 {r[1]} {uname}\n🆔 <code>{r[0]}</code>\n\n"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_spin_menu")
async def admin_spin_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    status = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    limit = db_query("SELECT value FROM settings WHERE key='daily_spin_limit'", fetchone=True)
    status_val = status[0] if status else 'ON'
    limit_val = limit[0] if limit else '50.0'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Append Reward Logic", callback_data="spin_add", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="❌ Drop Reward Logic", callback_data="spin_del", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="📋 Audit Configs", callback_data="spin_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary"), 
         InlineKeyboardButton(text="⚙️ Throttle Limits", callback_data="spin_limit", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if status_val == 'ON' else '🔴'} Master Toggle: {status_val}", callback_data="spin_toggle", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if status_val == 'ON' else "danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(f"🎰 <b>Advanced Ludo/Spin Algorithms</b>\nCurrent Threshold: ₹{limit_val}", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "spin_del")
async def spin_delete_menu(call: CallbackQuery):
    """Show individual reward delete buttons instead of leaving a dead button."""
    if not is_admin_user(call.from_user.id):
        return
    rewards = db_query(
        "SELECT id, amount FROM spin_rewards ORDER BY amount ASC",
        fetchall=True,
        commit=False,
    ) or []
    if not rewards:
        return await call.answer("No spin rewards are configured.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for reward_id, amount in rewards:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"❌ Delete {fmt_curr(float(amount or 0))}",
                callback_data=f"spin_delete_{int(reward_id)}",
                style="danger",
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="BACK TO SPIN SETTINGS",
            callback_data="admin_spin_menu",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger",
        )
    ])
    await call.message.edit_text(
        "❌ <b>Drop Reward Logic</b>\n\nSelect the reward amount to remove:",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )

@dp.callback_query(F.data.startswith("spin_delete_"))
async def spin_delete_reward(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        reward_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid reward.", show_alert=True)
    deleted = db_update_count("DELETE FROM spin_rewards WHERE id=?", (reward_id,))
    if not deleted:
        return await call.answer("Reward was already removed.", show_alert=True)
    await call.answer("Reward deleted.")
    await admin_spin_menu(call)

@dp.callback_query(F.data == "spin_limit")
async def spin_limit_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    current = get_setting("daily_spin_limit", "50.0")
    await call.message.edit_text(
        "⚙️ <b>Throttle Limits</b>\n\n"
        f"Current daily reward cap: <code>₹{html.escape(str(current))}</code>\n\n"
        "Send the new maximum reward amount (for example: <code>50</code>).",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.spin_set_limit)

@dp.message(AdminStates.spin_set_limit)
async def spin_limit_save(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    try:
        limit = float((m.text or "").strip())
        if limit <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return await m.answer("❌ Enter a positive number, for example: 50")
    set_setting("daily_spin_limit", f"{limit:g}")
    await state.clear()
    await m.answer(
        f"✅ Daily spin reward cap set to <b>{fmt_curr(limit)}</b>.",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )

@dp.callback_query(F.data == "spin_toggle")
async def spin_toggle(call: CallbackQuery):
    res = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('spin_status', ?)", (new_status,))
    await admin_spin_menu(call)

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('bot_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_toggle_start_alerts")
async def toggle_start_alerts(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    current = get_setting("start_alert_status", "ON")
    new_status = "OFF" if current == "ON" else "ON"
    set_setting("start_alert_status", new_status)
    await call.answer(f"Start alerts {new_status}")
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_payment_gateways")
async def admin_payment_gateways(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    zap_status = get_setting("zapupi_enabled", "ON")
    fam_status = get_setting("fampay_enabled", "ON")
    zap_config = "Configured" if get_setting("zapupi_api", "") else "API key missing"
    fam_config = (
        "Configured" if fampay_upi_id() and get_setting("fampay_api_key", "")
        else "UPI ID/API key missing"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⚡ ZapUPI: {zap_status} ({zap_config})",
            callback_data="admin_toggle_zapupi",
            style="success" if zap_status == "ON" else "danger"
        )],
        [InlineKeyboardButton(
            text=f"💳 FamPay QR: {fam_status} ({fam_config})",
            callback_data="admin_toggle_fampay",
            style="success" if fam_status == "ON" else "danger"
        )],
        [InlineKeyboardButton(text="⚙️ Setup ZapUPI API", callback_data="admin_setup_zapupi", style="primary")],
        [InlineKeyboardButton(text="🧾 Setup FamPay UPI ID", callback_data="admin_setup_fampay", style="primary")],
        [InlineKeyboardButton(text="🎯 Payment Mode (Quick Select)", callback_data="admin_payment_mode_menu", style="primary")],
        [InlineKeyboardButton(text="🔍 Debug FamPay Order", callback_data="admin_fampay_debug_menu", style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await call.message.edit_text(
        "💳 <b>Payment Gateway Control</b>\n\n"
        "ZapUPI creates live orders and auto-verifies them.\n"
        "FamPay uses FamGateway's dynamic QR + automatic status verification. "
        "No admin approval is needed.\n\n"
        "Dono ko independently ON/OFF kar sakte ho, ya neeche 'Payment Mode' se "
        "ek tap mein preset select kar sakte ho.",
        reply_markup=kb, parse_mode="HTML",
    )

@dp.callback_query(F.data.in_({"admin_toggle_zapupi", "admin_toggle_fampay"}))
async def admin_toggle_payment_gateway(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    name = "zapupi" if call.data.endswith("zapupi") else "fampay"
    key = f"{name}_enabled"
    current = get_setting(key, "ON")
    new_status = "OFF" if current == "ON" else "ON"
    set_setting(key, new_status)
    await call.answer(f"{name.upper()} is now {new_status}.", show_alert=True)
    await admin_payment_gateways(call)

@dp.callback_query(F.data == "admin_payment_mode_menu")
async def admin_payment_mode_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    zap_status = get_setting("zapupi_enabled", "ON")
    fam_status = get_setting("fampay_enabled", "ON")
    if zap_status == "ON" and fam_status == "OFF":
        current_mode = "Only ZapUPI"
    elif zap_status == "OFF" and fam_status == "ON":
        current_mode = "Only FamPay"
    elif zap_status == "ON" and fam_status == "ON":
        current_mode = "Both Active"
    else:
        current_mode = "Both OFF"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Only ZapUPI", callback_data="admin_payment_mode_zapupi", style="primary")],
        [InlineKeyboardButton(text="💳 Only FamPay", callback_data="admin_payment_mode_fampay", style="primary")],
        [InlineKeyboardButton(text="🔀 Both Active", callback_data="admin_payment_mode_both", style="success")],
        [InlineKeyboardButton(text="Back", callback_data="admin_payment_gateways", style="danger")],
    ])
    await call.message.edit_text(
        "🎯 <b>Payment Mode — Quick Select</b>\n\n"
        f"Current mode: <b>{current_mode}</b>\n\n"
        "Ek tap mein decide karo users ko checkout pe kaunsa gateway (ya dono) "
        "dikhega. Ye sirf ON/OFF flags set karta hai, dono gateway apni jagah "
        "independent hi rehte hain.",
        reply_markup=kb, parse_mode="HTML",
    )

@dp.callback_query(F.data.startswith("admin_payment_mode_"))
async def admin_payment_mode_set(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    mode = call.data.rsplit("_", 1)[1]
    if mode not in {"zapupi", "fampay", "both"}:
        return
    if mode == "zapupi":
        set_setting("zapupi_enabled", "ON")
        set_setting("fampay_enabled", "OFF")
        label = "Only ZapUPI"
    elif mode == "fampay":
        set_setting("zapupi_enabled", "OFF")
        set_setting("fampay_enabled", "ON")
        label = "Only FamPay"
    else:
        set_setting("zapupi_enabled", "ON")
        set_setting("fampay_enabled", "ON")
        label = "Both Active"
    await call.answer(f"Payment mode set: {label}", show_alert=True)
    await admin_payment_mode_menu(call)


@dp.callback_query(F.data == "admin_fampay_debug_menu")
async def admin_fampay_debug_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    rows = db_query(
        "SELECT order_id, user_id, amount_inr, status, timestamp FROM transactions "
        "WHERE payment_method='fampay' ORDER BY timestamp DESC LIMIT 10",
        fetchall=True, commit=False,
    ) or []
    kb_rows = []
    for order_id, user_id, amount, status, ts in rows:
        when = datetime.fromtimestamp(ts).strftime("%d-%b %H:%M")
        label = f"{'⏳' if status == 'pending' else '✅' if status == 'paid' else '❌'} ₹{amount:.0f} — {when} ({status})"
        kb_rows.append([InlineKeyboardButton(text=label, callback_data=f"admin_fampay_debug_run_{order_id}", style="primary")])
    kb_rows.append([InlineKeyboardButton(text="✏️ Type an Order ID", callback_data="admin_fampay_debug_type", style="success")])
    kb_rows.append([InlineKeyboardButton(text="🧪 Test create-order.php (₹10)", callback_data="admin_fampay_debug_create", style="success")])
    kb_rows.append([InlineKeyboardButton(text="Back", callback_data="admin_payment_gateways", style="danger")])
    text = (
        "🔍 <b>Debug FamPay Order</b>\n\n"
        "Neeche recent FamPay orders hain — kisi pe tap karo, bot seedha "
        "FamGateway ko poochega aur unka RAW jawab yahan dikhayega.\n\n"
        "Agar list mein order na mile to 'Type an Order ID' use karo."
    )
    if not rows:
        text += "\n\n<i>Koi FamPay order abhi DB mein nahi mila.</i>"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")


async def _run_fampay_debug_verify(message_obj: Message, internal_order_id: str) -> None:
    txn = db_query(
        "SELECT provider_order_id, amount_inr, status FROM transactions "
        "WHERE order_id=? AND payment_method='fampay'",
        (internal_order_id,), fetchone=True, commit=False,
    )
    if not txn:
        return await message_obj.answer(
            f"❌ Order <code>{html.escape(internal_order_id)}</code> FamPay transactions mein nahi mila.",
            parse_mode="HTML",
        )
    provider_id, amount, local_status = txn
    if not provider_id:
        return await message_obj.answer(
            f"⚠️ Order <code>{html.escape(internal_order_id)}</code> ka koi provider_order_id save nahi hai "
            "(create-order call ne kabhi order_id return nahi kiya tha).",
            parse_mode="HTML",
        )
    api_key = get_setting("fampay_api_key", "")
    if not api_key:
        return await message_obj.answer("⚠️ FamPay API key /admin mein set nahi hai.", parse_mode="HTML")

    await message_obj.answer(
        f"⏳ Checking order <code>{html.escape(internal_order_id)}</code> "
        f"(local status: <b>{local_status}</b>, ₹{amount:.2f})…",
        parse_mode="HTML",
    )
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            async with session.get(
                "https://famgateway.in/api/verify-order.php",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"order_id": provider_id, "api_key": api_key},
            ) as resp:
                status_code = resp.status
                raw_text = await resp.text()
    except asyncio.TimeoutError:
        return await message_obj.answer("❌ FamGateway timed out.", parse_mode="HTML")
    except aiohttp.ClientError as exc:
        return await message_obj.answer(f"❌ Connection error: {html.escape(str(exc))}", parse_mode="HTML")

    await _send_debug_response(message_obj, status_code, raw_text)


async def _run_fampay_debug_create(message_obj: Message, amount: float = 10.0) -> None:
    api_key = get_setting("fampay_api_key", "")
    if not api_key:
        return await message_obj.answer("⚠️ FamPay API key /admin mein set nahi hai.", parse_mode="HTML")
    await message_obj.answer(f"⏳ Testing create-order.php with ₹{amount:.2f}…", parse_mode="HTML")
    try:
        async with aiohttp.ClientSession(timeout=ZAPUPI_TIMEOUT) as session:
            async with session.post(
                "https://famgateway.in/api/create-order.php",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"amount": amount, "customer_name": "Debug Test", "redirect_url": f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else ""},
            ) as resp:
                status_code = resp.status
                raw_text = await resp.text()
    except asyncio.TimeoutError:
        return await message_obj.answer("❌ FamGateway timed out.", parse_mode="HTML")
    except aiohttp.ClientError as exc:
        return await message_obj.answer(f"❌ Connection error: {html.escape(str(exc))}", parse_mode="HTML")

    await _send_debug_response(message_obj, status_code, raw_text)


async def _send_debug_response(message_obj: Message, status_code: int, raw_text: str) -> None:
    try:
        parsed = json.loads(raw_text)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        pretty = raw_text
    pretty = pretty[:3500]  # keep well under Telegram's 4096-char message limit
    await message_obj.answer(
        f"📡 <b>HTTP {status_code}</b>\n<pre>{html.escape(pretty)}</pre>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_fampay_debug_run_"))
async def admin_fampay_debug_run(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    order_id = call.data[len("admin_fampay_debug_run_"):]
    await call.answer()
    await _run_fampay_debug_verify(call.message, order_id)


@dp.callback_query(F.data == "admin_fampay_debug_create")
async def admin_fampay_debug_create_cb(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    await call.answer()
    await _run_fampay_debug_create(call.message, 10.0)


@dp.callback_query(F.data == "admin_fampay_debug_type")
async def admin_fampay_debug_type_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "✏️ FamPay order ID paste karo (bot ka internal <code>FAM...</code> id):",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_fampay_debug_order)


@dp.message(AdminStates.wait_for_fampay_debug_order)
async def admin_fampay_debug_type_exec(m: Message, state: FSMContext):
    order_id = m.text.strip()
    await state.clear()
    await _run_fampay_debug_verify(m, order_id)

@dp.callback_query(F.data == "spin_add")
async def spin_add_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎰 Inject new decimal logic limit (e.g. 15.50):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.spin_add_reward)

@dp.message(AdminStates.spin_add_reward)
async def spin_add_exec(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        db_query("INSERT INTO spin_rewards (amount) VALUES (?)", (amt,))
        await m.answer(f"✅ Algorithm updated. New vector {fmt_curr(amt)} injected.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Math parsing error.")

@dp.callback_query(F.data == "spin_view")
async def spin_view(call: CallbackQuery):
    rewards = db_query("SELECT amount FROM spin_rewards ORDER BY amount ASC", fetchall=True)
    text = "🎰 <b>Live Ludo Constants</b>\n\n"
    for r in rewards: text += f"🎁 {fmt_curr(r[0])}\n"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_video")
async def admin_set_video_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("📹 Input direct streaming / YouTube Link for Tutorial system:\n<i>(Or type 'None' to clear registry):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_howto_video)

@dp.message(AdminStates.wait_for_howto_video)
async def exec_set_video(m: Message, state: FSMContext):
    link = m.text.strip()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('how_to_video', ?)", (link,))
    await m.answer("✅ Routing complete. Video linked.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_buy_gif")
async def admin_set_buy_gif_start(call: CallbackQuery, state: FSMContext):
    """Old 'Buy Loading GIF' button: it now sets the Store GIF / Video (that is where the GIF shows)."""
    await admin_set_store_media_start(call, state)

@dp.message(AdminStates.wait_for_buy_gif)
async def exec_set_buy_gif(m: Message, state: FSMContext):
    await exec_set_store_media(m, state)

@dp.callback_query(F.data == "admin_set_store_media")
async def admin_set_store_media_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kind, _ = _store_media()
    status = {"": "Not set", "video": "Video is active", "animation": "GIF is active", "url": "URL is active"}.get(kind, "Active")
    await call.message.edit_text(
        "🖼 <b>Store GIF / Video</b>\n\n"
        f"Current: <b>{status}</b>\n\n"
        "Jab bhi koi user <b>Enter Premium Store</b> par jayega, ye GIF/video store ke <b>upar</b> dikhega.\n\n"
        "👇 Ek <b>GIF</b> ya <b>video</b> yahan bhejo, YA direct GIF/MP4 URL paste karo.\n"
        "<i>(Hatane ke liye 'None' type karo)</i>",
        reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_store_media)

@dp.message(AdminStates.wait_for_store_media)
async def exec_set_store_media(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id): return
    value = ""
    if m.animation:
        value = f"animation|{m.animation.file_id}"
    elif m.video:
        value = f"video|{m.video.file_id}"
    elif m.video_note:
        return await m.answer("❌ Round video notes can't have a caption. Send a normal video or GIF.")
    elif m.document and (m.document.mime_type or "").startswith(("video/", "image/gif")):
        value = f"document|{m.document.file_id}"
    else:
        text = (m.text or "").strip()
        if text.lower() == "none":
            set_setting("store_media", "none")
            set_setting("buy_loading_gif", "")
            await state.clear()
            return await m.answer("✅ Store GIF/video removed.", reply_markup=admin_kb(), parse_mode="HTML")
        if text.startswith(("http://", "https://")):
            value = f"animation|{text}"
    if not value:
        return await m.answer("❌ Send a GIF, a video, a direct http(s) GIF/MP4 URL, or type 'None' to remove.")
    set_setting("store_media", value)
    await state.clear()
    preview = await _send_store_media(m, "👀 <b>Preview</b> — ye GIF/video Enter Premium Store ke upar dikhega.", None)
    if preview:
        await m.answer("✅ Store GIF/video saved. Ab store kholke dekho.", reply_markup=admin_kb(), parse_mode="HTML")
    else:
        await m.answer(
            "⚠️ Saved, lekin ye file bhej nahi paya. Koi dusra GIF/video bhejo (MP4 ya GIF) "
            "ya direct link try karo.", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_all_files")
async def admin_set_all_files_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("🔗 Input the direct Channel / Cloud URL for 'Download Files' button:\n<i>(Or type 'None' to format data):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_all_files_link)

@dp.message(AdminStates.wait_for_all_files_link)
async def exec_set_all_files(m: Message, state: FSMContext):
    link = m.text.strip()
    if link.lower() in {"none", "clear", "remove"}:
        link = "None"
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('all_files_link', ?)", (link,))
    await m.answer("✅ Global resource variable updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_payment_upi")
async def admin_set_payment_upi_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    current = fampay_upi_id() or "Not set"
    await call.message.edit_text(
        "🧾 <b>FamPay Auto-Verify Setup</b>\n\n"
        "Send the UPI ID that should receive exact-amount QR payments.\n"
        "Example: <code>yourname@fam</code> or <code>9876543210@ybl</code>\n\n"
        f"<b>Current:</b> <code>{html.escape(current)}</code>\n"
        "Users pay through a live FamGateway order QR. Type "
        "<code>clear</code> to remove it.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.update_data(fampay_full_setup=False)
    await state.set_state(AdminStates.wait_for_payment_upi)

@dp.message(AdminStates.wait_for_payment_upi)
async def save_payment_upi(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    setup_data = await state.get_data()
    full_setup = bool(setup_data.get("fampay_full_setup"))
    if value.lower() in {"clear", "none", "remove"}:
        set_setting("payment_upi_id", "")
        set_setting("fampay_upi_id", "")
        if full_setup:
            set_setting("fampay_api_key", "")
        await m.answer("✅ FamPay receiver UPI cleared.", reply_markup=admin_kb(), parse_mode="HTML")
        await state.clear()
        return
    if "@" not in value or " " in value or len(value) > 120:
        return await m.answer(
            "❌ Invalid UPI ID. Send a value like <code>yourname@fam</code>.",
            reply_markup=admin_back_kb(),
            parse_mode="HTML",
        )
    set_setting("payment_upi_id", value)
    set_setting("fampay_upi_id", value)
    if full_setup:
        await m.answer(
            f"✅ UPI ID saved: <code>{html.escape(value)}</code>\n\n"
            "Ab FamGateway API key bhejo. Ye key server-side save hogi; users ko nahi dikhegi.",
            reply_markup=admin_back_kb(), parse_mode="HTML",
        )
        await state.set_state(AdminStates.wait_for_fampay_api_key)
        return
    await m.answer(
        f"✅ FamPay receiver UPI saved: <code>{html.escape(value)}</code>\n"
        "FamGateway order QR receiver updated.",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )
    await state.clear()

@dp.message(AdminStates.wait_for_fampay_api_key)
async def save_fampay_api_key(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if value.lower() == "/cancel":
        await state.clear()
        return await m.answer("FamPay setup cancelled.", reply_markup=admin_kb(), parse_mode="HTML")
    if len(value) < 8 or len(value) > 240 or " " in value:
        return await m.answer("❌ Invalid API key. Paste the key exactly as provided by FamGateway.")
    set_setting("fampay_api_key", value)
    set_setting("fampay_enabled", "ON")
    await state.clear()
    await m.answer(
        "✅ <b>FamPay auto-verification enabled.</b>\n"
        "QR create hoga, status background mein poll hoga, aur success par wallet automatically credit hoga.",
        reply_markup=admin_kb(), parse_mode="HTML",
    )

@dp.callback_query(F.data == "admin_edit_emojis")
async def admin_edit_emojis(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    rows = db_query("SELECT key, value FROM settings WHERE key LIKE 'emoji_%' ORDER BY key", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for row in rows:
        key = row[0]
        slot = key.replace("emoji_", "")
        current_id = row[1] if row[1] else "Not set"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{slot} (ID: {current_id})", callback_data=f"edit_emoji_{slot}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Edit All Emojis</b>\nChoose an emoji slot to change its ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_emoji_"))
async def admin_edit_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    slot = call.data.split("edit_emoji_", 1)[1]
    await state.update_data(emoji_slot=slot)
    current = get_setting(f"emoji_{slot}", "Not set")
    await call.message.edit_text(f"✏️ Enter new emoji ID for <b>{slot}</b>:\nCurrent: {current}\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_emoji_slot)

@dp.message(AdminStates.wait_for_emoji_slot)
async def save_emoji_slot(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data['emoji_slot']
    new_id = m.text.strip()
    if new_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"emoji_{slot}",))
        await m.answer(f"✅ Reset emoji for '{slot}' to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not new_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"emoji_{slot}", new_id)
        await m.answer(f"✅ Emoji for '{slot}' updated to ID {new_id}.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_ui_menu")
async def admin_edit_ui_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Edit Start Menu Text", callback_data="edit_ui_start", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Edit Panel Select Text", callback_data="edit_ui_panel_select", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Edit Download Files Text", callback_data="edit_ui_download", icon_custom_emoji_id=get_emoji_icon("download"), style="primary")],
        [InlineKeyboardButton(text="Edit VIP Menu Text", callback_data="edit_ui_vip", icon_custom_emoji_id=get_emoji_icon("vip"), style="primary")],
        [InlineKeyboardButton(text="Edit Lucky Dice Text", callback_data="edit_ui_dice", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="primary")],
        [InlineKeyboardButton(text="Edit Add Balance Text", callback_data="edit_ui_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("✏️ <b>Edit User Interface Texts</b>\nSelect which text you want to modify:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_ui_"))
async def admin_edit_ui_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    ui_key = {
        "start": "start_menu",
        "panel_select": "panel_select_menu",
        "download": "download_files",
        "vip": "vip_menu",
        "dice": "lucky_dice_result",
        "add_balance": "add_balance_menu",
    }.get(call.data.removeprefix("edit_ui_"), call.data.removeprefix("edit_ui_"))
    await state.update_data(ui_key=ui_key)
    current_text = get_ui_text(ui_key)
    placeholders = {
        "start_menu": "{balance}",
        "panel_select_menu": "{name}, {user_id}, {balance}, {rank}, {cheats}",
        "add_balance_menu": "placeholders are optional",
    }
    hint = placeholders.get(ui_key, "placeholders are optional")
    await call.message.edit_text(
        f"📝 Send the new text for <b>{ui_key.upper()}</b> menu.\n"
        f"Use HTML formatting if needed. Hint: <code>{hint}</code>\n\n"
        f"Current text:\n<pre>{html.escape(current_text)}</pre>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.edit_ui_text)

@dp.message(AdminStates.edit_ui_text)
async def admin_save_ui_text(m: Message, state: FSMContext):
    data = await state.get_data()
    ui_key = data.get('ui_key')
    if not ui_key:
        await state.clear()
        return await m.answer("❌ Session expired. Edit UI Texts se dobara try karo.")
    if not m.text:
        return await m.answer("❌ Sirf text bhejo (photo/sticker nahi).")
    # Text styled inside Telegram (bold, italic, premium emoji...) arrives as entities; html_text keeps them.
    new_text = m.html_text if m.entities else m.text
    # Reject text that Telegram cannot render, so /start can never break.
    try:
        test_msg = await m.answer(new_text.replace("{", "(").replace("}", ")"), parse_mode='HTML')
        try:
            await test_msg.delete()
        except Exception:
            pass
    except TelegramBadRequest as exc:
        return await m.answer(
            f"❌ Ye text save nahi hua — HTML/format galat hai:\n<code>{html.escape(str(exc))}</code>\n\n"
            f"Tags theek se band karo (jaise <code>&lt;b&gt;text&lt;/b&gt;</code>) aur dobara bhejo.",
            parse_mode='HTML',
        )
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"ui_{ui_key}", new_text))
    await m.answer(f"✅ UI text <b>{ui_key}</b> updated successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_reseller_price")
async def admin_edit_reseller_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    prods = db_query("SELECT id, name, category, panel_name, reseller_price FROM products", fetchall=True)
    prods = sorted(prods or [], key=lambda row: (natural_sort_key(row[2]), natural_sort_key(row[3]), natural_sort_key(row[1])))
    if not prods: return await call.message.edit_text("No products to edit.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods:
        panel_name = p[3] if p[3] is not None else ""
        r_price = float(p[4]) if p[4] is not None else 0.0
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{p[2]} - {panel_name} - {p[1]} (₹{r_price:.2f})", callback_data=f"edit_reseller_{p[0]}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("👑 <b>Edit Reseller Price per Product</b>\nSelect a product to change its wholesale price:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_reseller_"))
async def admin_edit_reseller_price_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    prod_id = int(call.data.split("_")[2])
    await state.update_data(edit_reseller_prod_id=prod_id)
    await call.message.edit_text("💰 Enter the new <b>Reseller Price</b> in Rupees (₹) for this product:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.edit_reseller_price)

@dp.message(AdminStates.edit_reseller_price)
async def admin_save_reseller_price(m: Message, state: FSMContext):
    try:
        new_price = float(m.text)
        data = await state.get_data()
        prod_id = data['edit_reseller_prod_id']
        db_query("UPDATE products SET reseller_price=? WHERE id=?", (new_price, prod_id))
        await m.answer(f"✅ Reseller price updated to {fmt_curr(new_price)} for product ID {prod_id}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Invalid number. Please enter a valid price.")

@dp.callback_query(F.data == "admin_set_quick_reseller_discount")
async def admin_set_quick_reseller_discount(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    current = get_setting("quick_api_reseller_discount_percent", "20")
    await call.message.edit_text(
        f"🏷 Enter the <b>Quick-Add Reseller Discount %</b> "
        f"(Quick Add API Key mein reseller price automatically Price se itna % kam set hogi):\n"
        f"Current: {current}%",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_quick_reseller_discount)

@dp.message(AdminStates.wait_for_quick_reseller_discount)
async def admin_save_quick_reseller_discount(m: Message, state: FSMContext):
    try:
        pct = float(m.text)
        if pct < 0 or pct > 100:
            raise ValueError
        set_setting("quick_api_reseller_discount_percent", str(pct))
        await m.answer(f"✅ Quick-Add Reseller Discount ab {pct:g}% set ho gaya.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError):
        await m.answer("❌ 0 se 100 ke beech ek number bhejo (e.g. 20).")

@dp.callback_query(F.data == "admin_set_reseller_fee")
async def admin_set_reseller_fee(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("💰 Enter the new <b>Reseller Setup Fee</b> in Rupees (₹):\nCurrent: " + get_setting("reseller_setup_fee", "200.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_setup_fee)

@dp.message(AdminStates.wait_for_reseller_setup_fee)
async def admin_save_reseller_fee(m: Message, state: FSMContext):
    try:
        fee = float(m.text)
        set_setting("reseller_setup_fee", str(fee))
        await m.answer(f"✅ Reseller setup fee updated to {fmt_curr(fee)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_reseller_min")
async def admin_set_reseller_min(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("💳 Enter the new <b>Minimum Balance</b> required to become reseller (₹):\nCurrent: " + get_setting("reseller_min_balance", "500.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_min_balance)

@dp.message(AdminStates.wait_for_reseller_min_balance)
async def admin_save_reseller_min(m: Message, state: FSMContext):
    try:
        min_bal = float(m.text)
        set_setting("reseller_min_balance", str(min_bal))
        await m.answer(f"✅ Minimum reseller balance updated to {fmt_curr(min_bal)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except (ValueError, TypeError): await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_support_links")
async def admin_set_support_links(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Set Telegram Link", callback_data="admin_set_telegram", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="📱 Set WhatsApp Link", callback_data="admin_set_whatsapp", icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("📌 <b>Support Contact Links</b>\nSet the URLs for Telegram and WhatsApp support:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_telegram")
async def admin_set_telegram(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("✈️ Enter the Telegram contact URL (e.g., https://t.me/YOUR_SUPPORT):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_telegram)

@dp.message(AdminStates.wait_for_support_telegram)
async def save_telegram_link(m: Message, state: FSMContext):
    link = m.text.strip()
    set_setting("support_telegram", link)
    await m.answer("✅ Telegram support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_whatsapp")
async def admin_set_whatsapp(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("📱 Enter the WhatsApp contact URL (e.g., https://wa.me/1234567890):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_whatsapp)

@dp.message(AdminStates.wait_for_support_whatsapp)
async def save_whatsapp_link(m: Message, state: FSMContext):
    link = m.text.strip()
    set_setting("support_whatsapp", link)
    await m.answer("✅ WhatsApp support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_category_emojis")
async def admin_set_category_emojis(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for _, cat in get_shop_categories():
        current = get_setting(f"cat_emoji_{cat}", "Not set")
        # Category IDs keep callback_data safe even when an admin uses a long
        # or punctuation-heavy custom category name.
        category_id = db_query(
            "SELECT id FROM product_categories WHERE name=? COLLATE NOCASE",
            (cat,),
            fetchone=True,
            commit=False,
        )[0]
        label = cat if len(cat) <= 32 else cat[:29] + "..."
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{label} (ID: {current})", callback_data=f"set_cat_emoji_id_{category_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Set Category Emojis</b>\nChoose a category to set its custom emoji ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_cat_emoji_id_"))
async def admin_set_category_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    try:
        category_id = int(call.data.split("set_cat_emoji_id_", 1)[1])
    except (TypeError, ValueError):
        return await call.answer("Invalid category.", show_alert=True)
    row = db_query("SELECT name FROM product_categories WHERE id=?", (category_id,), fetchone=True, commit=False)
    if not row:
        return await call.answer("Category not found.", show_alert=True)
    category = str(row[0])
    await state.update_data(cat_emoji_category=category)
    await call.message.edit_text(f"🎨 Enter the emoji ID for <b>{category}</b>:\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_category_emoji)

@dp.message(AdminStates.wait_for_category_emoji)
async def save_category_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    category = data['cat_emoji_category']
    emoji_id = m.text.strip()
    if emoji_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"cat_emoji_{category}",))
        await m.answer(f"✅ Reset emoji for {category} to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not emoji_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"cat_emoji_{category}", emoji_id)
        await m.answer(f"✅ Emoji set for {category} successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_panel_emojis")
async def admin_set_panel_emojis(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    panels = db_query("SELECT DISTINCT panel_name FROM products WHERE panel_name != '' ORDER BY panel_name", fetchall=True)
    if not panels:
        await call.message.edit_text("No panel names found in products.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in panels:
        panel = p[0]
        current = get_setting(f"panel_emoji_{panel}", "Not set")
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{panel} (ID: {current})", callback_data=f"set_panel_emoji_{panel}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🖼 <b>Set Panel Emojis</b>\nChoose a panel name to set its custom emoji ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_panel_emoji_"))
async def admin_set_panel_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    panel_name = call.data.split("set_panel_emoji_", 1)[1]
    await state.update_data(panel_emoji_name=panel_name)
    await call.message.edit_text(f"🎨 Enter the emoji ID for panel <b>{panel_name}</b>:\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_panel_emoji_id)

@dp.message(AdminStates.wait_for_panel_emoji_id)
async def save_panel_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data['panel_emoji_name']
    emoji_id = m.text.strip()
    if emoji_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"panel_emoji_{panel_name}",))
        await m.answer(f"✅ Reset emoji for panel '{panel_name}'.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not emoji_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"panel_emoji_{panel_name}", emoji_id)
        await m.answer(f"✅ Emoji set for panel '{panel_name}'!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_welcome_voice")
async def admin_set_welcome_voice_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    kind, _ = _welcome_voice()
    if kind:
        current = "✅ Your recording"
    elif get_setting("start_welcome_voice", "").strip().lower() in ("none", "off"):
        current = "🔇 Off"
    else:
        current = f"🤖 Auto voice: {WELCOME_SPEECH_TEXT}" if gTTS else "❌ Not set"
    await call.message.edit_text(
        f"🔊 <b>Welcome Voice</b> (currently: {current})\n\n"
        "Send a voice note (mic recording) or an audio/mp3 file — it plays "
        "<b>once for every user</b>, on their first /start.\n\n"
        "If you set nothing, the bot speaks “Welcome to Nagesh Panel Shop” by itself.\n"
        "Send <code>none</code> to turn the voice off.\n"
        "Use /resetwelcomevoice to make everyone hear it once more.",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_welcome_voice)

@dp.message(AdminStates.wait_for_welcome_voice)
async def admin_save_welcome_voice(m: Message, state: FSMContext):
    if m.voice:
        set_setting("start_welcome_voice", f"voice:{m.voice.file_id}")
        await m.answer("✅ Welcome voice saved. Ab /start karke sunlo.", reply_markup=admin_kb(), parse_mode='HTML')
    elif m.audio:
        set_setting("start_welcome_voice", f"audio:{m.audio.file_id}")
        await m.answer("✅ Welcome audio saved. Ab /start karke sunlo.", reply_markup=admin_kb(), parse_mode='HTML')
    elif m.text and m.text.strip().lower() == "none":
        set_setting("start_welcome_voice", "None")
        await m.answer("✅ Welcome voice removed. /start ab chup rahega.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        await m.answer("❌ Voice note ya audio file bhejo, ya 'none' type karke hatao.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    await state.clear()

@dp.callback_query(F.data == "admin_set_panel_videos")
async def admin_set_panel_videos(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    panels = db_query("SELECT DISTINCT panel_name FROM products WHERE panel_name != '' ORDER BY panel_name", fetchall=True)
    if not panels:
        await call.message.edit_text("No panel names found in products.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in panels:
        panel = p[0]
        has_video = "✅ Set" if get_panel_video(panel) else "❌ Not set"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{panel} ({has_video})", callback_data=f"set_panel_video_{panel}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        "🎬 <b>Set Panel Gameplay Video</b>\n\n"
        "Choose a panel — the video you send will play above its plan list, "
        "so buyers see the gameplay before purchasing a key.",
        reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_panel_video_"))
async def admin_set_panel_video_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    panel_name = call.data.split("set_panel_video_", 1)[1]
    await state.update_data(panel_video_name=panel_name)
    await call.message.edit_text(
        f"🎬 Send the gameplay <b>video</b> for panel <b>{html.escape(panel_name)}</b>.\n\n"
        "Just forward or upload the video file here (not a link).\n"
        "Send <code>none</code> to remove the current video for this panel.",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_panel_video)

@dp.message(AdminStates.wait_for_panel_video)
async def save_panel_video(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data.get('panel_video_name')
    if not panel_name:
        await state.clear()
        return
    if m.video:
        set_setting(f"panel_video_{panel_name}", m.video.file_id)
        await m.answer(f"✅ Gameplay video set for panel '{html.escape(panel_name)}'!", reply_markup=admin_kb(), parse_mode='HTML')
    elif m.text and m.text.strip().lower() == "none":
        db_query("DELETE FROM settings WHERE key=?", (f"panel_video_{panel_name}",))
        await m.answer(f"✅ Removed the gameplay video for panel '{html.escape(panel_name)}'.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        await m.answer("❌ Please send an actual video file, or type 'none' to remove it.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    await state.clear()

@dp.callback_query(F.data == "admin_setup_zapupi")
async def setup_zapupi_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("⚙️ <b>ZAPUPI SECURITY DEPLOYMENT</b>\nInput master <b>API Key (zap_key)</b>:\n<i>(Type /cancel to abort sequence)</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_zapupi_api)

@dp.message(AdminStates.wait_for_zapupi_api)
async def zapupi_api(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear()
        return await m.answer("Sequence killed.", reply_markup=admin_kb(), parse_mode='HTML')
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('zapupi_api', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Keys synchronized with ZapUPI backbone.</b>", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_setup_binance")
async def setup_binance_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.message.edit_text("🪙 <b>CRYPTO NODE INIT: Step 1/3</b>\nInput Master <b>Binance API Key</b>:\n<i>(Type /cancel to halt protocol)</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_api)

@dp.message(AdminStates.wait_for_binance_api)
async def setup_binance_api(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear()
        return await m.answer("Sequence aborted.", reply_markup=admin_kb(), parse_mode='HTML')
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_api', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>CRYPTO NODE INIT: Step 2/3</b>\nNow inject the highly secure <b>Binance Secret Key</b>:", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_secret)

@dp.message(AdminStates.wait_for_binance_secret)
async def setup_binance_secret(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_secret', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>CRYPTO NODE INIT: Step 3/3</b>\nFinal variable: Set the public <b>USDT Deposit Address (TRC20/BEP20)</b>\nUsers will broadcast to this ledger:", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_address)

@dp.message(AdminStates.wait_for_binance_address)
async def setup_binance_address(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_address', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Blockchain node synchronized.</b> Crypto gateway is fully armed.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 22. BOOTSTRAPPING & MAIN
# ==============================================================================
async def main() -> None:
    logger.info("🏷 Running build: %s", BOT_CODE_VERSION)
    init_db()
    logger.info("Initializing DB structure...")
    migrate_categories()
    # Give untouched installations a premium first impression without
    # overwriting a design the admin has already saved.
    if get_setting("premium_button_style_v1", "0") != "1":
        if not get_setting("button_order_main", "").strip():
            _apply_style_preset("style_g")
        set_setting("premium_button_style_v1", "1")
    asyncio.create_task(auto_verify_task())
    logger.info("ZapUPI Auto-Verifier Daemon Running in Background.")
    logger.info("🚀 CORE SYSTEM IS FULLY OPERATIONAL...")
    api_runner = None
    try:
        api_runner = await start_reseller_api_server()
        await dp.start_polling(bot)
    except Exception as err:
        logger.error(f"Critical System Failure in Polling: {err}")
    finally:
        if api_runner:
            await api_runner.cleanup()
        await bot.session.close()

# ==============================================================================
# EXTERNAL KEY GENERATION API
# ==============================================================================
def normalize_api_duration(duration: str) -> str:
    """Normalize the package name into the duration format expected by the API."""
    value = str(duration or "").strip()
    if not value:
        return value

    # Normalize whitespace
    value = re.sub(r"\s+", " ", value)
    
    # Define exact mapping for your API format
    # API expects: "X Hours" or "X DaYs" (with capital D and Y)
    duration_map = {
        # Hours - API expects "X Hours"
        "1 hour": "1 Hours",
        "1 hours": "1 Hours",
        "1hr": "1 Hours",
        "1h": "1 Hours",
        "2 hour": "2 Hours",
        "2 hours": "2 Hours",
        "2hr": "2 Hours",
        "3 hour": "3 Hours",
        "3 hours": "3 Hours",
        "3hr": "3 Hours",
        "6 hour": "6 Hours",
        "6 hours": "6 Hours",
        "6hr": "6 Hours",
        "12 hour": "12 Hours",
        "12 hours": "12 Hours",
        "12hr": "12 Hours",
        
        # Days - API expects "X DaYs" (capital D and Y)
        "1 day": "1 DaYs",
        "1 days": "1 DaYs",
        "1d": "1 DaYs",
        "2 day": "2 DaYs",
        "2 days": "2 DaYs",
        "2d": "2 DaYs",
        "3 day": "3 DaYs",
        "3 days": "3 DaYs",
        "3d": "3 DaYs",
        "5 day": "5 DaYs",
        "5 days": "5 DaYs",
        "5d": "5 DaYs",
        "7 day": "7 DaYs",
        "7 days": "7 DaYs",
        "7d": "7 DaYs",
    }
    
    # Check exact matches first (case insensitive)
    lower_val = value.lower()
    for pattern, result in duration_map.items():
        if lower_val == pattern.lower():
            return result
    
    # Check if it's already in correct format
    if value in ["1 Hours", "2 Hours", "3 Hours", "6 Hours", "12 Hours", 
                 "1 DaYs", "2 DaYs", "3 DaYs", "5 DaYs", "7 DaYs"]:
        return value
    
    # Try to extract number and determine unit
    match = re.match(r"(\d+)\s*(hour|hours|hr|hrs|h|day|days|d)", value, re.IGNORECASE)
    if match:
        number = match.group(1)
        unit = match.group(2).lower()
        if unit in ["hour", "hours", "hr", "hrs", "h"]:
            return f"{number} Hours"
        if unit in ["day", "days", "d"]:
            return f"{number} DaYs"
    
    # If it's just a number, treat as hours
    if value.isdigit():
        return f"{value} Hours"
    
    # Return as-is if nothing matches (fallback)
    return value

DEFAULT_EXTERNAL_API_URL = "https://adminpanels.shop/api/reseller_v1.php"

def _panel_setting_key(base: str, panel_name: str) -> str:
    """Build the settings-table key for a panel-scoped credential.

    Empty panel_name means "the shop-wide default", which keeps using the
    original unscoped keys (external_api_url / external_api_key /
    external_master_key) so upgrades from a single-panel setup don't lose
    their existing configuration.
    """
    panel_name = (panel_name or "").strip()
    if not panel_name:
        return base
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", panel_name).strip("_").lower()
    return f"{base}_panel_{slug}"

def get_panel_api_config(panel_name: str = "") -> Tuple[str, str, str]:
    """Return (url, api_key, master_key) for one upstream panel.

    Each panel (e.g. "ABCD", "CTB") can have its own upstream credentials so
    that a purchase against panel ABCD debits ABCD's own account there,
    while a purchase against panel CTB debits CTB's own account there. If a
    panel has never been configured on its own, it falls back to the
    shop-wide default credentials so nothing silently breaks.
    """
    panel_name = (panel_name or "").strip()
    url = get_setting(_panel_setting_key("external_api_url", panel_name), "").strip()
    api_key = get_setting(_panel_setting_key("external_api_key", panel_name), "").strip()
    master_key = get_setting(_panel_setting_key("external_master_key", panel_name), "").strip()
    if panel_name and not url and not api_key:
        url = get_setting("external_api_url", DEFAULT_EXTERNAL_API_URL).strip()
        api_key = get_setting("external_api_key", "").strip()
        master_key = get_setting("external_master_key", "").strip()
    if not panel_name and not url:
        url = DEFAULT_EXTERNAL_API_URL
    return url, api_key, master_key

def list_configured_api_panels() -> List[str]:
    """Distinct panel names that have at least one API-enabled product."""
    rows = db_query(
        "SELECT DISTINCT panel_name FROM products WHERE panel_name != '' ORDER BY panel_name COLLATE NOCASE",
        fetchall=True, commit=False,
    ) or []
    return [str(r[0]) for r in rows if str(r[0] or "").strip()]

async def fetch_external_key(product_id: str, duration: str, android_id: str = "", panel_name: str = "") -> dict:
    """Generate one key. This is a non-idempotent BUY endpoint, so never retry automatically."""
    url, api_key, master_key = get_panel_api_config(panel_name)
    if not url or not api_key:
        label = f" for panel '{panel_name}'" if panel_name else ""
        return {"status": "error", "msg": f"External API configuration is incomplete{label}"}
    product_id = str(product_id or "").strip(); duration = str(duration or "").strip()
    if not product_id:
        return {"status": "error", "msg": "External API product ID is empty"}

    # keypanel.shop's reseller_gateway.php has its own strict, documented
    # format (confirmed against their official PHP sample): ONLY
    # action/variant_id/quantity in the body, and ONLY the x-master-key
    # header. Sending the extra fields/headers the generic branch below adds
    # (api_key, product_id, pid, duration, X-API-Key, Authorization, a
    # spoofed User-Agent/Referer/Origin) gets the request blocked by their
    # security layer with an empty-body HTTP 403 before it ever reaches
    # their script. Detect that panel by its endpoint path and match its
    # format exactly instead of guessing.
    is_keypanel_gateway = "reseller_gateway.php" in url.lower()
    if is_keypanel_gateway:
        data = {
            "action": "buy",
            "variant_id": product_id,
            "quantity": "1",
        }
        # keypanel.shop has a single key (labelled "Master Key" here, shown
        # as "rsk_..." on their "My API" page) - no separate API key concept.
        master_header_value = master_key or api_key
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            # aiohttp's default User-Agent identifies itself as
            # "Python/x.y aiohttp/z", which some security layers flag as a
            # bot signature and block with a "Security Verification" page
            # before the request ever reaches keypanel's script. Their own
            # documented example uses plain PHP-curl (no distinctive
            # signature), so present as an ordinary curl client instead.
            "User-Agent": "curl/8.4.0",
            "Accept": "*/*",
            "x-master-key": master_header_value,
        }
    else:
        if not duration:
            return {"status": "error", "msg": "External API duration is empty"}
        data = {
            "api_key": api_key, "key": api_key,
            "action": "buy", "type": "buy",
            "product_id": product_id, "pid": product_id,
            "variant_id": product_id, "quantity": "1",
            "duration": duration,
        }
        if android_id: data["android_id"] = str(android_id).strip()
        # Different reseller panels expect the key in different places (a form
        # field, a custom header, or a Bearer token). We are not guessing the
        # duplicate form fields (api_key/key, action/type, product_id/pid) or
        # sending it three ways at once (form field + X-API-Key + Authorization)
        # is harmless for panels that ignore the extras, and fixes the common
        # case where a 401 happens simply because the key was expected in a
        # header instead of the POST body.
        parsed_url = urllib.parse.urlsplit(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
            # Some panels sit behind a basic bot/security check that blocks the
            # default aiohttp User-Agent. Look like an ordinary browser request
            # coming from their own site, which is enough for a simple check
            # (it will NOT get past a real Cloudflare JS challenge/CAPTCHA).
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": origin + "/",
            "Origin": origin,
        }
        master_header_value = master_key or api_key
        headers["x-master-key"] = master_header_value
        headers["X-Master-Key"] = master_header_value
    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=data, headers=headers, allow_redirects=True) as resp:
                raw = await resp.text()
                logger.info("External API BUY HTTP=%s body=%s", resp.status, raw[:1000])
                if resp.status != 200:
                    snippet = raw[:500]
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
                    if "<!doctype" in raw.lower() or "<html" in raw.lower():
                        # This is a full HTML page, not a JSON API error. Pull out
                        # the page <title> (if any) instead of showing raw CSP/meta
                        # tag junk, which is useless for diagnosing the real cause.
                        snippet = (title_match.group(1).strip() if title_match else "") or \
                                   "the panel returned an HTML page instead of JSON (this URL is probably not the real API endpoint, or the key/format this panel expects is different)"
                    return {"status": "error", "msg": f"HTTP {resp.status}: {snippet}"}
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    return {"status": "error", "msg": f"API returned invalid JSON: {raw[:300]}"}
                if not isinstance(result, dict):
                    return {"status": "error", "msg": "API returned invalid response"}

                # Different reseller_v1 builds use slightly different field
                # names for the generated license. Normalize them here.
                status = result.get("status")
                if status is True or status == 1:
                    result["status"] = "success"
                if str(status).lower() in {"ok", "true", "1", "success", "successful"}:
                    result["status"] = "success"
                # keypanel.shop-style panels reply with {"ok": true/false} and
                # no "status" field at all - map that to our status field too.
                if "ok" in result and not result.get("status"):
                    result["status"] = "success" if result.get("ok") else "error"
                if not result.get("key"):
                    for field in ("license_key", "license", "key_text", "generated_key", "token"):
                        if result.get(field):
                            result["key"] = result[field]
                            break
                if not result.get("msg"):
                    for field in ("message", "error", "detail"):
                        if result.get(field):
                            result["msg"] = str(result[field])
                            break
                return result
    except asyncio.TimeoutError:
        return {"status": "error", "msg": "API timed out. No automatic retry was made to avoid duplicate key generation."}
    except aiohttp.ClientError as exc:
        return {"status": "error", "msg": f"API connection failed: {exc}"}
    except Exception as exc:
        logger.exception("API unexpected error")
        return {"status": "error", "msg": f"API unexpected error: {exc}"}

def _encode_panel_token(panel_name: str) -> str:
    return base64.urlsafe_b64encode((panel_name or "").encode("utf-8")).decode("ascii").rstrip("=")

def _decode_panel_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    padded = token + "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return ""

@dp.callback_query(F.data == "admin_setup_external_api")
async def admin_setup_external_api(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    panels = list_configured_api_panels()
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    def_url, def_key, def_master = get_panel_api_config("")
    kb.inline_keyboard.append([InlineKeyboardButton(
        text=f"🌐 Default / Fallback — {'✅' if def_key else '⚠️ not set'}",
        callback_data="admin_extp_", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success" if def_key else "primary",
    )])
    for pn in panels:
        p_url, p_key, p_master = get_setting(_panel_setting_key("external_api_url", pn), ""), get_setting(_panel_setting_key("external_api_key", pn), ""), get_setting(_panel_setting_key("external_master_key", pn), "")
        own = bool(p_url or p_key)
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=f"🔌 {pn} — {'✅ own API' if own else '↪ uses default'}",
            callback_data=f"admin_extp_{_encode_panel_token(pn)}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success" if own else "primary",
        )])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    text = ("🔗 <b>External Key API Configuration</b>\n\n"
            "Har panel (jaise ABCD, CTB) ka apna alag upstream API URL/Key/Master "
            "set kar sakte ho — jis panel se koi key kharidega, usi panel ke "
            "apne account se katega. Jis panel ke liye kuch set nahi hai, wo "
            "neeche wale <b>Default / Fallback</b> API ko use karega.\n\n"
            "👇 Configure karne ke liye panel choose karo:")
    await call.message.edit_text(text, reply_markup=apply_button_theme(kb), parse_mode='HTML')

@dp.callback_query(F.data.startswith("admin_extp_"))
async def admin_ext_panel_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    panel_name = _decode_panel_token(call.data[len("admin_extp_"):])
    await state.update_data(ext_panel=panel_name)
    url = get_setting(_panel_setting_key("external_api_url", panel_name), "")
    key = get_setting(_panel_setting_key("external_api_key", panel_name), "")
    master = get_setting(_panel_setting_key("external_master_key", panel_name), "")
    label = html.escape(panel_name) if panel_name else "Default / Fallback"
    token = _encode_panel_token(panel_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set API URL", callback_data=f"admin_set_ext_url_{token}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set API Key", callback_data=f"admin_set_ext_key_{token}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set Master Key", callback_data=f"admin_set_ext_master_{token}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
    ])
    if panel_name and (url or key or master):
        kb.inline_keyboard.append([InlineKeyboardButton(text="🗑 Clear (use default instead)", callback_data=f"admin_clr_ext_{token}", style="danger")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_setup_external_api", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    text = (f"🔗 <b>{label}</b>\n\n"
            f"URL: <code>{html.escape(url) if url else 'Not set'}</code>\n"
            f"API Key: <code>{_mask_secret(key)}</code>\n"
            f"Master Key: <code>{_mask_secret(master)}</code>\n\n"
            + ("Purchases from this panel use this API." if panel_name
               else "Used by any panel that has not set its own API."))
    await call.message.edit_text(text, reply_markup=apply_button_theme(kb), parse_mode='HTML')

@dp.callback_query(F.data.startswith("admin_clr_ext_"))
async def admin_clear_ext_panel(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    panel_name = _decode_panel_token(call.data[len("admin_clr_ext_"):])
    if panel_name:
        for base in ("external_api_url", "external_api_key", "external_master_key"):
            db_query("DELETE FROM settings WHERE key=?", (_panel_setting_key(base, panel_name),))
        await call.answer("✅ Cleared. This panel will use the default API now.", show_alert=True)
    await admin_ext_panel_menu(call, state)

@dp.callback_query(F.data.startswith("admin_set_ext_url_"))
async def set_ext_url(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    panel_name = _decode_panel_token(call.data[len("admin_set_ext_url_"):])
    await state.update_data(ext_panel=panel_name)
    label = html.escape(panel_name) if panel_name else "Default / Fallback"
    await call.message.edit_text(f"Enter External API URL for <b>{label}</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_url)

@dp.message(AdminStates.wait_for_ext_url)
async def save_ext_url(m: Message, state: FSMContext):
    value = m.text.strip()
    if not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://")
    data = await state.get_data()
    panel_name = data.get('ext_panel', '')
    set_setting(_panel_setting_key("external_api_url", panel_name), value)
    await m.answer("✅ External API URL saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("admin_set_ext_key_"))
async def set_ext_key(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    panel_name = _decode_panel_token(call.data[len("admin_set_ext_key_"):])
    await state.update_data(ext_panel=panel_name)
    label = html.escape(panel_name) if panel_name else "Default / Fallback"
    await call.message.edit_text(f"Enter External API Key for <b>{label}</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_key)

@dp.message(AdminStates.wait_for_ext_key)
async def save_ext_key(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data.get('ext_panel', '')
    set_setting(_panel_setting_key("external_api_key", panel_name), m.text.strip())
    await m.answer("✅ External API Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("admin_set_ext_master_"))
async def set_ext_master(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    panel_name = _decode_panel_token(call.data[len("admin_set_ext_master_"):])
    await state.update_data(ext_panel=panel_name)
    label = html.escape(panel_name) if panel_name else "Default / Fallback"
    await call.message.edit_text(f"Enter External API Master Key for <b>{label}</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_master)

@dp.message(AdminStates.wait_for_ext_master)
async def save_ext_master(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data.get('ext_panel', '')
    set_setting(_panel_setting_key("external_master_key", panel_name), m.text.strip())
    await m.answer("✅ External API Master Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 22b. MANUAL "GET KEY VIA API" — pull one key straight from an upstream panel
#      (ABCD, CTB, etc.) without a customer purchase. Useful for testing or
#      handing a key out manually.
# ==============================================================================
@dp.callback_query(F.data == "admin_manual_get_key")
async def admin_manual_get_key(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    panels = list_configured_api_panels()
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    kb.inline_keyboard.append([InlineKeyboardButton(
        text="🌐 Default / Fallback", callback_data="admin_mgk_p_", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary",
    )])
    for pn in panels:
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=f"🔌 {pn}", callback_data=f"admin_mgk_p_{_encode_panel_token(pn)}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success",
        )])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        "🎯 <b>Get Key via API</b>\n\n"
        "Ye seedha upstream panel se ek key nikaal ke deta hai — koi customer, "
        "payment ya stock involved nahi hai. Testing ya kisi ko manually key dene ke kaam aata hai.\n\n"
        "👇 Panel choose karo:",
        reply_markup=apply_button_theme(kb), parse_mode='HTML',
    )

@dp.callback_query(F.data.startswith("admin_mgk_p_"))
async def admin_mgk_panel_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    panel_name = _decode_panel_token(call.data[len("admin_mgk_p_"):])
    await state.update_data(mgk_panel=panel_name)
    label = html.escape(panel_name) if panel_name else "Default / Fallback"
    await call.message.edit_text(f"🆔 <b>{label}</b>\n\nEnter the <b>PID</b> (external product id) for this key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_manual_key_pid)

@dp.message(AdminStates.wait_for_manual_key_pid)
async def admin_mgk_pid(m: Message, state: FSMContext):
    pid = m.text.strip()
    if not pid:
        return await m.answer("❌ PID cannot be empty.")
    await state.update_data(mgk_pid=pid)
    await m.answer("⏱ Enter the <b>duration</b> exactly as the panel expects it (e.g. <code>1 Day</code>, <code>3 Hours</code>):", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_manual_key_duration)

@dp.message(AdminStates.wait_for_manual_key_duration)
async def admin_mgk_duration(m: Message, state: FSMContext):
    duration = normalize_api_duration(m.text.strip())
    if not duration:
        return await m.answer("❌ Duration cannot be empty.")
    await state.update_data(mgk_duration=duration)
    await m.answer("📱 Enter <b>Android ID</b> if this panel needs one, or send <code>none</code>:", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_manual_key_android)

@dp.message(AdminStates.wait_for_manual_key_android)
async def admin_mgk_android(m: Message, state: FSMContext):
    android_id = "" if m.text.strip().lower() == "none" else m.text.strip()
    data = await state.get_data()
    panel_name = data.get('mgk_panel', '')
    pid = data.get('mgk_pid', '')
    duration = data.get('mgk_duration', '')
    wait_msg = await m.answer("⏳ Calling upstream API...", parse_mode='HTML')
    result = await fetch_external_key(pid, duration, android_id, panel_name)
    await state.clear()
    if str(result.get("status", "")).lower() == "success" and result.get("key"):
        key = result.get("key")
        if isinstance(key, list):
            key = "\n".join(str(x) for x in key)
        await wait_msg.edit_text(
            f"✅ <b>Key generated</b>\n\n"
            f"Panel: <code>{html.escape(panel_name or 'Default/Fallback')}</code>\n"
            f"PID: <code>{html.escape(pid)}</code>\n"
            f"Duration: <code>{html.escape(duration)}</code>\n\n"
            f"🔑 <code>{html.escape(str(key))}</code>",
            reply_markup=admin_kb(), parse_mode='HTML',
        )
    else:
        await wait_msg.edit_text(
            f"❌ <b>Failed:</b> {html.escape(str(result.get('msg') or 'Unknown error'))}",
            reply_markup=admin_kb(), parse_mode='HTML',
        )

# ==============================================================================
# 23. OPTIONAL AI SUPPORT & MULTI-API MANAGER
# ==============================================================================
def _mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return "Not set"
    if len(value) <= 8:
        return "Configured"
    return f"{value[:4]}••••{value[-4:]}"


def _ai_api_manager_markup(rows, active_id: int = 0) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text="➕ Add New AI API",
            callback_data="admin_ai_api_add",
            icon_custom_emoji_id=get_emoji_icon("info_icon"),
            style="success"
        )]
    ]
    for api_id, name, base_url, model, is_active in rows:
        prefix = "✅" if int(api_id) == int(active_id or 0) else "⚪"
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} {str(name)[:28]}",
                callback_data=f"admin_ai_use_{api_id}",
                icon_custom_emoji_id=get_emoji_icon("check_icon"),
                style="primary"
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"admin_ai_delete_{api_id}",
                icon_custom_emoji_id=get_emoji_icon("support"),
                style="danger"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="🔙 Back to Admin",
            callback_data="admin_panel_back",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _ai_api_rows():
    return db_query(
        "SELECT id, name, base_url, model, is_active FROM ai_api_configs ORDER BY id",
        fetchall=True,
        commit=False,
    ) or []


async def _show_ai_api_manager(target: Message, edit: bool = True) -> None:
    rows = _ai_api_rows()
    active_id = int(get_setting("ai_active_api_id", "0") or 0)
    if rows:
        lines = ["🔗 <b>Multi API Manager</b>", "", "Select an API to make it active:"]
        for api_id, name, base_url, model, is_active in rows:
            marker = "✅ ACTIVE" if int(api_id) == active_id else "⚪ available"
            lines.append(f"{marker} — <b>{html.escape(str(name))}</b> · <code>{html.escape(str(model))}</code>")
        text = "\n".join(lines)
    else:
        text = (
            "🔗 <b>Multi API Manager</b>\n\n"
            "No AI API is configured yet. Add an OpenAI-compatible API to enable AI support."
        )
    markup = _ai_api_manager_markup(rows, active_id)
    if edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


async def _start_ai_api_wizard(message_or_call, state: FSMContext) -> None:
    await state.clear()
    message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
    await message.edit_text(
        "🤖 <b>Add AI API — Step 1/4</b>\n\n"
        "Send a short name, for example: <code>OpenAI Main</code>\n"
        "Type /cancel to stop.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.ai_add_name)


@dp.callback_query(F.data == "admin_multi_api_manager")
async def admin_multi_api_manager(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    await _show_ai_api_manager(call.message)


@dp.callback_query(F.data == "admin_ai_api_add")
async def admin_ai_api_add(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await _start_ai_api_wizard(call, state)


@dp.callback_query(F.data == "admin_ai_api_paste")
async def admin_ai_api_paste(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    active_id = int(get_setting("ai_active_api_id", "0") or 0)
    active = db_query(
        "SELECT name FROM ai_api_configs WHERE id=?",
        (active_id,),
        fetchone=True,
        commit=False,
    )
    if active:
        await call.message.edit_text(
            f"🤖 <b>AI API Paste</b>\n\n"
            f"Active API: <b>{html.escape(str(active[0]))}</b>\n"
            "Send the new API key to replace the current key.\n"
            "The key will be masked in the admin panel. Type /cancel to stop.",
            reply_markup=admin_back_kb(),
            parse_mode="HTML",
        )
        await state.update_data(ai_api_id=active_id)
        await state.set_state(AdminStates.ai_update_key)
        return
    await _start_ai_api_wizard(call, state)


@dp.message(AdminStates.ai_add_name)
async def ai_add_name(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    name = m.text.strip()
    if not 2 <= len(name) <= 50:
        return await m.answer("❌ Name must be between 2 and 50 characters.")
    await state.update_data(ai_name=name)
    await m.answer(
        "🤖 <b>Step 2/4</b>\nSend the API base URL.\n"
        "Example: <code>https://api.openai.com/v1</code>",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.ai_add_url)


@dp.message(AdminStates.ai_add_url)
async def ai_add_url(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    value = m.text.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://")
    if value.endswith("/chat/completions"):
        value = value[:-len("/chat/completions")]
    await state.update_data(ai_url=value)
    await m.answer(
        "🤖 <b>Step 3/4</b>\nSend the model name.\n"
        "Example: <code>gpt-4o-mini</code>",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.ai_add_model)


@dp.message(AdminStates.ai_add_model)
async def ai_add_model(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    model = m.text.strip()
    if not 1 <= len(model) <= 100:
        return await m.answer("❌ Enter a valid model name.")
    await state.update_data(ai_model=model)
    await m.answer(
        "🤖 <b>Step 4/4</b>\nSend the API key.\n"
        "It will not be shown back in full. Type /cancel to stop.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.ai_add_key)


@dp.message(AdminStates.ai_add_key)
async def ai_add_key(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    api_key = m.text.strip()
    if len(api_key) < 8:
        return await m.answer("❌ The API key looks too short. Send a valid key or /cancel.")
    data = await state.get_data()
    try:
        db_query(
            "INSERT INTO ai_api_configs (name, base_url, model, api_key, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (
                data["ai_name"],
                data["ai_url"],
                data["ai_model"],
                api_key,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        new_row = db_query(
            "SELECT id FROM ai_api_configs WHERE name=?",
            (data["ai_name"],),
            fetchone=True,
            commit=False,
        )
        if new_row:
            set_setting("ai_active_api_id", str(new_row[0]))
        set_setting("ai_support_status", "OFF")
    except sqlite3.IntegrityError:
        await state.clear()
        return await m.answer("❌ An API with this name already exists.", reply_markup=admin_kb())
    await state.clear()
    await m.answer(
        f"✅ AI API added and selected.\nKey: <code>{html.escape(_mask_secret(api_key))}</code>",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@dp.message(AdminStates.ai_update_key)
async def ai_update_key(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    api_key = m.text.strip()
    if len(api_key) < 8:
        return await m.answer("❌ The API key looks too short. Send a valid key or /cancel.")
    data = await state.get_data()
    updated = db_update_count(
        "UPDATE ai_api_configs SET api_key=? WHERE id=?",
        (api_key, int(data.get("ai_api_id", 0))),
    )
    await state.clear()
    if not updated:
        return await m.answer("❌ Active AI API was not found.", reply_markup=admin_kb())
    await m.answer(
        f"✅ AI API key updated.\nKey: <code>{html.escape(_mask_secret(api_key))}</code>",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_ai_use_"))
async def admin_ai_use(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        api_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, TypeError):
        return await call.answer("Invalid API.", show_alert=True)
    row = db_query("SELECT name FROM ai_api_configs WHERE id=?", (api_id,), fetchone=True, commit=False)
    if not row:
        return await call.answer("API not found.", show_alert=True)
    set_setting("ai_active_api_id", str(api_id))
    await call.answer(f"{row[0]} is now active.")
    await _show_ai_api_manager(call.message)


@dp.callback_query(F.data.startswith("admin_ai_delete_") & ~F.data.startswith("admin_ai_delete_confirm_"))
async def admin_ai_delete_start(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        api_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, TypeError):
        return await call.answer("Invalid API.", show_alert=True)
    row = db_query("SELECT name FROM ai_api_configs WHERE id=?", (api_id,), fetchone=True, commit=False)
    if not row:
        return await call.answer("API not found.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Delete API", callback_data=f"admin_ai_delete_confirm_{api_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 Cancel", callback_data="admin_multi_api_manager", style="primary")],
    ])
    await call.message.edit_text(
        f"⚠️ Delete AI API <b>{html.escape(str(row[0]))}</b>?\n"
        "Existing bot features will not be affected, but AI support cannot use this API.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_ai_delete_confirm_"))
async def admin_ai_delete_confirm(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    try:
        api_id = int(call.data.rsplit("_", 1)[1])
    except (ValueError, TypeError):
        return await call.answer("Invalid API.", show_alert=True)
    db_query("DELETE FROM ai_api_configs WHERE id=?", (api_id,))
    if int(get_setting("ai_active_api_id", "0") or 0) == api_id:
        next_row = db_query("SELECT id FROM ai_api_configs ORDER BY id LIMIT 1", fetchone=True, commit=False)
        set_setting("ai_active_api_id", str(next_row[0]) if next_row else "")
        if not next_row:
            set_setting("ai_support_status", "OFF")
    await call.answer("AI API deleted.")
    await _show_ai_api_manager(call.message)


@dp.callback_query(F.data == "admin_ai_support_setup")
async def admin_ai_support_setup(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    status = get_setting("ai_support_status", "OFF")
    active_id = int(get_setting("ai_active_api_id", "0") or 0)
    api = db_query(
        "SELECT name, model, api_key FROM ai_api_configs WHERE id=?",
        (active_id,),
        fetchone=True,
        commit=False,
    )
    active_text = (
        f"<b>{html.escape(str(api[0]))}</b> · <code>{html.escape(str(api[1]))}</code>\n"
        f"Key: <code>{html.escape(_mask_secret(api[2]))}</code>"
        if api else "Not configured"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"AI Support: {'ON 🟢' if status == 'ON' else 'OFF 🔴'}",
            callback_data="admin_ai_toggle",
            icon_custom_emoji_id=get_emoji_icon("support"),
            style="success" if status == "ON" else "danger",
        )],
        [InlineKeyboardButton(text="✏️ Edit System Prompt", callback_data="admin_ai_edit_prompt", style="primary")],
        [InlineKeyboardButton(text="🔗 Manage APIs", callback_data="admin_multi_api_manager", style="primary")],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await call.message.edit_text(
        f"💬 <b>AI Support Setup</b>\n\n"
        f"Status: <b>{status}</b>\nActive API: {active_text}\n\n"
        "When enabled, users see an AI Support button inside Support.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_ai_toggle")
async def admin_ai_toggle(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    current = get_setting("ai_support_status", "OFF")
    if current != "ON" and not db_query(
        "SELECT id FROM ai_api_configs WHERE id=? AND api_key != ''",
        (int(get_setting("ai_active_api_id", "0") or 0),),
        fetchone=True,
        commit=False,
    ):
        return await call.answer("Add an AI API first.", show_alert=True)
    set_setting("ai_support_status", "OFF" if current == "ON" else "ON")
    await admin_ai_support_setup(call)


@dp.callback_query(F.data == "admin_ai_edit_prompt")
async def admin_ai_edit_prompt(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "✏️ <b>Edit AI System Prompt</b>\n\n"
        "Send the instruction the AI should follow. Type /cancel to keep the current prompt.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.ai_edit_prompt)


@dp.message(AdminStates.ai_edit_prompt)
async def save_ai_prompt(m: Message, state: FSMContext):
    if m.text.strip() == "/cancel":
        await state.clear()
        return await m.answer("Cancelled.", reply_markup=admin_kb())
    prompt = m.text.strip()
    if not 10 <= len(prompt) <= 2000:
        return await m.answer("❌ Prompt must be between 10 and 2000 characters.")
    set_setting("ai_system_prompt", prompt)
    await state.clear()
    await m.answer("✅ AI system prompt updated.", reply_markup=admin_kb())


async def _get_ai_support_answer(user_text: str) -> str:
    # 1st: try the explicitly selected active API
    active_id = int(get_setting("ai_active_api_id", "0") or 0)
    row = None
    if active_id:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs WHERE id=?",
            (active_id,),
            fetchone=True,
            commit=False,
        )
    # 2nd fallback: any entry with is_active=1
    if not row or not row[2]:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs WHERE is_active=1 ORDER BY id DESC LIMIT 1",
            fetchone=True,
            commit=False,
        )
    # 3rd fallback: any entry at all
    if not row or not row[2]:
        row = db_query(
            "SELECT base_url, model, api_key FROM ai_api_configs ORDER BY id DESC LIMIT 1",
            fetchone=True,
            commit=False,
        )
    if not row or not row[2]:
        return "AI support is not configured yet. Please open a support ticket."
    base_url, model, api_key = row
    endpoint = str(base_url).rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": get_setting("ai_system_prompt", "You are a helpful support assistant.")},
            {"role": "user", "content": user_text[:3000]},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    logger.warning("AI API returned HTTP %s: %s", response.status, body)
                    return "AI support is temporarily unavailable. Please open a support ticket."
        answer = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return answer[:4000] if answer else "I could not generate a reply. Please open a support ticket."
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.warning("AI API connection/timeout error")
        return "AI support is temporarily unavailable. Please try again or open a support ticket."
    except Exception:
        logger.exception("AI support request failed")
        return "AI support is temporarily unavailable. Please open a support ticket."


@dp.callback_query(F.data == "ai_support_start")
async def ai_support_start(call: CallbackQuery, state: FSMContext):
    if get_setting("ai_support_status", "OFF") != "ON":
        return await call.answer("AI support is currently OFF.", show_alert=True)
    await state.set_state(UserStates.ai_support_chat)
    await call.message.edit_text(
        "🤖 <b>AI Support</b>\n\n"
        "Apna sawal bhejiye. Bot jawab dega.\n"
        "Band karne ke liye /cancel bhejein.",
        reply_markup=back_kb("menu_support"),
        parse_mode="HTML",
    )


@dp.message(UserStates.ai_support_chat)
async def process_ai_support_message(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Please send your question as text.")
    if m.text.strip().lower() == "/cancel":
        await state.clear()
        return await m.answer("AI support closed.", reply_markup=main_menu_kb(m.from_user.id))
    answer = await _get_ai_support_answer(m.text.strip())
    await m.answer(answer, reply_markup=back_kb("menu_support"), parse_mode=None)


@dp.callback_query(F.data == "admin_setup_fampay")
async def setup_fampay_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    current = fampay_upi_id() or "Not set"
    await call.message.edit_text(
        "💳 <b>FamPay Auto-Verify Setup</b>\n\n"
        "Send the receiver UPI ID first. Next, send the FamGateway API key "
        "for automatic payment verification.\n\n"
        f"<b>Current:</b> <code>{html.escape(current)}</code>\n"
        "Type /cancel to stop.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.update_data(fampay_full_setup=True)
    await state.set_state(AdminStates.wait_for_payment_upi)

# ==============================================================================
# 24. PUBLIC RESELLER API + BUTTON DESIGN CONTROLS
# ==============================================================================
def _api_key_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _public_api_url() -> str:
    configured = get_setting("reseller_api_public_url", "").strip()
    if configured:
        return configured.rstrip("/")
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


def _api_min_price() -> float:
    """Lowest reseller price the API will sell at (admin-controlled from /admin)."""
    try:
        value = float(get_setting("reseller_api_min_price", "1.0"))
    except (TypeError, ValueError):
        value = 1.0
    return value if value > 0 else 0.01


def _api_port() -> int:
    """Port priority: /admin setting > RESELLER_API_PORT env > host's PORT env > 8099."""
    for raw in (get_setting("reseller_api_port", ""), os.getenv("RESELLER_API_PORT", ""), os.getenv("PORT", "")):
        try:
            port = int(str(raw).strip())
            if 1 <= port <= 65535:
                return port
        except (TypeError, ValueError):
            continue
    return 8099


def _api_json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False))


def _extract_api_key(request: web.Request) -> str:
    value = request.headers.get("X-API-Key", "").strip()
    if value:
        return value
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.query.get("api_key", "")).strip()


def _lookup_api_client(raw_key: str):
    """Return the active API client row for a raw key, or None."""
    raw_key = str(raw_key or "").strip()
    if not raw_key:
        return None
    row = db_query(
        """SELECT id, name, balance, is_active FROM reseller_api_clients
           WHERE api_key_hash=?""",
        (_api_key_hash(raw_key),),
        fetchone=True,
        commit=False,
    )
    if not row or not int(row[3]):
        return None
    db_query(
        "UPDATE reseller_api_clients SET last_used_at=? WHERE id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(row[0])),
    )
    return row


def _find_api_client(request: web.Request):
    return _lookup_api_client(_extract_api_key(request))


async def reseller_api_health(request: web.Request) -> web.Response:
    return _api_json({
        "ok": True,
        "service": "reseller-api",
        "enabled": get_setting("reseller_api_status", "OFF") == "ON",
    })


async def reseller_api_products(request: web.Request) -> web.Response:
    if get_setting("reseller_api_status", "OFF") != "ON":
        return _api_json({"success": False, "error": "Reseller API is disabled"}, 503)
    client = _find_api_client(request)
    if not client:
        return _api_json({"success": False, "error": "Valid X-API-Key or Bearer token required"}, 401)
    rows = db_query(
        """SELECT id, name, category, panel_name, validity, device_limit,
                  reseller_price, external_product_id, external_duration
           FROM products
           WHERE is_active=1 AND external_enabled=1 AND reseller_price >= ?
           ORDER BY category COLLATE NOCASE, panel_name COLLATE NOCASE, name COLLATE NOCASE""",
        (_api_min_price(),),
        fetchall=True,
        commit=False,
    ) or []
    return _api_json({
        "success": True,
        "balance": round(float(client[2] or 0), 2),
        "products": [
            {
                "id": int(row[0]),
                "name": row[1],
                "category": row[2],
                "panel_name": row[3] or "",
                "validity": row[4] or "",
                "device_limit": row[5] or "",
                "reseller_price": round(float(row[6] or 0), 2),
                "external_product_id": row[7] or "",
                "api_duration": row[8] or row[4] or "",
                "available": True,
            }
            for row in rows
        ],
    })


_API_PRODUCT_COLS = (
    "id, name, reseller_price, external_product_id, external_duration, "
    "requires_android_id, is_active, external_enabled, apk_link, panel_name"
)


async def _execute_api_purchase(client, product, quantity: int, android_id: str) -> Tuple[int, dict]:
    """Charge the client's API wallet and fetch keys. Returns (http_status, body)."""
    if not product or not int(product[6] or 0) or not int(product[7] or 0):
        return 404, {"success": False, "error": "API product not found or disabled"}
    if int(product[5] or 0) and not android_id:
        return 400, {"success": False, "error": "android_id is required for this product"}
    product_id = int(product[0])

    unit_price = max(0.0, float(product[2] or 0))
    if unit_price <= 0 or unit_price < _api_min_price():
        return 400, {"success": False, "error": "Price not set for this product"}
    reserved_amount = unit_price * quantity
    # Reserve the complete amount before making non-idempotent upstream BUY
    # calls. A failed upstream call is refunded below.
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE reseller_api_clients
               SET balance=balance-?
               WHERE id=? AND is_active=1 AND balance>=?""",
            (reserved_amount, int(client[0]), reserved_amount),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return 402, {
                "success": False,
                "error": "Insufficient API wallet balance",
                "required": round(reserved_amount, 2),
                "balance": round(float(client[2] or 0), 2),
            }
        conn.commit()
    finally:
        conn.close()

    keys = []
    upstream_errors = []
    for _ in range(quantity):
        result = await fetch_external_key(
            str(product[3] or ""),
            str(product[4] or ""),
            android_id,
            str(product[9] or ""),
        )
        if str(result.get("status", "")).lower() == "success" and result.get("key"):
            keys.append(str(result["key"]))
        else:
            upstream_errors.append(str(result.get("msg") or "External API did not return a key"))

    failed_count = quantity - len(keys)
    charged = unit_price * len(keys)
    refund = unit_price * failed_count
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        conn.execute(
            """UPDATE reseller_api_clients
               SET balance=balance+?,
                   total_spent=total_spent+?,
                   total_orders=total_orders+?
               WHERE id=?""",
            (refund, charged, len(keys), int(client[0])),
        )
        conn.commit()
    finally:
        conn.close()

    log_activity(
        0,
        "RESELLER_API_BUY",
        f"Client={client[1]} Product={product_id} Success={len(keys)} Failed={failed_count} Charged={charged:.2f}",
    )
    remaining = db_query(
        "SELECT balance FROM reseller_api_clients WHERE id=?",
        (int(client[0]),),
        fetchone=True,
        commit=False,
    )
    response = {
        "success": bool(keys),
        "product_id": product_id,
        "product_name": product[1],
        "quantity_requested": quantity,
        "quantity_delivered": len(keys),
        "charged": round(charged, 2),
        "refunded": round(refund, 2),
        "balance": round(float(remaining[0] or 0), 2) if remaining else 0.0,
        "keys": keys,
    }
    if upstream_errors:
        response["errors"] = upstream_errors
    if not keys:
        response["error"] = "No key was generated by the connected external API"
    if product[8]:
        response["apk_link"] = product[8]
    return (200 if keys else 502), response


async def reseller_api_buy(request: web.Request) -> web.Response:
    if get_setting("reseller_api_status", "OFF") != "ON":
        return _api_json({"success": False, "error": "Reseller API is disabled"}, 503)
    client = _find_api_client(request)
    if not client:
        return _api_json({"success": False, "error": "Valid X-API-Key or Bearer token required"}, 401)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _api_json({"success": False, "error": "Request body must be valid JSON"}, 400)
    try:
        product_id = int(payload.get("product_id"))
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError, AttributeError):
        return _api_json({"success": False, "error": "product_id and quantity must be numbers"}, 400)
    if quantity < 1 or quantity > 10:
        return _api_json({"success": False, "error": "quantity must be between 1 and 10"}, 400)

    product = db_query(
        f"SELECT {_API_PRODUCT_COLS} FROM products WHERE id=?",
        (product_id,),
        fetchone=True,
        commit=False,
    )
    android_id = str(payload.get("android_id", "")).strip()
    status, body = await _execute_api_purchase(client, product, quantity, android_id)
    return _api_json(body, status)


async def reseller_compat_buy(request: web.Request) -> web.Response:
    """Drop-in replacement for the upstream reseller_v1.php endpoint.

    Another copy of this bot can point its "External Key API" at
    https://<this-bot>/api/reseller_v1.php with its API key. It sends form
    fields api_key, action=buy, product_id, duration (+ optional android_id).
    product_id is matched against this bot's API products by their external
    product id + duration, or use "id:<number>" to pick a product by its local ID.
    Always answers HTTP 200 with {"status": "success"|"error", "msg": ..., "key": ...}.
    """
    def out(status: str, msg: str, **extra) -> web.Response:
        return _api_json({"status": status, "msg": msg, **extra})

    if get_setting("reseller_api_status", "OFF") != "ON":
        return out("error", "Reseller API is disabled")
    try:
        if "json" in (request.content_type or ""):
            fields = await request.json()
            if not isinstance(fields, dict):
                fields = {}
        else:
            fields = dict(await request.post())
    except Exception:
        fields = {}

    client = _lookup_api_client(_extract_api_key(request) or fields.get("api_key", ""))
    if not client:
        return out("error", "Invalid API key or client disabled")
    if str(fields.get("action", "buy")).strip().lower() != "buy":
        return out("error", "Unsupported action")

    pid = str(fields.get("product_id", "")).strip()
    duration = normalize_api_duration(str(fields.get("duration", "")).strip())
    android_id = str(fields.get("android_id", "")).strip()
    if not pid:
        return out("error", "product_id is required")

    product = None
    if pid.lower().startswith("id:") and pid[3:].strip().isdigit():
        product = db_query(
            f"SELECT {_API_PRODUCT_COLS} FROM products WHERE id=?",
            (int(pid[3:].strip()),), fetchone=True, commit=False,
        )
    elif duration:
        rows = db_query(
            f"""SELECT {_API_PRODUCT_COLS} FROM products
                WHERE is_active=1 AND external_enabled=1 AND external_product_id=?
                  AND reseller_price >= ?
                ORDER BY reseller_price ASC, id ASC""",
            (pid, _api_min_price()), fetchall=True, commit=False,
        ) or []
        product = next(
            (r for r in rows if normalize_api_duration(str(r[4] or "")).casefold() == duration.casefold()),
            None,
        )
    else:
        return out("error", "duration is required")
    if not product:
        return out("error", "Product not found for this product_id and duration")

    status, body = await _execute_api_purchase(client, product, 1, android_id)
    if body.get("keys"):
        return out(
            "success", "Key generated",
            key=body["keys"][0], balance=body.get("balance"), charged=body.get("charged"),
        )
    return out("error", str(body.get("error") or "; ".join(body.get("errors") or []) or "Could not generate key"))


async def start_reseller_api_server():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/api/v1/health", reseller_api_health)
    app.router.add_get("/api/v1/products", reseller_api_products)
    app.router.add_post("/api/v1/buy", reseller_api_buy)
    app.router.add_post("/api/reseller_v1.php", reseller_compat_buy)
    app.router.add_post("/api/reseller_v1", reseller_compat_buy)
    app.router.add_get("/health", reseller_api_health)
    runner = web.AppRunner(app)
    await runner.setup()
    host = os.getenv("API_HOST", "0.0.0.0")
    port = _api_port()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info("Reseller API listening on %s:%s", host, port)
        return runner
    except OSError as exc:
        await runner.cleanup()
        logger.error("Reseller API could not bind to %s:%s: %s", host, port, exc)
        return None


def _api_client_markup(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    status = get_setting("reseller_api_status", "OFF")
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text=f"Reseller API: {status} {'🟢' if status == 'ON' else '🔴'}",
            callback_data="admin_toggle_reseller_api",
            style="success" if status == "ON" else "danger",
        )
    ])
    for row in rows:
        client_id, name, last4, balance, active, orders = row
        status = "🟢" if int(active) else "🔴"
        label = f"{status} {str(name)[:20]} • ₹{float(balance or 0):.2f}"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"admin_api_balance_{int(client_id)}", style="primary"),
            InlineKeyboardButton(text="ON/OFF", callback_data=f"admin_api_toggle_{int(client_id)}", style="success"),
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="➕ Create API Key", callback_data="admin_api_create", style="success"),
        InlineKeyboardButton(text="➕/➖ Wallet", callback_data="admin_api_wallet", style="primary"),
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🔗 Public URL", callback_data="admin_api_set_url", style="primary"),
        InlineKeyboardButton(text="🔌 Port", callback_data="admin_api_set_port", style="primary"),
        InlineKeyboardButton(text="💲 Min Price", callback_data="admin_api_set_minprice", style="primary"),
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    return apply_button_theme(kb)


@dp.callback_query(F.data == "admin_reseller_api_menu")
async def admin_reseller_api_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    status = get_setting("reseller_api_status", "OFF")
    rows = db_query(
        """SELECT id, name, api_key_last4, balance, is_active, total_orders
           FROM reseller_api_clients ORDER BY id DESC""",
        fetchall=True,
        commit=False,
    ) or []
    base_url = _public_api_url() or "Not set - use the 🔗 Public URL button"
    bad_price = db_query(
        "SELECT COUNT(*) FROM products WHERE is_active=1 AND external_enabled=1 AND reseller_price < ?",
        (_api_min_price(),), fetchone=True, commit=False,
    )
    bad_price_count = int(bad_price[0]) if bad_price else 0
    warn = (
        f"\n⚠️ <b>{bad_price_count}</b> API product(s) are below the min price, so they are hidden from the API. "
        "Fix them from Edit Reseller Price.\n"
    ) if bad_price_count else ""
    text = (
        "🌐 <b>RESELLER API CONTROL</b>\n\n"
        f"API switch: <b>{status}</b>\n"
        f"Base URL: <code>{html.escape(base_url)}</code>\n"
        f"Port: <b>{_api_port()}</b> (restart bot after changing)\n"
        f"Min price: <b>₹{_api_min_price():.2f}</b>\n"
        f"{warn}\n"
        "Each client gets its own API key and wallet. Their bot calls "
        "<code>/api/v1/buy</code>; this bot charges the product's reseller price "
        "and consumes one key from your connected external API.\n\n"
        f"Connected API clients: <b>{len(rows)}</b>"
    )
    await call.message.edit_text(text, reply_markup=_api_client_markup(rows), parse_mode="HTML")


@dp.callback_query(F.data == "admin_toggle_reseller_api")
async def admin_toggle_reseller_api(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    current = get_setting("reseller_api_status", "OFF")
    set_setting("reseller_api_status", "OFF" if current == "ON" else "ON")
    await call.answer(f"Reseller API is now {'OFF' if current == 'ON' else 'ON'}", show_alert=True)
    await admin_reseller_api_menu(call)


@dp.callback_query(F.data == "admin_api_create")
async def admin_api_create(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "➕ <b>Create reseller API access</b>\n\n"
        "Send a short name for the other bot/customer.\n"
        "Example: <code>Rahul Bot</code>",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.api_client_name)


@dp.message(AdminStates.api_client_name)
async def admin_api_create_save(m: Message, state: FSMContext):
    name = " ".join((m.text or "").split()).strip()
    if not name or len(name) > 48:
        return await m.answer("❌ Name must be between 1 and 48 characters.", reply_markup=admin_back_kb())
    token = "yp_live_" + secrets.token_urlsafe(24)
    db_query(
        """INSERT INTO reseller_api_clients
           (name, api_key_hash, api_key_last4, created_at)
           VALUES (?, ?, ?, ?)""",
        (name, _api_key_hash(token), token[-4:], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    await state.clear()
    await m.answer(
        "✅ <b>API client created</b>\n\n"
        f"Name: <b>{html.escape(name)}</b>\n"
        "Copy this key now; it is shown only once:\n\n"
        f"<code>{html.escape(token)}</code>\n\n"
        "Use it as <code>X-API-Key</code>. Add wallet balance from 🌐 Reseller API.",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_api_wallet")
async def admin_api_wallet(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    rows = db_query(
        "SELECT id, name, api_key_last4, balance, is_active, total_orders FROM reseller_api_clients ORDER BY name COLLATE NOCASE",
        fetchall=True,
        commit=False,
    ) or []
    if not rows:
        return await call.answer("Create an API client first.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{str(row[1])[:28]} — ₹{float(row[3] or 0):.2f}",
            callback_data=f"admin_api_balance_{int(row[0])}",
            style="primary",
        )]
        for row in rows
    ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back", callback_data="admin_reseller_api_menu", style="danger")])
    await call.message.edit_text(
        "💰 <b>Select API client wallet</b>\n\n"
        "Send a positive amount to add balance or a negative amount to deduct it.\n"
        "Example: <code>500</code> or <code>-100</code>",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_api_balance_"))
async def admin_api_balance_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    client_id = int(call.data.rsplit("_", 1)[1])
    row = db_query("SELECT name, balance FROM reseller_api_clients WHERE id=?", (client_id,), fetchone=True, commit=False)
    if not row:
        return await call.answer("Client not found.", show_alert=True)
    await state.update_data(api_client_id=client_id)
    await call.message.edit_text(
        f"💰 <b>{html.escape(str(row[0]))}</b>\nCurrent wallet: <b>₹{float(row[1] or 0):.2f}</b>\n\n"
        "Send amount to add or deduct. Example: <code>500</code> or <code>-100</code>.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.api_client_balance)


@dp.message(AdminStates.api_client_balance)
async def admin_api_balance_save(m: Message, state: FSMContext):
    try:
        amount = float((m.text or "").replace(",", "").strip())
    except (ValueError, TypeError):
        return await m.answer("❌ Enter a valid amount, e.g. 500 or -100.", reply_markup=admin_back_kb())
    data = await state.get_data()
    client_id = int(data["api_client_id"])
    db_query("UPDATE reseller_api_clients SET balance=balance+? WHERE id=?", (amount, client_id))
    row = db_query("SELECT name, balance FROM reseller_api_clients WHERE id=?", (client_id,), fetchone=True, commit=False)
    if row and float(row[1] or 0) < 0:
        db_query("UPDATE reseller_api_clients SET balance=0 WHERE id=?", (client_id,))
    balance = db_query("SELECT balance FROM reseller_api_clients WHERE id=?", (client_id,), fetchone=True, commit=False)
    await state.clear()
    await m.answer(
        f"✅ Wallet updated for <b>{html.escape(str(row[0]) if row else 'client')}</b>.\n"
        f"New balance: <b>₹{float(balance[0] or 0):.2f}</b>",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_api_toggle_"))
async def admin_api_toggle(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    client_id = int(call.data.rsplit("_", 1)[1])
    row = db_query("SELECT is_active FROM reseller_api_clients WHERE id=?", (client_id,), fetchone=True)
    if not row:
        return await call.answer("Client not found.", show_alert=True)
    new_status = 0 if int(row[0]) else 1
    db_query("UPDATE reseller_api_clients SET is_active=? WHERE id=?", (new_status, client_id))
    await call.answer(f"Client {'enabled' if new_status else 'disabled'}.", show_alert=True)
    await admin_reseller_api_menu(call)


@dp.callback_query(F.data == "admin_api_set_url")
async def admin_api_set_url(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "🔗 <b>Public API URL</b>\n\n"
        f"Current: <code>{html.escape(_public_api_url() or 'not set')}</code>\n\n"
        "Send the public address of this bot's API, e.g. <code>https://mybot.up.railway.app</code>\n"
        "Send <code>-</code> to clear it.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.api_set_url)


@dp.message(AdminStates.api_set_url)
async def admin_api_save_url(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    value = (m.text or "").strip()
    if value == "-":
        value = ""
    elif not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://", reply_markup=admin_back_kb())
    set_setting("reseller_api_public_url", value.rstrip("/"))
    await state.clear()
    await m.answer("✅ Public URL saved.", reply_markup=admin_kb())


@dp.callback_query(F.data == "admin_api_set_port")
async def admin_api_set_port(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "🔌 <b>API Port</b>\n\n"
        f"Current: <b>{_api_port()}</b>\n\n"
        "Send a port number (1-65535). Send <code>-</code> to go back to auto "
        "(env RESELLER_API_PORT, then host PORT, then 8099).\n\n"
        "⚠️ The bot must be restarted for a port change to apply.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.api_set_port)


@dp.message(AdminStates.api_set_port)
async def admin_api_save_port(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    raw = (m.text or "").strip()
    if raw == "-":
        set_setting("reseller_api_port", "")
        await state.clear()
        return await m.answer("✅ Port set to auto. Restart the bot to apply.", reply_markup=admin_kb())
    if not raw.isdigit() or not (1 <= int(raw) <= 65535):
        return await m.answer("❌ Send a number between 1 and 65535.", reply_markup=admin_back_kb())
    set_setting("reseller_api_port", raw)
    await state.clear()
    await m.answer(f"✅ Port saved: <b>{raw}</b>. Restart the bot to apply.", reply_markup=admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_api_set_minprice")
async def admin_api_set_minprice(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "💲 <b>API Minimum Price</b>\n\n"
        f"Current: <b>₹{_api_min_price():.2f}</b>\n\n"
        "API products with a reseller price below this are hidden and cannot be bought. "
        "This stops free keys when a price was left at 0.\n"
        "Send a new amount, e.g. <code>50</code>.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.api_set_min_price)


@dp.message(AdminStates.api_set_min_price)
async def admin_api_save_minprice(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    try:
        value = float((m.text or "").replace(",", "").strip())
    except (ValueError, TypeError):
        return await m.answer("❌ Send a valid amount.", reply_markup=admin_back_kb())
    if not (0 < value < 1_000_000):
        return await m.answer("❌ Amount must be more than 0.", reply_markup=admin_back_kb())
    set_setting("reseller_api_min_price", str(value))
    await state.clear()
    await m.answer(f"✅ Min price saved: <b>₹{value:.2f}</b>", reply_markup=admin_kb(), parse_mode="HTML")



# ==============================================================================
# SOURCE CODE SALE (admin toggles it ON/OFF; buyers pay from their wallet)
# ==============================================================================
def _source_cfg() -> dict:
    try:
        price = float(get_setting("source_sale_price", "0"))
    except (TypeError, ValueError):
        price = 0.0
    return {
        "on": get_setting("source_sale_status", "OFF") == "ON",
        "title": get_setting("source_sale_title", "Bot Source Code") or "Bot Source Code",
        "desc": get_setting("source_sale_desc", ""),
        "price": max(0.0, price),
        "file_id": get_setting("source_sale_file_id", ""),
        "file_name": get_setting("source_sale_file_name", ""),
    }


def _source_ready(cfg: dict) -> bool:
    return bool(cfg["file_id"]) and cfg["price"] > 0


def _source_has_bought(user_id: int) -> bool:
    return bool(db_query("SELECT 1 FROM source_sales WHERE user_id=? LIMIT 1", (user_id,), fetchone=True, commit=False))


async def _deliver_source_file(user_id: int, cfg: dict) -> bool:
    try:
        await bot.send_document(
            user_id, cfg["file_id"],
            caption=f"💻 <b>{html.escape(cfg['title'])}</b>\n\n✅ Thank you for your purchase!",
            parse_mode="HTML",
        )
        return True
    except Exception as err:
        logger.warning("Source code delivery to %s failed: %s", user_id, err)
        return False


async def _render_source_menu(call: CallbackQuery) -> None:
    cfg = _source_cfg()
    uid = call.from_user.id
    row = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True, commit=False)
    balance = float(row[0] or 0) if row else 0.0
    bought = _source_has_bought(uid)
    text = (
        f"💻 <b>{html.escape(cfg['title'])}</b>\n\n"
        f"{html.escape(cfg['desc'])}\n\n"
        f"💰 Price: <b>{fmt_curr(cfg['price'])}</b>\n"
        f"👛 Your balance: <b>{fmt_curr(balance)}</b>"
    )
    rows = []
    if bought:
        text += "\n\n✅ You have already purchased this."
        rows.append([InlineKeyboardButton(text="📥 Download Again", callback_data="source_download", style="success")])
    elif _source_ready(cfg):
        rows.append([InlineKeyboardButton(text=f"🛒 Buy Now - {fmt_curr(cfg['price'])}", callback_data="source_buy", style="success")])
        if balance < cfg["price"]:
            text += "\n\n⚠️ Your balance is low. Add balance first."
            rows.append([InlineKeyboardButton(text="➕ Add Balance", callback_data="menu_add_balance", style="primary")])
    else:
        text += "\n\n⏳ Coming soon."
    rows.append([InlineKeyboardButton(text="BACK", callback_data="back_main", style="danger")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


@dp.callback_query(F.data == "menu_source_code")
async def menu_source_code(call: CallbackQuery):
    if not _source_cfg()["on"]:
        return await call.answer("This is not available right now.", show_alert=True)
    await _render_source_menu(call)
    await call.answer()


@dp.callback_query(F.data == "source_download")
async def source_download(call: CallbackQuery):
    cfg = _source_cfg()
    if not cfg["on"] or not cfg["file_id"] or not _source_has_bought(call.from_user.id):
        return await call.answer("Not available.", show_alert=True)
    ok = await _deliver_source_file(call.from_user.id, cfg)
    if ok:
        await call.answer("📥 File sent.")
    else:
        await call.answer("File could not be sent. Please contact support.", show_alert=True)


@dp.callback_query(F.data == "source_buy")
async def source_buy(call: CallbackQuery):
    cfg = _source_cfg()
    uid = call.from_user.id
    if not cfg["on"] or not _source_ready(cfg):
        return await call.answer("This is not available right now.", show_alert=True)
    if _source_has_bought(uid):
        return await source_download(call)

    price = cfg["price"]
    sale_id = None
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (price, uid, price))
        if cur.rowcount == 1:
            cur.execute(
                "INSERT INTO source_sales (user_id, price, created_at) VALUES (?, ?, ?)",
                (uid, price, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            sale_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    if sale_id is None:
        return await call.answer(f"Insufficient balance. You need {fmt_curr(price)}.", show_alert=True)

    if not await _deliver_source_file(uid, cfg):
        # Never keep the money if the file could not be delivered.
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (price, uid))
        db_query("DELETE FROM source_sales WHERE id=?", (sale_id,))
        return await call.answer("File could not be sent, your money was refunded. Please try again later.", show_alert=True)

    log_activity(uid, "SOURCE_SALE", f"Price={price:.2f}")
    await call.answer("✅ Purchased! File sent.")
    try:
        await _render_source_menu(call)
    except Exception:
        pass
    try:
        username = f"@{html.escape(call.from_user.username)}" if call.from_user.username else "no username"
        await bot.send_message(
            get_owner_id(),
            "💰 <b>SOURCE CODE SOLD</b>\n\n"
            f"👤 <a href=\"tg://user?id={uid}\">{html.escape(call.from_user.full_name or 'Unknown')}</a> ({username})\n"
            f"🆔 <code>{uid}</code>\n"
            f"💵 Amount: <b>{fmt_curr(price)}</b>",
            parse_mode="HTML",
        )
    except Exception as err:
        logger.warning("Source sale alert to admin failed: %s", err)


# ==============================================================================
# 🤖 BOT SALE — Buy This Bot
# ==============================================================================

def _bot_sale_cfg() -> dict:
    price = get_setting("bot_sale_price", "0")
    try:
        price = float(price)
    except Exception:
        price = 0.0
    return {
        "on":    get_setting("bot_sale_status", "OFF") == "ON",
        "title": get_setting("bot_sale_title", "Buy This Bot") or "Buy This Bot",
        "desc":  get_setting("bot_sale_desc", "Get your own copy of this bot fully set up and ready to use."),
        "price": price,
    }

@dp.callback_query(F.data == "menu_bot_sale")
async def menu_bot_sale(call: CallbackQuery):
    cfg = _bot_sale_cfg()
    if not cfg["on"]:
        return await call.answer("Bot sale is not available right now.", show_alert=True)
    await call.answer()
    uid = call.from_user.id
    row = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True, commit=False)
    balance = float(row[0] or 0) if row else 0.0
    text = (
        f"\U0001f916 <b>{html.escape(cfg['title'])}</b>\n\n"
        f"{html.escape(cfg['desc'])}\n\n"
        f"\U0001f4b0 <b>Price:</b> {fmt_curr(cfg['price'])}\n"
        f"\U0001f45b <b>Your Balance:</b> {fmt_curr(balance)}\n\n"
        "\u2705 <b>Payment ke baad aapko yeh details deni hongi:</b>\n"
        "\U0001f4e7 Gmail\n"
        "\U0001f194 Aapka Telegram User ID\n"
        "\U0001f464 Aapka Telegram Username\n"
        "\U0001f916 Naye Bot ka Username (BotFather se)"
    )
    rows = []
    if cfg["price"] <= 0:
        rows.append([InlineKeyboardButton(text="\u26a0\ufe0f Price not set yet", callback_data="menu_bot_sale", style="danger")])
    elif balance >= cfg["price"]:
        rows.append([InlineKeyboardButton(text=f"\U0001f916 Buy Now \u2014 {fmt_curr(cfg['price'])}", callback_data="bot_sale_confirm", style="success")])
    else:
        text += f"\n\n\u26a0\ufe0f Balance kam hai \u2014 <b>{fmt_curr(cfg['price'] - balance)}</b> aur chahiye."
        rows.append([InlineKeyboardButton(text="\u2795 Add Balance", callback_data="menu_add_balance", style="primary")])
    rows.append([InlineKeyboardButton(text="BACK", callback_data="back_main", style="danger")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


@dp.callback_query(F.data == "bot_sale_confirm")
async def bot_sale_confirm(call: CallbackQuery):
    cfg = _bot_sale_cfg()
    uid = call.from_user.id
    if not cfg["on"]:
        return await call.answer("Not available.", show_alert=True)
    price = cfg["price"]
    conn = sqlite3.connect(DB_PATH, timeout=20)
    sale_id = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (price, uid, price))
        if cur.rowcount == 1:
            cur.execute(
                "INSERT INTO bot_sales (user_id, price, status, created_at) VALUES (?, ?, 'pending', ?)",
                (uid, price, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            sale_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    if sale_id is None:
        return await call.answer(f"Insufficient balance. You need {fmt_curr(price)}.", show_alert=True)
    await call.answer()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"bot_sale_pending_{uid}", str(sale_id)))
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"bot_sale_step_{uid}", "gmail"))
    await call.message.edit_text(
        "\u2705 <b>Payment successful!</b>\n\n"
        "Ab apni details step-by-step bhejein:\n\n"
        "<blockquote>\U0001f4e7 <b>Step 1/4</b>\nApna <b>Gmail address</b> bhejein\n"
        "<i>Example: yourname@gmail.com</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="\u274c Cancel & Refund", callback_data=f"bot_sale_cancel_{sale_id}", style="danger")
        ]]),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("bot_sale_cancel_"))
async def bot_sale_cancel(call: CallbackQuery):
    uid = call.from_user.id
    sale_id = int(call.data.split("_")[-1])
    row = db_query("SELECT price, status FROM bot_sales WHERE id=? AND user_id=?", (sale_id, uid), fetchone=True, commit=False)
    if row and row[1] not in ("cancelled","refunded","delivered"):
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (float(row[0]), uid))
        db_query("UPDATE bot_sales SET status='cancelled' WHERE id=?", (sale_id,))
    db_query("DELETE FROM settings WHERE key=?", (f"bot_sale_step_{uid}",))
    db_query("DELETE FROM settings WHERE key=?", (f"bot_sale_pending_{uid}",))
    await call.answer("Refund diya gaya!", show_alert=True)
    await menu_bot_sale(call)


def _has_bot_sale_step(message: Message) -> bool:
    uid = message.from_user.id
    return bool(get_setting(f"bot_sale_step_{uid}", "")) and bool(get_setting(f"bot_sale_pending_{uid}", ""))


@dp.message(F.text, _has_bot_sale_step)
async def bot_sale_detail_handler(message: Message):
    uid = message.from_user.id
    step = get_setting(f"bot_sale_step_{uid}", "")
    sale_id_str = get_setting(f"bot_sale_pending_{uid}", "")
    sale_id = int(sale_id_str)
    text = message.text.strip()
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="\u274c Cancel & Refund", callback_data=f"bot_sale_cancel_{sale_id}", style="danger")
    ]])
    if step == "gmail":
        db_query("UPDATE bot_sales SET gmail=? WHERE id=?", (text, sale_id))
        db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"bot_sale_step_{uid}", "buyer_userid"))
        await message.answer(
            f"\u2705 Gmail: <code>{html.escape(text)}</code>\n\n"
            "<blockquote>\U0001f194 <b>Step 2/4</b>\nApna <b>Telegram User ID</b> bhejein\n"
            "<i>Pata nahi? @userinfobot se ID milegi</i></blockquote>",
            parse_mode="HTML", reply_markup=cancel_kb)
    elif step == "buyer_userid":
        db_query("UPDATE bot_sales SET buyer_user_id=? WHERE id=?", (text, sale_id))
        db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"bot_sale_step_{uid}", "buyer_username"))
        await message.answer(
            f"\u2705 User ID: <code>{html.escape(text)}</code>\n\n"
            "<blockquote>\U0001f464 <b>Step 3/4</b>\nApna <b>Telegram Username</b> bhejein\n"
            "<i>Example: @yourname</i></blockquote>",
            parse_mode="HTML", reply_markup=cancel_kb)
    elif step == "buyer_username":
        db_query("UPDATE bot_sales SET buyer_username=? WHERE id=?", (text, sale_id))
        db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"bot_sale_step_{uid}", "bot_username"))
        await message.answer(
            f"\u2705 Username: <code>{html.escape(text)}</code>\n\n"
            "<blockquote>\U0001f916 <b>Step 4/4</b>\nNaye bot ka <b>Bot Username</b> bhejein\n"
            "<i>BotFather se bot banao, uska username yahan bhejo\nExample: @MyNewShopBot</i></blockquote>",
            parse_mode="HTML", reply_markup=cancel_kb)
    elif step == "bot_username":
        db_query("UPDATE bot_sales SET bot_username=?, status='details_received' WHERE id=?", (text, sale_id))
        db_query("DELETE FROM settings WHERE key=?", (f"bot_sale_step_{uid}",))
        db_query("DELETE FROM settings WHERE key=?", (f"bot_sale_pending_{uid}",))
        sale = db_query("SELECT price, gmail, buyer_user_id, buyer_username, bot_username FROM bot_sales WHERE id=?",
                        (sale_id,), fetchone=True, commit=False)
        try:
            buyer_tag = f"@{html.escape(message.from_user.username)}" if message.from_user.username else "no username"
            await bot.send_message(
                get_owner_id(),
                "\U0001f916 <b>NEW BOT SALE \u2014 Details Received!</b>\n\n"
                f"\U0001f464 Buyer: <a href='tg://user?id={uid}'>{html.escape(message.from_user.full_name or 'User')}</a> ({buyer_tag})\n"
                f"\U0001f194 Buyer TG ID: <code>{uid}</code>\n"
                f"\U0001f4b0 Paid: <b>{fmt_curr(float(sale[0]))}</b>\n\n"
                f"\U0001f4e7 Gmail: <code>{html.escape(sale[1] or '')}</code>\n"
                f"\U0001f194 Their User ID: <code>{html.escape(sale[2] or '')}</code>\n"
                f"\U0001f464 Their Username: <code>{html.escape(sale[3] or '')}</code>\n"
                f"\U0001f916 Bot Username: <code>{html.escape(sale[4] or '')}</code>\n\n"
                f"\U0001f516 Sale ID: <code>{sale_id}</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="\u2705 Mark Delivered", callback_data=f"admin_botsale_delivered_{sale_id}", style="success"),
                    InlineKeyboardButton(text="\U0001f504 Refund", callback_data=f"admin_botsale_refund_{sale_id}", style="danger")
                ]]),
                parse_mode="HTML"
            )
        except Exception as err:
            logger.warning("Bot sale admin alert failed: %s", err)
        await message.answer(
            "\U0001f389 <b>Sab details mil gayi!</b>\n\n"
            "<blockquote>"
            f"\U0001f4e7 Gmail: <code>{html.escape(sale[1] or '')}</code>\n"
            f"\U0001f194 User ID: <code>{html.escape(sale[2] or '')}</code>\n"
            f"\U0001f464 Username: <code>{html.escape(sale[3] or '')}</code>\n"
            f"\U0001f916 Bot: <code>{html.escape(text)}</code>"
            "</blockquote>\n\n"
            "\u2705 Admin ko details bhej di gayi hain.\n"
            "<i>Jald hi aapka bot deliver kiya jaayega!</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="\U0001f3e0 Main Menu", callback_data="back_main", style="primary")
            ]])
        )


@dp.callback_query(F.data.startswith("admin_botsale_delivered_"))
async def admin_botsale_delivered(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    sale_id = int(call.data.split("_")[-1])
    db_query("UPDATE bot_sales SET status='delivered' WHERE id=?", (sale_id,))
    sale = db_query("SELECT user_id FROM bot_sales WHERE id=?", (sale_id,), fetchone=True, commit=False)
    if sale:
        try:
            await bot.send_message(int(sale[0]),
                "\U0001f389 <b>Aapka bot deliver ho gaya!</b>\n\nAdmin ne aapka bot setup kar diya. Koi issue ho to support se contact karo.",
                parse_mode="HTML")
        except Exception: pass
    await call.answer("\u2705 Delivered & buyer notified!", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="\u2705 Delivered", callback_data="noop", style="success")]]))
    except Exception: pass


@dp.callback_query(F.data.startswith("admin_botsale_refund_"))
async def admin_botsale_refund(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    sale_id = int(call.data.split("_")[-1])
    sale = db_query("SELECT user_id, price, status FROM bot_sales WHERE id=?", (sale_id,), fetchone=True, commit=False)
    if sale and sale[2] not in ("cancelled", "refunded", "delivered"):
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (float(sale[1]), int(sale[0])))
        db_query("UPDATE bot_sales SET status='refunded' WHERE id=?", (sale_id,))
        try:
            await bot.send_message(int(sale[0]),
                f"\U0001f4b8 <b>Refund!</b>\n\n{fmt_curr(float(sale[1]))} aapke wallet mein wapas add kar diya gaya.",
                parse_mode="HTML")
        except Exception: pass
    await call.answer("\u2705 Refunded!", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="\U0001f504 Refunded", callback_data="noop", style="danger")]]))
    except Exception: pass


@dp.callback_query(F.data == "admin_bot_sale_menu")
async def admin_bot_sale_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    await call.answer()
    cfg = _bot_sale_cfg()
    stats = db_query("SELECT COUNT(*), COALESCE(SUM(price),0) FROM bot_sales WHERE status NOT IN ('cancelled','refunded')", fetchone=True, commit=False)
    count, revenue = (int(stats[0]), float(stats[1])) if stats else (0, 0.0)
    on = cfg["on"]
    status_label = "ON \U0001f7e2" if on else "OFF \U0001f534"
    text = (
        "\U0001f916 <b>Bot Sale Settings</b>\n\n"
        f"Status: <b>{status_label}</b>\n"
        f"Title: <b>{html.escape(cfg['title'])}</b>\n"
        f"Price: <b>{fmt_curr(cfg['price'])}</b>\n\n"
        f"Description:\n{html.escape(cfg['desc'][:200])}\n\n"
        f"\U0001f4c8 Sold: <b>{count}</b> | Earned: <b>{fmt_curr(revenue)}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u270f\ufe0f Title", callback_data="admin_botsale_set_title", style="primary"),
         InlineKeyboardButton(text="\U0001f4b0 Price", callback_data="admin_botsale_set_price", style="primary")],
        [InlineKeyboardButton(text="\U0001f4dd Description", callback_data="admin_botsale_set_desc", style="primary")],
        [InlineKeyboardButton(text=f"Bot Sale: {status_label}",
            callback_data="admin_botsale_toggle", style="success" if on else "danger")],
        [InlineKeyboardButton(text="\U0001f4cb View Orders", callback_data="admin_botsale_orders", style="success")],
        [InlineKeyboardButton(text="\u2b05\ufe0f Back to Admin", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "admin_botsale_toggle")
async def admin_botsale_toggle(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    new_val = "OFF" if get_setting("bot_sale_status","OFF") == "ON" else "ON"
    set_setting("bot_sale_status", new_val)
    await call.answer(f"Bot Sale: {new_val}", show_alert=True)
    await admin_bot_sale_menu(call)


@dp.callback_query(F.data == "admin_botsale_set_price")
async def admin_botsale_set_price(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.answer()
    await state.set_state("botsale_set_price")
    await call.message.edit_text(
        f"\U0001f4b0 <b>Bot Sale Price</b>\n\nCurrent: <b>{fmt_curr(float(get_setting('bot_sale_price','0')))}</b>\n\nNaya price bhejein:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u274c Cancel", callback_data="admin_bot_sale_menu", style="danger")]]),
        parse_mode="HTML")


@dp.message(StateFilter("botsale_set_price"))
async def admin_botsale_got_price(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id): return
    try: price = float(message.text.strip().replace("\u20b9","").replace(",",""))
    except (ValueError, TypeError): return await message.answer("\u26a0\ufe0f Valid number bhejein!")
    set_setting("bot_sale_price", str(price))
    await state.clear()
    await message.answer(f"\u2705 Price set: <b>{fmt_curr(price)}</b>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u2b05\ufe0f Back", callback_data="admin_bot_sale_menu", style="danger")]]))


@dp.callback_query(F.data == "admin_botsale_set_title")
async def admin_botsale_set_title(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.answer()
    await state.set_state("botsale_set_title")
    await call.message.edit_text("\u270f\ufe0f <b>Bot Sale Title</b>\n\nNaya title bhejein:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u274c Cancel", callback_data="admin_bot_sale_menu", style="danger")]]),
        parse_mode="HTML")


@dp.message(StateFilter("botsale_set_title"))
async def admin_botsale_got_title(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id): return
    set_setting("bot_sale_title", message.text.strip())
    await state.clear()
    await message.answer("\u2705 Title updated!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u2b05\ufe0f Back", callback_data="admin_bot_sale_menu", style="danger")]]))


@dp.callback_query(F.data == "admin_botsale_set_desc")
async def admin_botsale_set_desc(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id): return
    await call.answer()
    await state.set_state("botsale_set_desc")
    await call.message.edit_text("\U0001f4dd <b>Bot Sale Description</b>\n\nNaya description bhejein:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u274c Cancel", callback_data="admin_bot_sale_menu", style="danger")]]),
        parse_mode="HTML")


@dp.message(StateFilter("botsale_set_desc"))
async def admin_botsale_got_desc(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id): return
    set_setting("bot_sale_desc", message.text.strip())
    await state.clear()
    await message.answer("\u2705 Description updated!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u2b05\ufe0f Back", callback_data="admin_bot_sale_menu", style="danger")]]))


@dp.callback_query(F.data == "admin_botsale_orders")
async def admin_botsale_orders(call: CallbackQuery):
    if not is_admin_user(call.from_user.id): return
    await call.answer()
    rows = db_query(
        "SELECT id, user_id, price, gmail, buyer_username, bot_username, status, created_at FROM bot_sales ORDER BY id DESC LIMIT 20",
        fetchall=True) or []
    if not rows:
        return await call.message.edit_text("\U0001f4cb <b>Bot Sale Orders</b>\n\nAbhi tak koi order nahi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u2b05\ufe0f Back", callback_data="admin_bot_sale_menu", style="danger")]]),
            parse_mode="HTML")
    text = "\U0001f4cb <b>Bot Sale Orders (Last 20)</b>\n\n"
    for r in rows:
        icon = "\u2705" if r[6]=="delivered" else "\u23f3" if r[6] in ("pending","details_received") else "\U0001f504" if r[6]=="refunded" else "\u274c"
        text += (
            f"{icon} <b>#{r[0]}</b> \u2014 {fmt_curr(float(r[2]))} \u2014 <code>{r[7][:10]}</code>\n"
            f"  Gmail: <code>{html.escape(r[3] or '-')}</code>\n"
            f"  Username: <code>{html.escape(r[4] or '-')}</code> | Bot: <code>{html.escape(r[5] or '-')}</code>\n\n"
        )
    await call.message.edit_text(text[:4000],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\u2b05\ufe0f Back", callback_data="admin_bot_sale_menu", style="danger")]]),
        parse_mode="HTML")


# ---- admin side ----
def _source_admin_text() -> str:
    cfg = _source_cfg()
    stats = db_query("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM source_sales", fetchone=True, commit=False)
    count, revenue = (int(stats[0]), float(stats[1])) if stats else (0, 0.0)
    desc = cfg["desc"] if len(cfg["desc"]) <= 300 else cfg["desc"][:300] + "..."
    return (
        "💻 <b>Source Code Sale Settings</b>\n\n"
        f"Status: <b>{'ON 🟢' if cfg['on'] else 'OFF 🔴'}</b>\n"
        f"Title: <b>{html.escape(cfg['title'])}</b>\n"
        f"Price: <b>{fmt_curr(cfg['price'])}</b>\n"
        f"File: <b>{html.escape(cfg['file_name']) if cfg['file_id'] else 'not uploaded'}</b>\n\n"
        f"Description:\n{html.escape(desc)}\n\n"
        f"📈 Sold: <b>{count}</b> | Earned: <b>{fmt_curr(revenue)}</b>"
    )


def _source_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Title", callback_data="admin_source_title", style="primary"),
            InlineKeyboardButton(text="💰 Price", callback_data="admin_source_price", style="primary"),
        ],
        [InlineKeyboardButton(text="📝 Description", callback_data="admin_source_desc", style="primary")],
        [InlineKeyboardButton(text="📁 Upload / Replace File", callback_data="admin_source_file", style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])


@dp.callback_query(F.data == "admin_toggle_source_sale")
async def admin_toggle_source_sale(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    cfg = _source_cfg()
    new_status = "OFF" if cfg["on"] else "ON"
    if new_status == "ON" and not _source_ready(cfg):
        return await call.answer("First set a price and upload the file in ⚙️ Source Settings.", show_alert=True)
    set_setting("source_sale_status", new_status)
    await call.answer(f"Source sale {new_status}")
    await call.message.edit_reply_markup(reply_markup=admin_kb())


@dp.callback_query(F.data == "admin_source_menu")
async def admin_source_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text(_source_admin_text(), reply_markup=_source_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_source_title")
async def admin_source_title(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text("✏️ Send the new <b>title</b> (max 100 characters).", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.source_title)


@dp.message(AdminStates.source_title)
async def admin_source_save_title(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    value = (m.text or "").strip()
    if not value or len(value) > 100:
        return await m.answer("❌ Send a title up to 100 characters.", reply_markup=admin_back_kb())
    set_setting("source_sale_title", value)
    await state.clear()
    await m.answer(_source_admin_text(), reply_markup=_source_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_source_price")
async def admin_source_price(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text("💰 Send the new <b>price</b>, for example <code>499</code>.", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.source_price)


@dp.message(AdminStates.source_price)
async def admin_source_save_price(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    try:
        value = float((m.text or "").replace(",", "").strip())
    except (ValueError, TypeError):
        return await m.answer("❌ Send a valid number.", reply_markup=admin_back_kb())
    if not (0 < value < 10_000_000):
        return await m.answer("❌ Price must be more than 0.", reply_markup=admin_back_kb())
    set_setting("source_sale_price", str(value))
    await state.clear()
    await m.answer(_source_admin_text(), reply_markup=_source_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_source_desc")
async def admin_source_desc(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "📝 Send the new <b>description</b> buyers will see (features, what is included). Max 3000 characters.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.source_desc)


@dp.message(AdminStates.source_desc)
async def admin_source_save_desc(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    value = (m.text or "").strip()
    if not value or len(value) > 3000:
        return await m.answer("❌ Send a description up to 3000 characters.", reply_markup=admin_back_kb())
    set_setting("source_sale_desc", value)
    await state.clear()
    await m.answer(_source_admin_text(), reply_markup=_source_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_source_file")
async def admin_source_file(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.message.edit_text(
        "📁 Send the <b>file</b> you want to sell as a document (a .zip is best).\n\n"
        "⚠️ Before sending, remove your bot token, API keys and the database file from it.",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.source_file)


@dp.message(AdminStates.source_file, F.document)
async def admin_source_save_file(m: Message, state: FSMContext):
    if not is_admin_user(m.from_user.id):
        return
    set_setting("source_sale_file_id", m.document.file_id)
    set_setting("source_sale_file_name", m.document.file_name or "source_code")
    await state.clear()
    await m.answer(_source_admin_text(), reply_markup=_source_admin_kb(), parse_mode="HTML")


@dp.message(AdminStates.source_file)
async def admin_source_file_wrong(m: Message):
    if not is_admin_user(m.from_user.id):
        return
    await m.answer("❌ Please send the file as a <b>document</b> (attach it with the 📎 paperclip).", reply_markup=admin_back_kb(), parse_mode="HTML")


def _base_button_catalog(menu: str) -> List[Tuple[str, str, str]]:
    """Stable button IDs used by the admin's per-button designer."""
    if menu == "main":
        return [
            ("menu_shop", "Shop Now", "menu_shop"),
            ("menu_profile", "My Profile", "menu_profile"),
            ("menu_add_balance", "Add Balance", "menu_add_balance"),
            ("menu_orders", "My Keys", "menu_orders"),
            ("menu_how_to", "How to use", "menu_how_to"),
            ("menu_all_files", "Download Files", "menu_all_files"),
            ("menu_support", "Support", "menu_support"),
            ("menu_spin_landing", "Daily Gift", "menu_spin_landing"),
            ("menu_referral", "Referral", "menu_referral"),
            ("menu_reseller_dash", "Reseller Panel", "menu_reseller_dash"),
            ("menu_vip_dash", "VIP Club", "menu_vip_dash"),
        ]
    if menu == "admin":
        return [
            ("admin_view_stats", "Bot Statistics", "admin_view_stats"),
            ("admin_user_control_start", "User Control Panel", "admin_user_control_start"),
            ("admin_bulk_deduct_start", "Deduct All Users Balance", "admin_bulk_deduct_start"),
            ("admin_advanced_management", "Advanced Management", "admin_advanced_management"),
            ("admin_bot_system_tools", "BOT SYSTEM Tools", "admin_bot_system_tools"),
            ("admin_add_prod", "Add Product", "admin_add_prod"),
            ("admin_manage_prods", "Manage Products", "admin_manage_prods"),
            ("admin_quick_add_keys", "Add Keys to Existing", "admin_quick_add_keys"),
            ("admin_quick_add_api", "Quick Add API Key", "admin_quick_add_api"),
            ("admin_manage_categories", "Manage Categories", "admin_manage_categories"),
            ("admin_reseller_menu", "Reseller Mgmt", "admin_reseller_menu"),
            ("admin_reseller_api_menu", "Reseller API", "admin_reseller_api_menu"),
            ("admin_button_design", "Button Design", "admin_button_design"),
            ("admin_spin_menu", "Spin Settings", "admin_spin_menu"),
            ("admin_create_coupon", "Create Coupon", "admin_create_coupon"),
            ("admin_broadcast_btn", "Broadcast", "admin_broadcast_btn"),
            ("admin_view_tickets", "View Tickets", "admin_view_tickets"),
            ("admin_set_video", "Tutorial Video", "admin_set_video"),
            ("admin_set_all_files", "All Files Link", "admin_set_all_files"),
            ("admin_set_store_media", "Store GIF / Video", "admin_set_store_media"),
            ("admin_edit_emojis", "Edit All Emojis", "admin_edit_emojis"),
            ("admin_setup_zapupi", "ZapUPI Setup", "admin_setup_zapupi"),
            ("admin_setup_binance", "Binance Setup", "admin_setup_binance"),
            ("admin_setup_external_api", "External Key API", "admin_setup_external_api"),
            ("admin_multi_api_manager", "Multi API Manager", "admin_multi_api_manager"),
            ("admin_ai_api_paste", "AI API Paste", "admin_ai_api_paste"),
            ("admin_ai_support_setup", "AI Support Setup", "admin_ai_support_setup"),
            ("admin_edit_ui_menu", "Edit UI Texts", "admin_edit_ui_menu"),
            ("admin_edit_reseller_price", "Edit Reseller Price", "admin_edit_reseller_price"),
            ("admin_set_reseller_fee", "Reseller Fee", "admin_set_reseller_fee"),
            ("admin_set_reseller_min", "Min Balance", "admin_set_reseller_min"),
            ("admin_set_support_links", "Set Support Links", "admin_set_support_links"),
            ("admin_set_category_emojis", "Set Category Emojis", "admin_set_category_emojis"),
            ("admin_set_panel_emojis", "Set Panel Emojis", "admin_set_panel_emojis"),
            ("admin_set_panel_videos", "Set Panel Video", "admin_set_panel_videos"),
            ("admin_set_welcome_voice", "Welcome Voice", "admin_set_welcome_voice"),
            ("admin_toggle_bot", "Bot Status", "admin_toggle_bot"),
            ("admin_toggle_vip_sys", "VIP System", "admin_toggle_vip_sys"),
            ("admin_maint_categories", "Product Maintenance", "admin_maint_categories"),
        ]
    return []


def _button_catalog(menu: str) -> List[Tuple[str, str, str]]:
    items = list(_base_button_catalog(menu))
    if menu == "main":
        for link in _custom_links():
            items.append((f"link_{link['id']}", link["label"], f"link_{link['id']}"))
    return items


def _button_color(callback_data: str) -> str:
    color = get_setting(f"button_color_cb_{callback_data}", "")
    if color not in {"primary", "success", "danger"}:
        color = get_setting("button_color", "primary")
    return color if color in {"primary", "success", "danger"} else "primary"


def _button_order(menu: str) -> List[str]:
    catalog = _button_catalog(menu)
    default = [item[0] for item in catalog]
    raw = get_setting(f"button_order_{menu}", "")
    try:
        saved = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        saved = []
    result = [key for key in saved if key in default]
    result.extend(key for key in default if key not in result)
    return result


def _save_button_order(menu: str, order: List[str]) -> None:
    set_setting(f"button_order_{menu}", json.dumps(order))


def _button_layout(menu: str) -> int:
    try:
        return max(1, min(3, int(get_setting(f"button_layout_{menu}", get_setting("button_layout", "2")))))
    except (ValueError, TypeError):
        return 2


def _custom_links() -> List[Dict[str, str]]:
    """Admin-created URL buttons (e.g. Payment Proof, YouTube Course) for the main menu."""
    raw = get_setting("custom_link_buttons", "")
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        data = []
    links: List[Dict[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        if item.get("id") and item.get("label") and url.startswith(("http://", "https://", "tg://")):
            links.append({"id": str(item["id"]), "label": str(item["label"]), "url": url})
    return links[:8]


def _save_custom_links(links: List[Dict[str, str]]) -> None:
    set_setting("custom_link_buttons", json.dumps(links))


def _button_width(key: str) -> str:
    width = get_setting(f"button_width_cb_{key}", "auto")
    return width if width in {"auto", "full", "half"} else "auto"


def _button_label(key: str, default: str) -> str:
    return get_setting(f"button_label_cb_{key}", "").strip() or default


def _button_hidden(key: str) -> bool:
    return get_setting(f"button_hidden_cb_{key}", "0") == "1"


def _pack_button_rows(buttons: List[Any], layout: int, key_of) -> List[List[Any]]:
    """Group buttons into rows. full = own row, half = max 2 per row, auto = menu layout."""
    rows: List[List[Any]] = []
    current: List[Any] = []
    current_cap = 0
    for button in buttons:
        width = _button_width(key_of(button))
        cap = 1 if width == "full" else 2 if width == "half" else layout
        if current and len(current) < current_cap and len(current) < cap:
            current.append(button)
        else:
            if current:
                rows.append(current)
            current = [button]
            current_cap = cap
    if current:
        rows.append(current)
    return rows


def apply_button_theme(kb: InlineKeyboardMarkup, menu: str = "") -> InlineKeyboardMarkup:
    """Apply the configured button style to every screen.

    Main/admin menus additionally support the full designer controls
    (custom labels, visibility, ordering and widths).  Shop and other
    submenus keep their intentional layout while still inheriting the
    selected per-button or global colour.
    """
    link_keys = {link["url"]: f"link_{link['id']}" for link in _custom_links()}

    def key_of(button: Any) -> str:
        return button.callback_data or link_keys.get(getattr(button, "url", None) or "", "")

    buttons = []
    for row in kb.inline_keyboard:
        for button in row:
            try:
                button.style = _button_color(key_of(button))
            except Exception:
                pass
            buttons.append(button)

    if menu in {"main", "admin"}:
        visible = []
        for button in buttons:
            key = key_of(button)
            if key and key != "admin_button_design" and _button_hidden(key):
                continue
            custom_label = get_setting(f"button_label_cb_{key}", "").strip() if key else ""
            if custom_label:
                button.text = custom_label
            visible.append(button)
        order = _button_order(menu)
        positions = {key: index for index, key in enumerate(order)}
        visible.sort(key=lambda button: positions.get(key_of(button), len(positions)))
        kb.inline_keyboard = _pack_button_rows(visible, _button_layout(menu), key_of)
    else:
        # Other screens keep their carefully designed rows, but still receive
        # the selected per-button/default colour.
        kb.inline_keyboard = [list(row) for row in kb.inline_keyboard]
    return kb


_COLOR_ICON = {"primary": "🔵", "success": "🟢", "danger": "🔴", "secondary": "⚫"}
_WIDTH_LABEL = {"auto": "Auto", "full": "Full row", "half": "Half (2 per row)"}

# One-tap looks (colours, widths, names, order).
# rows: (button key, label, colour, width, hidden)
_STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "style_a": {
        "title": "Style A",
        "links_before": "menu_support", "link_color": "success", "link_width": "half",
        "rows": [
            ("menu_shop", "Product Store", "primary", "full", False),
            ("menu_reseller_dash", "Reseller Plan", "primary", "full", False),
            ("menu_profile", "My Profile", "primary", "half", False),
            ("menu_add_balance", "Add Balance", "success", "half", False),
            ("menu_orders", "All History", "primary", "half", False),
            ("menu_spin_landing", "Ludo Spin", "primary", "half", False),
            ("menu_how_to", "Tutorial", "primary", "half", False),
            ("menu_support", "Support", "danger", "full", False),
            ("menu_referral", "Referral", "primary", "half", True),
            ("menu_all_files", "Download Files", "success", "half", True),
            ("menu_vip_dash", "VIP Club", "success", "full", True),
        ],
    },
    "style_b": {
        "title": "Style B",
        "links_before": "menu_spin_landing", "link_color": "danger", "link_width": "full",
        "rows": [
            ("menu_shop", "Product Store", "primary", "full", False),
            ("menu_profile", "My Profile", "success", "half", False),
            ("menu_add_balance", "Add Balance", "success", "half", False),
            ("menu_orders", "All History", "danger", "half", False),
            ("menu_referral", "Referral", "danger", "half", False),
            ("menu_how_to", "Tutorials", "success", "half", False),
            ("menu_support", "Support", "success", "half", False),
            ("menu_spin_landing", "Ludo Spin", "success", "half", False),
            ("menu_all_files", "Download Files", "success", "half", False),
            ("menu_reseller_dash", "Reseller Panel", "primary", "full", False),
            ("menu_vip_dash", "VIP Club", "primary", "full", False),
        ],
    },
    "style_c": {
        "title": "Style C — Cyber Grid",
        "links_before": "menu_support", "link_color": "primary", "link_width": "half",
        "rows": [
            ("menu_shop", "⚡ ENTER STORE", "primary", "full", False),
            ("menu_profile", "👤 Profile", "success", "half", False),
            ("menu_add_balance", "💳 Wallet", "success", "half", False),
            ("menu_orders", "🧾 Orders", "primary", "half", False),
            ("menu_all_files", "📥 Files", "primary", "half", False),
            ("menu_spin_landing", "🎲 Daily Spin", "success", "half", False),
            ("menu_referral", "🚀 Invite & Earn", "success", "half", False),
            ("menu_how_to", "📖 Guide", "primary", "half", False),
            ("menu_support", "🛟 Live Support", "danger", "full", False),
            ("menu_reseller_dash", "👑 Reseller", "success", "full", False),
            ("menu_vip_dash", "🌟 VIP Club", "success", "full", True),
        ],
    },
    "style_d": {
        "title": "Style D — Premium",
        "links_before": "menu_how_to", "link_color": "success", "link_width": "full",
        "rows": [
            ("menu_shop", "🛍 Product Store", "success", "full", False),
            ("menu_add_balance", "💰 Add Funds", "success", "half", False),
            ("menu_profile", "✨ My Profile", "primary", "half", False),
            ("menu_orders", "📜 Purchase History", "primary", "half", False),
            ("menu_referral", "🎁 Referral Rewards", "success", "half", False),
            ("menu_spin_landing", "🎰 Lucky Gift", "success", "half", False),
            ("menu_how_to", "🎓 Tutorials", "primary", "half", False),
            ("menu_support", "💬 Priority Support", "danger", "full", False),
            ("menu_all_files", "⬇️ Download Center", "primary", "full", False),
            ("menu_reseller_dash", "💎 Reseller Club", "success", "full", False),
            ("menu_vip_dash", "🌟 VIP Club", "success", "full", False),
        ],
    },
    "style_e": {
        "title": "Style E — Compact",
        "links_before": "menu_support", "link_color": "danger", "link_width": "half",
        "rows": [
            ("menu_shop", "Shop", "primary", "full", False),
            ("menu_profile", "Profile", "primary", "half", False),
            ("menu_add_balance", "Balance", "success", "half", False),
            ("menu_orders", "Orders", "primary", "half", False),
            ("menu_all_files", "Files", "success", "half", False),
            ("menu_how_to", "Guide", "primary", "half", False),
            ("menu_support", "Support", "danger", "half", False),
            ("menu_referral", "Referral", "success", "half", False),
            ("menu_spin_landing", "Spin", "success", "half", False),
            ("menu_reseller_dash", "Reseller", "success", "full", False),
            ("menu_vip_dash", "VIP", "success", "full", True),
        ],
    },
    "style_f": {
        "title": "Style F — Red Alert",
        "links_before": "menu_support", "link_color": "danger", "link_width": "full",
        "rows": [
            ("menu_shop", "🔥 BUY NOW", "danger", "full", False),
            ("menu_add_balance", "💸 Recharge Wallet", "danger", "full", False),
            ("menu_profile", "🧍 Account", "primary", "half", False),
            ("menu_orders", "📦 My Keys", "primary", "half", False),
            ("menu_all_files", "📂 Downloads", "primary", "half", False),
            ("menu_referral", "📣 Refer Friends", "danger", "half", False),
            ("menu_spin_landing", "🎲 Daily Reward", "danger", "half", False),
            ("menu_how_to", "❔ How It Works", "primary", "half", False),
            ("menu_support", "🚨 Emergency Support", "danger", "full", False),
            ("menu_reseller_dash", "⚔️ Reseller Mode", "danger", "full", False),
            ("menu_vip_dash", "👑 VIP Mode", "danger", "full", True),
        ],
    },
    "style_g": {
        "title": "Style G — Ultra Premium",
        "links_before": "menu_support", "link_color": "success", "link_width": "full",
        "layout": "2",
        "rows": [
            ("menu_shop", "✦  ENTER PREMIUM STORE  ✦", "primary", "full", False),
            ("menu_add_balance", "💳  ADD FUNDS", "success", "full", False),
            ("menu_profile", "👤  Profile", "primary", "half", False),
            ("menu_orders", "🧾  My Orders", "primary", "half", False),
            ("menu_all_files", "📥  Downloads", "success", "half", False),
            ("menu_referral", "🎁  Rewards", "success", "half", False),
            ("menu_spin_landing", "🎲  Daily Gift", "success", "half", False),
            ("menu_how_to", "◇  How It Works", "primary", "half", False),
            ("menu_support", "🛟  PREMIUM SUPPORT", "danger", "full", False),
            ("menu_reseller_dash", "♛  Reseller Club", "success", "full", False),
            ("menu_vip_dash", "♛  VIP LOUNGE", "success", "full", False),
        ],
    },
    "style_h": {
        "title": "Style H — Royal Gold ✨",
        "links_before": "menu_support", "link_color": "success", "link_width": "full",
        "layout": "2",
        "rows": [
            ("menu_shop", "👑  ROYAL STORE  👑", "primary", "full", False),
            ("menu_add_balance", "💎  Add Funds", "success", "half", False),
            ("menu_profile", "🪪  My Profile", "success", "half", False),
            ("menu_orders", "📜  Order History", "primary", "half", False),
            ("menu_all_files", "📦  Downloads", "primary", "half", False),
            ("menu_referral", "🎁  Invite & Earn", "success", "half", False),
            ("menu_spin_landing", "🎰  Daily Gift", "success", "half", False),
            ("menu_how_to", "📘  Guide", "primary", "half", False),
            ("menu_support", "⚜️  VIP SUPPORT", "danger", "full", False),
            ("menu_reseller_dash", "💠  Reseller Elite", "success", "full", False),
            ("menu_vip_dash", "🌟  VIP LOUNGE  🌟", "success", "full", False),
        ],
    },
}


def _reset_button_design(menu: str) -> None:
    for _, _, cb in _button_catalog(menu):
        for prefix in (
            "button_color_cb_", "button_width_cb_", "button_label_cb_",
            "button_hidden_cb_", "button_animation_cb_",
        ):
            set_setting(f"{prefix}{cb}", "")
    set_setting(f"button_order_{menu}", "")


def _apply_style_preset(name: str) -> None:
    preset = _STYLE_PRESETS[name]
    _reset_button_design("main")
    link_keys = [f"link_{link['id']}" for link in _custom_links()]
    order: List[str] = []
    for key, label, color, width, hidden in preset["rows"]:
        if key == preset["links_before"]:
            for link_key in link_keys:
                order.append(link_key)
                set_setting(f"button_color_cb_{link_key}", preset["link_color"])
                set_setting(f"button_width_cb_{link_key}", preset["link_width"])
        order.append(key)
        set_setting(f"button_color_cb_{key}", color)
        set_setting(f"button_width_cb_{key}", width)
        set_setting(f"button_label_cb_{key}", label)
        set_setting(f"button_hidden_cb_{key}", "1" if hidden else "")
    if preset.get("layout") in {"1", "2", "3"}:
        set_setting("button_layout_main", preset["layout"])
    _save_button_order("main", order)


async def _designer_reply(target: Any, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


def _menu_preview(menu: str) -> str:
    """Text picture of how the menu rows will look, e.g. [Shop] / [Profile] [Balance]."""
    catalog = {item[0]: item for item in _button_catalog(menu)}
    stubs = []
    for key in _button_order(menu):
        _, label, cb = catalog[key]
        if _button_hidden(cb) and cb != "admin_button_design":
            continue
        stubs.append(InlineKeyboardButton(text=_button_label(cb, label), callback_data=cb[:64]))
    rows = _pack_button_rows(stubs, _button_layout(menu), lambda b: b.callback_data or "")
    return "\n".join("[" + "] [".join(html.escape(b.text) for b in row) + "]" for row in rows) or "(no buttons)"


@dp.callback_query(F.data == "admin_button_design")
async def admin_button_design(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    color = get_setting("button_color", "primary")
    layout = get_setting("button_layout", "2")
    inline_on = get_setting("start_inline_products", "ON").upper() == "ON"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧩 Main Menu — edit buttons", callback_data="admin_design_menu_main", style="primary")],
        [InlineKeyboardButton(text="🛠 Admin Menu — edit buttons", callback_data="admin_design_menu_admin", style="primary")],
        [InlineKeyboardButton(text="🎭 Ready styles (A / F)", callback_data="admin_design_presets", style="success")],
        [InlineKeyboardButton(text="🔗 Link buttons (Payment Proof…)", callback_data="admin_design_links", style="success")],
        [InlineKeyboardButton(
            text=f"🎞 Click animation: {_BUTTON_ANIMATION_LABELS.get(get_setting('button_animation', 'normal'), 'Shimmer')}",
            callback_data="admin_button_animation_menu", style="success")],
        [InlineKeyboardButton(
            text=f"🛍 Products on /start: {'ON 🟢' if inline_on else 'OFF 🔴'}",
            callback_data="admin_design_toggle_inline", style="success" if inline_on else "danger")],
        [InlineKeyboardButton(text=f"🎨 Default colour: {color}", callback_data="admin_button_color_menu", style="primary")],
        [InlineKeyboardButton(text=f"📐 Default layout: {layout} per row", callback_data="admin_button_layout_menu", style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await _designer_reply(
        call,
        "🎨 <b>Bot Button Design</b>\n\n"
        "• <b>Main Menu</b>: har button ka colour, naam, width (full/half), hide aur position badlo.\n"
        "• <b>Ready styles</b>: 6 looks mein se koi bhi ek tap mein lagao.\n"
        "• <b>Animation</b>: global ya har button ka click effect alag set karo.\n"
        "• <b>Link buttons</b>: Payment Proof / YouTube jaise URL buttons banao.\n"
        "• <b>Products on /start</b>: ON = products seedha /start par, OFF = sirf Product Store button.\n\n"
        f"Default colour: <b>{color}</b> · Default layout: <b>{layout} per row</b>",
        kb,
    )


async def _show_button_menu_editor(target: Any, menu: str):
    catalog = {item[0]: item for item in _button_catalog(menu)}
    order = _button_order(menu)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for index, key in enumerate(order):
        _, label, cb = catalog[key]
        animation = _button_animation(cb)
        text = (
            f"{index + 1}. {_COLOR_ICON[_button_color(cb)]} {_button_label(cb, label)}"
            f" · {_button_width(cb)} · 🎞 {_BUTTON_ANIMATION_LABELS[animation]}"
            f"{' · 🙈 hidden' if _button_hidden(cb) else ''}"
        )
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=text[:60], callback_data=f"admin_design_button_{menu}_{index}", style=_button_color(cb))
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="📐 Layout for this menu", callback_data=f"admin_design_layout_{menu}", style="primary"),
        InlineKeyboardButton(text="♻️ Reset design", callback_data=f"admin_design_reset_{menu}", style="danger"),
    ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="danger")])
    await _designer_reply(
        target,
        f"🎨 <b>{'Main' if menu == 'main' else 'Admin'} Menu Designer</b>\n\n"
        "Kisi bhi button par tap karo: colour, naam, width, hide ya position badalne ke liye.\n\n"
        f"<b>Preview:</b>\n<code>{_menu_preview(menu)}</code>",
        kb,
    )


@dp.callback_query(F.data.in_({"admin_design_menu_main", "admin_design_menu_admin"}))
async def admin_design_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    await _show_button_menu_editor(call, call.data.rsplit("_", 1)[1])


def _design_target(data: str, expected_parts: int):
    """Parse admin_design_<action>_<menu>_<index>[_<value>] -> (menu, index, label, key, parts)."""
    parts = data.split("_")
    if len(parts) != expected_parts or parts[3] not in {"main", "admin"}:
        return None
    try:
        index = int(parts[4])
    except (ValueError, TypeError):
        return None
    menu = parts[3]
    order = _button_order(menu)
    if index < 0 or index >= len(order):
        return None
    catalog = {item[0]: item for item in _button_catalog(menu)}
    key = order[index]
    return menu, index, catalog[key][1], catalog[key][2], parts


async def _render_button_screen(target: Any, menu: str, index: int) -> None:
    order = _button_order(menu)
    if index < 0 or index >= len(order):
        return
    catalog = {item[0]: item for item in _button_catalog(menu)}
    _, label, cb = catalog[order[index]]
    shown = _button_label(cb, label)
    color, width, hidden = _button_color(cb), _button_width(cb), _button_hidden(cb)
    animation = _button_animation(cb)

    def mark(active: bool, text: str) -> str:
        return f"✅ {text}" if active else text

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=mark(color == "primary", "🔵 Blue"), callback_data=f"admin_design_color_{menu}_{index}_primary", style="primary"),
         InlineKeyboardButton(text=mark(color == "success", "🟢 Green"), callback_data=f"admin_design_color_{menu}_{index}_success", style="success"),
         InlineKeyboardButton(text=mark(color == "danger", "🔴 Red"), callback_data=f"admin_design_color_{menu}_{index}_danger", style="danger"),
         InlineKeyboardButton(text=mark(color == "secondary", "⚫ Grey"), callback_data=f"admin_design_color_{menu}_{index}_secondary", style="secondary")],
        [InlineKeyboardButton(text=mark(width == "full", "▭ Full row"), callback_data=f"admin_design_width_{menu}_{index}_full", style="primary"),
         InlineKeyboardButton(text=mark(width == "half", "▯▯ Half"), callback_data=f"admin_design_width_{menu}_{index}_half", style="primary"),
         InlineKeyboardButton(text=mark(width == "auto", "Auto"), callback_data=f"admin_design_width_{menu}_{index}_auto", style="primary")],
        [InlineKeyboardButton(text="✏️ Rename", callback_data=f"admin_design_rename_{menu}_{index}", style="primary"),
         InlineKeyboardButton(text="🙈 Hidden — show" if hidden else "👁 Visible — hide", callback_data=f"admin_design_hide_{menu}_{index}", style="danger" if not hidden else "success")],
        [InlineKeyboardButton(text="⬆️ Up", callback_data=f"admin_design_move_{menu}_{index}_up", style="primary"),
         InlineKeyboardButton(text="⬇️ Down", callback_data=f"admin_design_move_{menu}_{index}_down", style="primary")],
         [InlineKeyboardButton(text=f"🎞 Animation: {_BUTTON_ANIMATION_LABELS[animation]}", callback_data=f"admin_design_animation_menu_{menu}_{index}", style="success")],
        [InlineKeyboardButton(text="Back to button list", callback_data=f"admin_design_menu_{menu}", style="danger")],
    ])
    await _designer_reply(
        target,
        f"🧩 <b>{html.escape(shown)}</b>"
        + (f" <i>(default: {html.escape(label)})</i>" if shown != label else "")
        + f"\n\nColour: <b>{_COLOR_ICON[color]} {color}</b>\n"
        f"Width: <b>{_WIDTH_LABEL[width]}</b>\n"
        f"Click animation: <b>🎞 {_BUTTON_ANIMATION_LABELS[animation]}</b>\n"
        f"Position: <b>{index + 1}</b>\n"
        f"Status: <b>{'🙈 hidden' if hidden else '👁 visible'}</b>\n\n"
        "<i>Full = akela poori row | Half = do button ek row mein | Auto = menu layout.</i>",
        kb,
    )


@dp.callback_query(F.data.startswith("admin_design_button_"))
async def admin_design_button(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 5)
    if not info:
        return await call.answer("Button not found.", show_alert=True)
    await _render_button_screen(call, info[0], info[1])


@dp.callback_query(F.data.startswith("admin_design_color_"))
async def admin_design_color(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 6)
    if not info or info[4][5] not in {"primary", "success", "danger", "secondary"}:
        return
    menu, index, _, key, parts = info
    set_setting(f"button_color_cb_{key}", parts[5])
    await call.answer("✅ Colour saved")
    await _render_button_screen(call, menu, index)


@dp.callback_query(F.data.startswith("admin_design_width_"))
async def admin_design_width(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 6)
    if not info or info[4][5] not in {"full", "half", "auto"}:
        return
    menu, index, _, key, parts = info
    set_setting(f"button_width_cb_{key}", parts[5])
    await call.answer("✅ Width saved")
    await _render_button_screen(call, menu, index)


@dp.callback_query(F.data.startswith("admin_design_hide_"))
async def admin_design_hide(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 5)
    if not info:
        return
    menu, index, _, key, _ = info
    if key == "admin_button_design":
        return await call.answer("Ye button hide nahi ho sakta, warna designer band ho jayega.", show_alert=True)
    set_setting(f"button_hidden_cb_{key}", "" if _button_hidden(key) else "1")
    await call.answer("✅ Saved")
    await _render_button_screen(call, menu, index)


@dp.callback_query(F.data.startswith("admin_design_animation_menu_"))
async def admin_design_animation_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(
        call.data.replace("admin_design_animation_menu_", "admin_design_button_", 1),
        5,
    )
    if not info:
        return await call.answer("Button not found.", show_alert=True)
    menu, index, label, key, _ = info
    current = _button_animation(key)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for value, animation_label in _BUTTON_ANIMATION_LABELS.items():
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{'✅ ' if current == value else ''}{animation_label}",
                callback_data=f"admin_design_animation_{menu}_{index}_{value}",
                style="success" if current == value else "primary",
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Back",
            callback_data=f"admin_design_button_{menu}_{index}",
            style="danger",
        )
    ])
    await _designer_reply(
        call,
        f"🎞 <b>Click animation</b>\n\nButton: <b>{html.escape(label)}</b>\n"
        "Is button ke liye alag effect choose karo:",
        kb,
    )

@dp.callback_query(F.data.startswith("admin_design_animation_"))
async def admin_design_animation(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 6)
    if not info or info[4][5] not in _BUTTON_ANIMATION_LABELS:
        return
    menu, index, _, key, parts = info
    set_setting(f"button_animation_cb_{key}", parts[5])
    await call.answer("✅ Animation saved")
    await _render_button_screen(call, menu, index)

@dp.callback_query(F.data.startswith("admin_design_rename_"))
async def admin_design_rename(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    info = _design_target(call.data, 5)
    if not info:
        return
    menu, index, label, _, _ = info
    await state.set_state(AdminStates.wait_button_label)
    await state.update_data(design_menu=menu, design_index=index)
    await call.message.edit_text(
        f"✏️ <b>Rename: {html.escape(label)}</b>\n\n"
        "Naya naam bhejo (max 30 characters, emoji bhi chalega).\n"
        "Default naam wapas lene ke liye <code>-</code> bhejo. Cancel: /cancel",
        parse_mode="HTML",
    )


@dp.message(AdminStates.wait_button_label)
async def admin_design_rename_save(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        return
    data = await state.get_data()
    menu, index = data.get("design_menu"), data.get("design_index")
    text = (message.text or "").strip()
    await state.clear()
    if text.startswith("/"):
        return await message.answer("Cancelled.")
    info = _design_target(f"admin_design_x_{menu}_{index}", 5)
    if not info or not text:
        return await message.answer("❌ Button not found ya naam khali hai.")
    key = info[3]
    set_setting(f"button_label_cb_{key}", "" if text == "-" else text[:30])
    await _render_button_screen(message, menu, index)


@dp.callback_query(F.data == "admin_design_presets")
async def admin_design_presets(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🅰 Style A", callback_data="admin_design_preset_style_a", style="primary")],
        [InlineKeyboardButton(text="🅱 Style B", callback_data="admin_design_preset_style_b", style="success")],
        [InlineKeyboardButton(text="© Style C — Cyber", callback_data="admin_design_preset_style_c", style="primary")],
        [InlineKeyboardButton(text="🅳 Style D — Premium", callback_data="admin_design_preset_style_d", style="success")],
        [InlineKeyboardButton(text="🅴 Style E — Compact", callback_data="admin_design_preset_style_e", style="primary")],
        [InlineKeyboardButton(text="🅵 Style F — Red Alert", callback_data="admin_design_preset_style_f", style="danger")],
        [InlineKeyboardButton(text="✦ Style G — Ultra Premium", callback_data="admin_design_preset_style_g", style="success")],
        [InlineKeyboardButton(text="👑 Style H — Royal Gold", callback_data="admin_design_preset_style_h", style="success")],
        [InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="danger")],
    ])
    await _designer_reply(
        call,
        "🎭 <b>Ready styles</b> (Main Menu)\n\n"
        "<b>Style A</b>\n<code>[Product Store]\n[Reseller Plan]\n[My Profile] [Add Balance]\n"
        "[All History] [Ludo Spin]\n[Tutorial] [link]\n[Support]</code>\n\n"
        "<b>Style B</b>\n<code>[Product Store]\n[My Profile] [Add Balance]\n[All History] [Referral]\n"
        "[Tutorials] [Support]\n[link]\n[Ludo Spin] [Download Files]\n[Reseller Panel]</code>\n\n"
        "<b>Style C-G</b>: Cyber Grid, Premium, Compact, Red Alert aur Ultra Premium layouts.\n\n"
        "<b>Style H — Royal Gold</b>: 👑💎 icons ke saath sabse premium look, VIP/Reseller ke liye highlight kiya hua.\n\n"
        "Style lagane ke baad har button ko apne hisaab se badal sakte ho. "
        "Link buttons (Payment Proof / YouTube) pehle <b>Link buttons</b> se banao.",
        kb,
    )


@dp.callback_query(F.data.startswith("admin_design_preset_"))
async def admin_design_preset_apply(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    name = call.data.removeprefix("admin_design_preset_")
    if name not in _STYLE_PRESETS:
        return
    _apply_style_preset(name)
    await call.answer(f"✅ {_STYLE_PRESETS[name]['title']} applied", show_alert=True)
    await _show_button_menu_editor(call, "main")


@dp.callback_query(F.data.startswith("admin_design_reset_"))
async def admin_design_reset(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    menu = call.data.removeprefix("admin_design_reset_")
    if menu not in {"main", "admin"}:
        return
    _reset_button_design(menu)
    await call.answer("♻️ Design reset to default", show_alert=True)
    await _show_button_menu_editor(call, menu)


@dp.callback_query(F.data == "admin_design_toggle_inline")
async def admin_design_toggle_inline(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    now_on = get_setting("start_inline_products", "ON").upper() == "ON"
    set_setting("start_inline_products", "OFF" if now_on else "ON")
    await call.answer("Products on /start: " + ("OFF" if now_on else "ON"))
    await admin_button_design(call)


async def _show_links_screen(target: Any) -> None:
    links = _custom_links()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ {link['label']}", callback_data=f"admin_design_linkdel_{link['id']}", style="danger")]
        for link in links
    ])
    if len(links) < 8:
        kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Add link button", callback_data="admin_design_linkadd", style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="danger")])
    listing = "\n".join(f"• <b>{html.escape(l['label'])}</b> → {html.escape(l['url'])}" for l in links) or "Abhi koi link button nahi hai."
    await _designer_reply(
        target,
        "🔗 <b>Link buttons</b>\n\n"
        "Ye URL buttons main menu mein dikhte hain (jaise Payment Proof, YouTube Course). "
        "Banane ke baad Main Menu designer mein unka colour/width/position badal sakte ho. "
        "❌ dabao to delete.\n\n" + listing,
        kb,
    )


@dp.callback_query(F.data == "admin_design_links")
async def admin_design_links(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    await _show_links_screen(call)


@dp.callback_query(F.data == "admin_design_linkadd")
async def admin_design_link_add(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await state.set_state(AdminStates.wait_custom_link)
    await call.message.edit_text(
        "➕ <b>New link button</b>\n\nIs format mein bhejo:\n"
        "<code>Payment Proof | https://t.me/yourchannel</code>\n\nCancel: /cancel",
        parse_mode="HTML",
    )


@dp.message(AdminStates.wait_custom_link)
async def admin_design_link_save(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        return
    text = (message.text or "").strip()
    await state.clear()
    if text.startswith("/"):
        return await message.answer("Cancelled.")
    label, _, url = (part.strip() for part in text.partition("|"))
    if not label or not url.startswith(("http://", "https://", "tg://")):
        return await message.answer("❌ Format galat. Aise bhejo: <code>Payment Proof | https://t.me/yourchannel</code>", parse_mode="HTML")
    links = _custom_links()
    if len(links) >= 8:
        return await message.answer("❌ Max 8 link buttons.")
    links.append({"id": secrets.token_hex(3), "label": label[:30], "url": url})
    _save_custom_links(links)
    await _show_links_screen(message)


@dp.callback_query(F.data.startswith("admin_design_linkdel_"))
async def admin_design_link_delete(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    link_id = call.data.removeprefix("admin_design_linkdel_")
    _save_custom_links([link for link in _custom_links() if link["id"] != link_id])
    await call.answer("🗑 Deleted")
    await _show_links_screen(call)


@dp.callback_query(F.data.startswith("admin_design_move_"))
async def admin_design_move(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    parts = call.data.split("_")
    if len(parts) != 6 or parts[3] not in {"main", "admin"}:
        return
    menu, index, direction = parts[3], int(parts[4]), parts[5]
    order = _button_order(menu)
    new_index = index - 1 if direction == "up" else index + 1
    if index < 0 or index >= len(order) or new_index < 0 or new_index >= len(order):
        return await call.answer("Already at the edge.", show_alert=True)
    order[index], order[new_index] = order[new_index], order[index]
    _save_button_order(menu, order)
    await call.answer("Button position updated.", show_alert=True)
    await _show_button_menu_editor(call, menu)


@dp.callback_query(F.data.startswith("admin_design_layout_"))
async def admin_design_layout(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    menu = call.data.rsplit("_", 1)[1]
    if menu not in {"main", "admin"}:
        return
    current = _button_layout(menu)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 per row — large", callback_data=f"admin_set_menu_layout_{menu}_1", style="primary")],
        [InlineKeyboardButton(text="2 per row — balanced", callback_data=f"admin_set_menu_layout_{menu}_2", style="primary")],
        [InlineKeyboardButton(text="3 per row — compact", callback_data=f"admin_set_menu_layout_{menu}_3", style="primary")],
        [InlineKeyboardButton(text=f"Back (current {current})", callback_data=f"admin_design_menu_{menu}", style="danger")],
    ])
    await call.message.edit_text(
        f"📐 <b>{'Main' if menu == 'main' else 'Admin'} Menu Layout</b>\n\n"
        "Choose how many buttons appear in each row.",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_set_menu_layout_"))
async def admin_set_menu_layout(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    parts = call.data.split("_")
    if len(parts) != 6 or parts[4] not in {"main", "admin"} or parts[5] not in {"1", "2", "3"}:
        return
    menu, layout = parts[4], parts[5]
    set_setting(f"button_layout_{menu}", layout)
    await call.answer(f"{menu.title()} menu layout set to {layout}.", show_alert=True)
    await _show_button_menu_editor(call, menu)


@dp.callback_query(F.data == "admin_button_animation_menu")
async def admin_button_animation_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    current = get_setting("button_animation", "normal")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for value, label in _BUTTON_ANIMATION_LABELS.items():
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{'✅ ' if current == value else ''}{label}",
                callback_data=f"admin_set_button_animation_{value}",
                style="success" if current == value else "primary",
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="danger")
    ])
    await call.message.edit_text(
        "🎞 <b>Global button click animation</b>\n\n"
        "Ye default effect un buttons par lagega jinke liye alag animation set nahi hai.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_set_button_animation_"))
async def admin_set_button_animation(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    value = call.data.removeprefix("admin_set_button_animation_")
    if value not in _BUTTON_ANIMATION_LABELS:
        return
    set_setting("button_animation", value)
    await call.answer(f"✅ Global animation: {_BUTTON_ANIMATION_LABELS[value]}", show_alert=True)
    await admin_button_design(call)


@dp.callback_query(F.data == "admin_button_color_menu")
async def admin_button_color_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Blue / Primary", callback_data="admin_set_button_color_primary", style="primary")],
        [InlineKeyboardButton(text="🟢 Green / Success", callback_data="admin_set_button_color_success", style="success")],
        [InlineKeyboardButton(text="🔴 Red / Danger", callback_data="admin_set_button_color_danger", style="danger")],
        [InlineKeyboardButton(text="⚫ Grey / Secondary", callback_data="admin_set_button_color_secondary", style="secondary")],
        [InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="primary")],
    ])
    await call.message.edit_text("🎨 <b>Select button colour</b>", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin_set_button_color_"))
async def admin_set_button_color(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    color = call.data.rsplit("_", 1)[1]
    if color not in {"primary", "success", "danger", "secondary"}:
        return
    set_setting("button_color", color)
    await call.answer(f"Button colour set to {color}.", show_alert=True)
    await admin_button_design(call)


@dp.callback_query(F.data == "admin_button_layout_menu")
async def admin_button_layout_menu(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 per row — large", callback_data="admin_set_button_layout_1", style="primary")],
        [InlineKeyboardButton(text="2 per row — balanced", callback_data="admin_set_button_layout_2", style="primary")],
        [InlineKeyboardButton(text="3 per row — compact", callback_data="admin_set_button_layout_3", style="primary")],
        [InlineKeyboardButton(text="Back", callback_data="admin_button_design", style="danger")],
    ])
    await call.message.edit_text("📐 <b>Select button layout</b>", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin_set_button_layout_"))
async def admin_set_button_layout(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return
    layout = call.data.rsplit("_", 1)[1]
    if layout not in {"1", "2", "3"}:
        return
    set_setting("button_layout", layout)
    await call.answer(f"Button layout set to {layout} per row.", show_alert=True)
    await admin_button_design(call)


@dp.callback_query(F.data == "admin_quick_add_keys")
async def admin_quick_add_keys(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    rows = db_query(
        "SELECT id, name, category, panel_name, COALESCE(stock, 0) FROM products ORDER BY name COLLATE NOCASE",
        fetchall=True,
        commit=False,
    ) or []
    if not rows:
        return await call.answer("Add a product first.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for row in rows:
        label = f"{str(row[1])[:32]} — {int(row[4] or 0)} keys"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label, callback_data=f"admin_quick_keys_{int(row[0])}", style="success")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")])
    await call.message.edit_text(
        "🔑 <b>Add keys to an existing product</b>\n\n"
        "Product name is already shown below—tap it, then paste one key per line.",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("admin_quick_keys_"))
async def admin_quick_keys_start(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    product_id = int(call.data.rsplit("_", 1)[1])
    row = db_query("SELECT name FROM products WHERE id=?", (product_id,), fetchone=True, commit=False)
    if not row:
        return await call.answer("Product not found.", show_alert=True)
    await state.update_data(edit_p_id=product_id)
    await call.message.edit_text(
        f"🔑 <b>{html.escape(str(row[0]))}</b>\n\n"
        "Paste new keys, one per line. They will be added to this product; "
        "you do not need to type its name again.",
        reply_markup=admin_back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_add_keys)

QUICK_API_FIELD_HINT = (
    "⚡ <b>Quick Add API Key</b> — panel: <b>{panel}</b>\n\n"
    "Category / Payload / Device-limit is copied automatically from this panel's "
    "last product, so you only need to send <b>6 lines</b>, in this exact order:\n\n"
    "<code>Package Name\n"
    "Validity Text\n"
    "Price\n"
    "Reseller Price\n"
    "PID\n"
    "Hours</code>\n\n"
    "<b>Example:</b>\n"
    "<code>1 Day\n"
    "24 Hours\n"
    "89\n"
    "60\n"
    "abcd123pid\n"
    "1 Day</code>\n\n"
    "Sab 6 lines ek hi message mein bhejo (copy-paste karke bhi bhej sakte ho)."
)

@dp.callback_query(F.data == "admin_quick_add_api")
async def admin_quick_add_api(call: CallbackQuery):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    rows = db_query(
        "SELECT DISTINCT category, panel_name FROM products "
        "WHERE external_enabled=1 AND panel_name != '' ORDER BY category COLLATE NOCASE, panel_name COLLATE NOCASE",
        fetchall=True, commit=False,
    ) or []
    if not rows:
        return await call.answer(
            "⚠️ Koi API panel nahi mila abhi. Pehle ➕ Add Product se ek API product banao, "
            "uske baad yahan se naye durations/PID jaldi add kar paoge.",
            show_alert=True,
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for cat, panel in rows:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"{panel} [{cat}]", callback_data=f"qapi_pnl_{cat[:30]}_{panel[:30]}", style="success")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")])
    await call.message.edit_text(
        "⚡ <b>Quick Add API Key</b>\n\n"
        "Jis panel mein naya duration/PID key add karna hai, usko choose karo. "
        "Category, payload link aur device-limit dubara nahi bharne padenge.",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )

@dp.callback_query(F.data.startswith("qapi_pnl_"))
async def admin_quick_add_api_panel_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    parts = call.data.split("qapi_pnl_", 1)[1].split("_", 1)
    if len(parts) != 2:
        return await call.answer("Invalid selection.", show_alert=True)
    await _qapi_show_duration_screen(call, state, parts[0], parts[1])

async def _qapi_show_duration_screen(call: CallbackQuery, state: FSMContext, category: str, panel_name: str):
    ref = db_query(
        "SELECT category, panel_name, apk_link, device_limit FROM products "
        "WHERE category LIKE ? AND panel_name LIKE ? AND external_enabled=1 "
        "ORDER BY id DESC LIMIT 1",
        (category + '%', panel_name + '%'), fetchone=True, commit=False,
    )
    if not ref:
        return await call.answer("This panel no longer has an API product to copy from.", show_alert=True)
    await state.update_data(qa_cat=ref[0], qa_panel=ref[1], qa_apk=ref[2] or "", qa_device=ref[3] or "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="6 Hours", callback_data="qapi_dur_6 Hours", style="success"),
            InlineKeyboardButton(text="12 Hours", callback_data="qapi_dur_12 Hours", style="success"),
        ],
        [
            InlineKeyboardButton(text="1 Day", callback_data="qapi_dur_1 Day", style="success"),
            InlineKeyboardButton(text="7 Days", callback_data="qapi_dur_7 Days", style="success"),
        ],
        [
            InlineKeyboardButton(text="30 Days", callback_data="qapi_dur_30 Days", style="success"),
            InlineKeyboardButton(text="✏️ Custom Duration", callback_data="qapi_dur_custom", style="primary"),
        ],
        [InlineKeyboardButton(text="💰 Profit Settings (duration-wise)", callback_data="qapi_pf_menu", style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await call.message.edit_text(
        f"⚡ <b>Quick Add API Key</b> — panel: <b>{html.escape(str(ref[1]))}</b>\n\n"
        "Duration choose karo (button se) — package name aur validity text "
        "automatically usi se set ho jayenge:",
        reply_markup=apply_button_theme(kb),
        parse_mode="HTML",
    )

@dp.callback_query(F.data.startswith("qapi_dur_"))
async def admin_quick_add_api_duration_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    choice = call.data.split("qapi_dur_", 1)[1]
    data = await state.get_data()
    if not data.get('qa_panel'):
        return await call.answer("Session expired, panel dobara choose karo.", show_alert=True)
    if choice == "custom":
        await call.message.edit_text(
            "✏️ Custom duration bhejo — <b>2 lines</b> mein:\n\n"
            "<code>Package Name\nValidity/Hours Text</code>\n\n"
            "<b>Example:</b>\n<code>3 Days\n3 Days</code>",
            reply_markup=admin_back_kb(), parse_mode="HTML",
        )
        await state.set_state(AdminStates.wait_for_quick_api_custom_duration)
        return
    # Preset: package name and validity text are both the button's label
    # (e.g. "1 Day"), and the same string is also passed to
    # normalize_api_duration() for the API duration - matches the
    # convention already used by every existing product.
    await state.update_data(qa_name=choice, qa_validity=choice, qa_hours=choice)
    await _send_quick_api_price_pid_prompt(call.message, state, edit=True)
    await state.set_state(AdminStates.wait_for_quick_api_price_pid)

QAPI_PRESET_DURATIONS = ["6 Hours", "12 Hours", "1 Day", "7 Days", "30 Days"]
QAPI_DEFAULT_PROFIT_PUBLIC = 10.0
QAPI_DEFAULT_PROFIT_RESELLER = 5.0

def _qapi_dur_key(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").strip().lower())

def _qapi_get_profit(duration_label: str):
    """Returns (public_profit, reseller_profit) in rupees for this duration."""
    key = _qapi_dur_key(duration_label)
    try:
        pub = float(get_setting(f"quick_api_profit_public_{key}", str(QAPI_DEFAULT_PROFIT_PUBLIC)))
    except (TypeError, ValueError):
        pub = QAPI_DEFAULT_PROFIT_PUBLIC
    try:
        res = float(get_setting(f"quick_api_profit_reseller_{key}", str(QAPI_DEFAULT_PROFIT_RESELLER)))
    except (TypeError, ValueError):
        res = QAPI_DEFAULT_PROFIT_RESELLER
    return pub, res

def _qapi_set_profit(duration_label: str, pub: float, res: float) -> None:
    key = _qapi_dur_key(duration_label)
    set_setting(f"quick_api_profit_public_{key}", str(pub))
    set_setting(f"quick_api_profit_reseller_{key}", str(res))

async def _send_quick_api_price_pid_prompt(msg_or_call, state: FSMContext, edit: bool):
    data = await state.get_data()
    dur_label = data.get('qa_name', '')
    pub_p, res_p = _qapi_get_profit(dur_label)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💰 Change Profit ({dur_label})", callback_data="qapi_pf_cur", style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    text = (
        f"⚡ <b>{html.escape(str(dur_label))}</b> — panel: <b>{html.escape(str(data.get('qa_panel','')))}</b>\n\n"
        f"Ab bas <b>2 lines</b> bhejo:\n\n<code>Real Price\nPID</code>\n\n"
        f"<b>Example:</b>\n<code>89\nabcd123pid</code>\n\n"
        f"Real Price = jitne me tum kharidte ho. Profit automatically add hoga:\n"
        f"👤 Public: <b>+₹{pub_p:g}</b>\n"
        f"🤝 Reseller: <b>+₹{res_p:g}</b>\n\n"
        f"Profit badalne ke liye neeche button dabao."
    )
    if edit:
        await msg_or_call.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg_or_call.answer(text, reply_markup=kb, parse_mode="HTML")

async def _qapi_show_profit_menu(msg: Message, edit: bool = True):
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    lines = []
    for i, dur in enumerate(QAPI_PRESET_DURATIONS):
        pub_p, res_p = _qapi_get_profit(dur)
        lines.append(f"• <b>{dur}</b> — Public +₹{pub_p:g} | Reseller +₹{res_p:g}")
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✏️ {dur}: ₹{pub_p:g} / ₹{res_p:g}", callback_data=f"qapi_pf_set_{i}", style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Back", callback_data="qapi_pf_back", style="danger")])
    text = (
        "💰 <b>Quick Add — Profit Settings</b>\n\n"
        "Har duration ke liye alag profit set karo (real price ke upar add hoga):\n\n"
        + "\n".join(lines) +
        "\n\nDefault: Public +₹10, Reseller +₹5. Kisi duration pe profit badalne ke liye uska button dabao."
    )
    if edit:
        await msg.edit_text(text, reply_markup=apply_button_theme(kb), parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=apply_button_theme(kb), parse_mode="HTML")

@dp.callback_query(F.data == "qapi_pf_menu")
async def admin_quick_add_api_profit_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return await call.answer("⛔ Admin only.", show_alert=True)
    await call.answer()
    await state.update_data(qa_pf_return="menu")
    await _qapi_show_profit_menu(call.message, edit=True)

@dp.callback_query(F.data == "qapi_pf_back")
async def admin_quick_add_api_profit_back(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    await call.answer()
    data = await state.get_data()
    if data.get('qa_cat') and data.get('qa_panel'):
        return await _qapi_show_duration_screen(call, state, data['qa_cat'], data['qa_panel'])
    await admin_quick_add_api(call)

async def _qapi_ask_profit(msg: Message, state: FSMContext, dur_label: str):
    pub_p, res_p = _qapi_get_profit(dur_label)
    await state.update_data(qa_pf_dur=dur_label)
    await msg.edit_text(
        f"💰 <b>{html.escape(dur_label)}</b> ka profit set karo — <b>2 lines</b> me:\n\n"
        f"<code>Public Profit\nReseller Profit</code>\n\n"
        f"<b>Example:</b>\n<code>10\n5</code>\n\n"
        f"Abhi: Public +₹{pub_p:g} | Reseller +₹{res_p:g}\n"
        f"(Profit nahi chahiye to 0 bhejo.)",
        reply_markup=admin_back_kb(), parse_mode="HTML",
    )
    await state.set_state(AdminStates.wait_for_quick_api_profit)

@dp.callback_query(F.data.startswith("qapi_pf_set_"))
async def admin_quick_add_api_profit_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    try:
        dur = QAPI_PRESET_DURATIONS[int(call.data.split("qapi_pf_set_", 1)[1])]
    except (ValueError, IndexError):
        return await call.answer("Invalid duration.", show_alert=True)
    await call.answer()
    await state.update_data(qa_pf_return="menu")
    await _qapi_ask_profit(call.message, state, dur)

@dp.callback_query(F.data == "qapi_pf_cur")
async def admin_quick_add_api_profit_current(call: CallbackQuery, state: FSMContext):
    if not is_admin_user(call.from_user.id):
        return
    data = await state.get_data()
    if not data.get('qa_name'):
        return await call.answer("Session expired, panel dobara choose karo.", show_alert=True)
    await call.answer()
    await state.update_data(qa_pf_return="prompt")
    await _qapi_ask_profit(call.message, state, data['qa_name'])

@dp.message(AdminStates.wait_for_quick_api_profit)
async def admin_quick_add_api_profit_save(m: Message, state: FSMContext):
    lines = [ln.strip() for ln in (m.text or "").split("\n") if ln.strip()]
    if len(lines) != 2:
        return await m.answer("❌ Exactly 2 lines chahiye: Public Profit, phir Reseller Profit (e.g. 10 aur 5).")
    try:
        pub_p, res_p = float(lines[0]), float(lines[1])
        if pub_p < 0 or res_p < 0:
            raise ValueError
    except (ValueError, TypeError):
        return await m.answer("❌ Dono profit numbers (0 ya usse zyada) hone chahiye.")
    data = await state.get_data()
    dur = data.get('qa_pf_dur')
    if not dur:
        await state.clear()
        return await m.answer("❌ Session expired. Dobara Quick Add API Key se try karo.")
    _qapi_set_profit(dur, pub_p, res_p)
    await m.answer(f"✅ <b>{html.escape(dur)}</b>: Public +₹{pub_p:g} | Reseller +₹{res_p:g} set ho gaya.", parse_mode="HTML")
    if data.get('qa_pf_return') == "prompt" and data.get('qa_name'):
        await _send_quick_api_price_pid_prompt(m, state, edit=False)
        await state.set_state(AdminStates.wait_for_quick_api_price_pid)
    else:
        await _qapi_show_profit_menu(m, edit=False)
        await state.set_state(None)

@dp.message(AdminStates.wait_for_quick_api_custom_duration)
async def admin_quick_add_api_custom_duration(m: Message, state: FSMContext):
    lines = [ln.strip() for ln in (m.text or "").split("\n") if ln.strip()]
    if len(lines) != 2:
        return await m.answer("❌ Exactly 2 lines chahiye (Package Name, Validity/Hours Text). Dubara bhejo.")
    name, validity = lines
    await state.update_data(qa_name=name, qa_validity=validity, qa_hours=validity)
    await _send_quick_api_price_pid_prompt(m, state, edit=False)
    await state.set_state(AdminStates.wait_for_quick_api_price_pid)

@dp.message(AdminStates.wait_for_quick_api_price_pid)
async def admin_quick_add_api_save_fast(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data.get('qa_panel')
    if not panel_name:
        await state.clear()
        return
    lines = [ln.strip() for ln in (m.text or "").split("\n") if ln.strip()]
    if len(lines) != 2:
        return await m.answer("❌ Exactly 2 lines chahiye: Price, phir PID. Dubara bhejo.")
    price_raw, pid = lines
    try:
        price = float(price_raw)
    except (ValueError, TypeError):
        return await m.answer("❌ Price ek number hona chahiye (line 1).")
    if not pid:
        return await m.answer("❌ PID (line 2) empty nahi ho sakta.")
    cost_price = price
    pub_profit, res_profit = _qapi_get_profit(data.get('qa_name', ''))
    price = round(cost_price + pub_profit, 2)
    reseller_price = round(cost_price + res_profit, 2)
    api_duration = normalize_api_duration(data.get('qa_hours', ''))
    if not api_duration:
        return await m.answer("❌ Duration set nahi ho payi, dubara panel choose karke try karo.")

    conn = sqlite3.connect(DB_PATH, timeout=20)
    c = conn.cursor()
    c.execute("""INSERT INTO products
        (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
         external_enabled, external_product_id, requires_android_id, external_duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['qa_cat'], data['qa_panel'], data['qa_name'], price, reseller_price, 1,
         data['qa_apk'], data['qa_validity'], data['qa_device'], 1, pid, 0, api_duration))
    prod_id = c.lastrowid
    conn.commit(); conn.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Another — Same Panel", callback_data=f"qapi_pnl_{data['qa_cat'][:30]}_{panel_name[:30]}", style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", style="danger")],
    ])
    await m.answer(
        f"✅ <b>New API key added!</b>\n\n"
        f"Panel: <b>{html.escape(panel_name)}</b>\n"
        f"Package: <code>{html.escape(data['qa_name'])}</code>\n"
        f"Real Price: ₹{cost_price:g}\n"
        f"Public: ₹{price:g} (+₹{pub_profit:g})  |  Reseller: ₹{reseller_price:g} (+₹{res_profit:g})\n"
        f"PID: <code>{html.escape(pid)}</code>\n"
        f"API Duration: <code>{api_duration}</code>\n"
        f"Product ID: <code>{prod_id}</code>",
        reply_markup=kb, parse_mode="HTML",
    )
    await state.clear()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("System shutting down gracefully. Goodbye.")
