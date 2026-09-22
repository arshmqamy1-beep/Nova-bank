# -*- coding: utf-8 -*-
"""
Nova - ربات بانکداری شبیه‌سازی‌شده و آزمایشی برای Soroush / Soroush Plus
ساخته‌شده برای کتابخانه‌ی soroush-bot==1.0.0

*** هشدار مهم ***
این ربات صرفاً یک شبیه‌سازی آموزشی/آزمایشی است و به هیچ بانک واقعی متصل نیست.
تمام مبالغ، کارت‌ها و تراکنش‌ها ساختگی هستند.
"""

import os
import re
import sqlite3
import random
import string
import hashlib
import logging
import traceback
import asyncio
from datetime import datetime

# ---------------------------------------------------------------------------
# تنظیمات ثابت (Placeholder ها را اینجا پر کن)
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("70040318:AGssR4xiJHyLiKHcbKtaIkvoDjaBr6GJcNs")
PAYMENT_CARD = "PUT_YOUR_PAYMENT_CARD_HERE"
ADMIN_IDS = {64682132}

SUPPORT_USERNAME = "@korosh_rag"
BROADCAST_SIGNATURE = "━━━━━━━━━━━━━━\nARASH MAGHAMI"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova_errors.log")

COIN_MIN = 10
COIN_MAX = 500
COIN_STEP = 10
TOMAN_PER_10_COINS = 20000

# ---------------------------------------------------------------------------
# لاگ‌گیری
# ---------------------------------------------------------------------------

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("nova")
logger.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------------------
# بارگذاری تطبیقی کتابخانه‌ی soroush_bot
# ---------------------------------------------------------------------------

import soroush_bot as _sb  # noqa: E402

Application = _sb.Application
CommandHandler = _sb.CommandHandler
MessageHandler = _sb.MessageHandler
CallbackQueryHandler = _sb.CallbackQueryHandler

try:
    filters = _sb.filters
except AttributeError:
    from soroush_bot import filters  # noqa: E402


def _resolve_attr(module, candidates, fallback_always_true=True):
    for name in candidates:
        if hasattr(module, name):
            return getattr(module, name)

    logger.warning(
        "هیچ‌کدام از این نام‌ها در ماژول filters پیدا نشد: %s -- از فیلتر جایگزین (Allow-All) استفاده می‌شود.",
        candidates,
    )

    if not fallback_always_true:
        return None

    class _AllowAllFilter:
        def __and__(self, other):
            return self

        def __rand__(self, other):
            return self

        def __or__(self, other):
            return self

        def __ror__(self, other):
            return self

        def __invert__(self):
            return self

        def check_update(self, update):
            return True

        def filter(self, message):
            return True

        def __call__(self, *a, **kw):
            return True

    return _AllowAllFilter()


TEXT_FILTER = _resolve_attr(filters, ["TEXT", "Text", "ALL_TEXT"])
PHOTO_FILTER = _resolve_attr(filters, ["PHOTO", "Photo"])
COMMAND_FILTER = _resolve_attr(filters, ["COMMAND", "Command"], fallback_always_true=False)

if COMMAND_FILTER is not None:
    try:
        TEXT_ONLY_FILTER = TEXT_FILTER & ~COMMAND_FILTER
    except Exception:
        TEXT_ONLY_FILTER = TEXT_FILTER
else:
    TEXT_ONLY_FILTER = TEXT_FILTER

InlineKeyboardButton = None
InlineKeyboardMarkup = None
ReplyKeyboardMarkup = None

for _mod in (_sb, None):
    try:
        if _mod is not None:
            InlineKeyboardButton = _mod.InlineKeyboardButton
            InlineKeyboardMarkup = _mod.InlineKeyboardMarkup
            ReplyKeyboardMarkup = _mod.ReplyKeyboardMarkup
            break
    except AttributeError:
        continue

if InlineKeyboardButton is None:
    try:
        from soroush_bot.types import (
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            ReplyKeyboardMarkup,
        )
    except Exception:
        logger.warning(
            "کلاس‌های کیبورد (InlineKeyboardButton/Markup, ReplyKeyboardMarkup) پیدا نشدند. "
            "بخش‌های مربوط به دکمه ممکن است کار نکنند تا وقتی مسیر واقعی import مشخص شود."
        )


# ---------------------------------------------------------------------------
# دیتابیس: ساخت و migration
# ---------------------------------------------------------------------------

REQUIRED_TABLES = {
    "users": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "numeric_id": "INTEGER UNIQUE",
        "username": "TEXT",
        "full_name": "TEXT",
        "phone": "TEXT",
        "password_hash": "TEXT",
        "referral_code": "TEXT UNIQUE",
        "referred_by": "TEXT",
        "coins": "INTEGER DEFAULT 0",
        "cash": "INTEGER DEFAULT 0",
        "card_number": "TEXT UNIQUE",
        "card_password_hash": "TEXT",
        "cvv2_hash": "TEXT",
        "password_plain": "TEXT",
        "cvv2_plain": "TEXT",
        "blocked": "INTEGER DEFAULT 0",
        "reg_date": "TEXT",
    },
    "transactions": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "user_id": "INTEGER",
        "source": "TEXT",
        "dest": "TEXT",
        "amount": "INTEGER",
        "unit": "TEXT",
        "status": "TEXT",
        "ttype": "TEXT",
        "date": "TEXT",
    },
    "requests": {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "request_number": "TEXT UNIQUE",
        "user_id": "INTEGER",
        "full_name": "TEXT",
        "username": "TEXT",
        "req_type": "TEXT",
        "amount": "INTEGER",
        "coin_count": "INTEGER",
        "payment_method": "TEXT",
        "receipt": "TEXT",
        "date": "TEXT",
        "status": "TEXT",
    },
    "settings": {
        "key": "TEXT PRIMARY KEY",
        "value": "TEXT",
    },
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema():
    conn = get_conn()
    cur = conn.cursor()
    try:
        for table, columns in REQUIRED_TABLES.items():
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            exists = cur.fetchone() is not None

            if not exists:
                cols_sql = ", ".join(f'"{c}" {t}' for c, t in columns.items())
                cur.execute(f'CREATE TABLE "{table}" ({cols_sql})')
                logger.info("جدول %s ساخته شد.", table)
                continue

            cur.execute(f'PRAGMA table_info("{table}")')
            existing_cols = {row["name"] for row in cur.fetchall()}
            for col, col_type in columns.items():
                if col in existing_cols:
                    continue
                if "PRIMARY KEY" in col_type:
                    continue
                safe_type = col_type.replace("UNIQUE", "").strip()
                try:
                    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {safe_type}')
                    logger.info("ستون %s.%s اضافه شد (migration).", table, col)
                except sqlite3.OperationalError as e:
                    logger.warning("خطا در افزودن ستون %s.%s: %s", table, col, e)

        conn.commit()

        cur.execute("SELECT value FROM settings WHERE key='maintenance'")
        if cur.fetchone() is None:
            cur.execute("INSERT INTO settings(key, value) VALUES ('maintenance','0')")
            conn.commit()
    finally:
        conn.close()


def get_setting(key, default=None):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def is_maintenance():
    return get_setting("maintenance", "0") == "1"


# ---------------------------------------------------------------------------
# ابزارهای کمکی
# ---------------------------------------------------------------------------

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(("nova_v1_salt::" + str(secret)).encode("utf-8")).hexdigest()


def check_secret(secret: str, hashed: str) -> bool:
    if not hashed:
        return False
    return hash_secret(secret) == hashed


def gen_unique(conn, table, column, generator):
    for _ in range(200):
        value = generator()
        row = conn.execute(
            f'SELECT 1 FROM "{table}" WHERE "{column}"=?', (value,)
        ).fetchone()
        if row is None:
            return value
    raise RuntimeError(f"امکان تولید مقدار یکتا برای {table}.{column} وجود نداشت.")


def gen_card_number(conn):
    return gen_unique(
        conn, "users", "card_number",
        lambda: "11" + "".join(random.choices(string.digits, k=9)),
    )


def gen_referral_code(conn):
    return gen_unique(
        conn, "users", "referral_code",
        lambda: "".join(random.choices(string.digits, k=5)),
    )


def gen_request_number(conn):
    return gen_unique(
        conn, "requests", "request_number",
        lambda: "NV" + "".join(random.choices(string.digits, k=6)),
    )


def mask_card(card_number: str) -> str:
    if not card_number or len(card_number) < 6:
        return "•" * 11
    return card_number[:3] + "•" * 5 + card_number[-3:]


def fmt_amount(amount) -> str:
    try:
        return f"{int(amount):,}"
    except Exception:
        return str(amount)


def record_transaction(conn, user_id, source, dest, amount, unit, status, ttype):
    conn.execute(
        "INSERT INTO transactions(user_id, source, dest, amount, unit, status, ttype, date) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_id, source, dest, amount, unit, status, ttype, now_str()),
    )


def get_user_by_numeric_id(conn, numeric_id):
    return conn.execute(
        "SELECT * FROM users WHERE numeric_id=?", (numeric_id,)
    ).fetchone()


def get_user_by_card(conn, card_number):
    return conn.execute(
        "SELECT * FROM users WHERE card_number=?", (card_number,)
    ).fetchone()


def is_admin(numeric_id) -> bool:
    return numeric_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# مدیریت state چندمرحله‌ای
# ---------------------------------------------------------------------------

STATE = {}


def set_state(uid, step, **data):
    d = {"step": step}
    d.update(data)
    STATE[uid] = d


def carry(st):
    d = dict(st)
    d.pop("step", None)
    return d


def get_state(uid):
    return STATE.get(uid)


def clear_state(uid):
    STATE.pop(uid, None)


# ---------------------------------------------------------------------------
# دسترسی تطبیقی به فیلدهای آپدیت
# ---------------------------------------------------------------------------

def extract_user(update):
    user = getattr(update, "effective_user", None)
    if user is None:
        msg = getattr(update, "message", None)
        if msg is not None:
            user = getattr(msg, "from_user", None)
    if user is None:
        cq = getattr(update, "callback_query", None)
        if cq is not None:
            user = getattr(cq, "from_user", None)
    return user


def extract_uid(update):
    user = extract_user(update)
    return getattr(user, "id", None) if user else None


def extract_username(update):
    user = extract_user(update)
    uname = getattr(user, "username", None) if user else None
    return uname or ""


def extract_full_name(update):
    user = extract_user(update)
    if not user:
        return ""
    full = getattr(user, "full_name", None)
    if full:
        return full
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    return (first + " " + last).strip()


def extract_text(update):
    msg = getattr(update, "message", None)
    return getattr(msg, "text", None) if msg else None


def extract_photo_file_id(update):
    msg = getattr(update, "message", None)
    if not msg:
        return None
    photo = getattr(msg, "photo", None)
    if not photo:
        return None
    if isinstance(photo, (list, tuple)):
        if not photo:
            return None
        item = photo[-1]
    else:
        item = photo
    return getattr(item, "file_id", None) or getattr(item, "id", None) or str(item)


async def safe_reply(update, text, reply_markup=None):
    try:
        msg = getattr(update, "message", None)
        if msg is not None and hasattr(msg, "reply_text"):
            if reply_markup is not None:
                return await msg.reply_text(text, reply_markup=reply_markup)
            return await msg.reply_text(text)

        cq = getattr(update, "callback_query", None)
        if cq is not None:
            cmsg = getattr(cq, "message", None)
            if cmsg is not None and hasattr(cmsg, "reply_text"):
                if reply_markup is not None:
                    return await cmsg.reply_text(text, reply_markup=reply_markup)
                return await cmsg.reply_text(text)
    except Exception:
        logger.exception("خطا در ارسال پاسخ (safe_reply)")
    return None


async def safe_send(context, uid, text, reply_markup=None):
    try:
        bot = getattr(context, "bot", None)
        if bot is None:
            logger.warning("context.bot در دسترس نیست؛ ارسال پیام به %s انجام نشد.", uid)
            return
        if reply_markup is not None:
            await bot.send_message(chat_id=uid, text=text, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id=uid, text=text)
    except Exception:
        logger.exception("خطا در ارسال پیام به کاربر %s", uid)


async def safe_answer_callback(update, text=None):
    try:
        cq = getattr(update, "callback_query", None)
        if cq is not None and hasattr(cq, "answer"):
            if text:
                await cq.answer(text)
            else:
                await cq.answer()
    except Exception:
        logger.exception("خطا در answer callback_query")


def build_inline(rows):
    """rows: لیستی از لیست‌های (متن, callback_data)"""
    if InlineKeyboardMarkup is None or InlineKeyboardButton is None:
        return None
    kb = [[InlineKeyboardButton(text=t, callback_data=cd) for (t, cd) in row] for row in rows]
    return InlineKeyboardMarkup(kb)


def build_callback_handler(callback):
    attempts = [
        lambda: CallbackQueryHandler(callback=callback),
        lambda: CallbackQueryHandler(callback),
        lambda: CallbackQueryHandler(pattern=None, callback=callback),
        lambda: CallbackQueryHandler(None, callback),
    ]
    last_err = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as e:
            last_err = e
            continue
    logger.error("ساخت CallbackQueryHandler با هیچ ترتیبی از آرگومان‌ها موفق نشد: %s", last_err)
    raise last_err


def build_reply(rows, resize=True):
    if ReplyKeyboardMarkup is None:
        return None
    try:
        return ReplyKeyboardMarkup(rows, resize_keyboard=resize)
    except TypeError:
        try:
            return ReplyKeyboardMarkup(rows)
        except Exception:
            logger.exception("ساخت ReplyKeyboardMarkup ناموفق بود.")
            return None


# ---------------------------------------------------------------------------
# منوها
# ---------------------------------------------------------------------------

MAIN_MENU_ROWS = [
    ["👤 پروفایل", "🏦 اطلاعات بانک"],
    ["🎁 کد معرف", "🎧 پشتیبانی"],
    ["🔐 تغییر رمز عبور", "📋 تراکنش‌ها"],
    ["💳 کارت به کارت", "🪙 خرید سکه"],
    ["💸 انتقال پول"],
]


def main_menu():
    return build_reply(MAIN_MENU_ROWS)


def admin_menu():
    toggle_label = "🟢 روشن کردن بات" if is_maintenance() else "🔴 خاموشی بات"
    rows = [
        ["📋 درخواست ها", "📢 پیام همگانی"],
        ["📊 آمار کاربران", "➖ کاهش پول"],
        ["🏦 حساب های بانکی", "🎁 هدیه به کاربر"],
        [toggle_label, "🚫 مسدود سازی حساب"],
        ["🔓 آزاد سازی حساب"],
        ["پنل کاربری"],
    ]
    return build_reply(rows)


SUPPORT_TEXT = f"🎧 پشتیبانی Nova\n\nبرای ارتباط با پشتیبانی:\n{SUPPORT_USERNAME}"


def support_inline_kb():
    return build_inline([[("🎧 ارتباط با پشتیبانی", "open_support")]])


# ---------------------------------------------------------------------------
# منطق ثبت‌نام
# ---------------------------------------------------------------------------

PHONE_RE = re.compile(r"^\+?\d{8,15}$")


async def cmd_start(update, context):
    try:
        uid = extract_uid(update)
        if uid is None:
            return
        conn = get_conn()
        try:
            user = get_user_by_numeric_id(conn, uid)
        finally:
            conn.close()

        if user is not None:
            clear_state(uid)
            if user["blocked"]:
                await safe_reply(
                    update,
                    "🚫 حساب شما مسدود شده است.\n\n"
                    "حساب شما به دلیل نقص قوانین مسدود شده است.\n"
                    "در صورتی که نسبت به این تصمیم اعتراض دارید، لطفاً با پشتیبانی Nova در ارتباط باشید.",
                )
                await safe_reply(update, SUPPORT_TEXT, reply_markup=support_inline_kb())
                return
            await safe_reply(update, "خوش آمدید 🌟", reply_markup=main_menu())
            return

        set_state(uid, "reg_phone")
        await safe_reply(
            update,
            "سلام 👋 به بانک داری هوشمند Nova خوش آمدید لطفا شماره موبایل خود را وارد نمایید ✨️",
        )
    except Exception:
        logger.exception("خطا در cmd_start")
        await safe_reply(update, "یک خطای موقت رخ داد. لطفاً دوباره تلاش کنید.")


async def handle_registration(update, context, uid, st, text):
    step = st["step"]

    if step == "reg_phone":
        if not PHONE_RE.match(text.replace(" ", "")):
            await safe_reply(
                update,
                "لطفا شماره خود را با ملیت + ثبت کنید\n\nمثال:\n+98 9157764480",
            )
            return
        st["phone"] = text.strip()
        set_state(uid, "reg_name", **carry(st))
        await safe_reply(update, "لطفا نام و نام خانوادگی خود را وارد کنید")
        return

    if step == "reg_name":
        if not text.strip():
            await safe_reply(update, "لطفا نام و نام خانوادگی خود را وارد کنید")
            return
        st["full_name"] = text.strip()
        set_state(uid, "reg_password", **carry(st))
        await safe_reply(update, "لطفا یک رمز عبور ۴ رقمی انتخاب کنید")
        return

    if step == "reg_password":
        if not (text.isdigit() and len(text) == 4):
            await safe_reply(update, "رمز عبور باید ۴ رقم عددی باشد. دوباره وارد کنید:")
            return
        st["password"] = text
        set_state(uid, "reg_referral_choice", **carry(st))
        kb = build_inline([[("بله", "ref:yes"), ("خیر", "ref:no")]])
        await safe_reply(update, "آیا کد معرف دارید؟؟", reply_markup=kb)
        return

    if step == "reg_referral_code":
        code = text.strip()
        if not (code.isdigit() and len(code) == 5):
            await safe_reply(update, "کد معرف باید ۵ رقمی باشد. دوباره وارد کنید:")
            return
        st["referred_by"] = code
        await finalize_registration(update, context, uid, st)
        return


async def finalize_registration(update, context, uid, st):
    conn = get_conn()
    try:
        card_number = gen_card_number(conn)
        referral_code = gen_referral_code(conn)
        password_hash = hash_secret(st["password"])
        cvv2 = "".join(random.choices(string.digits, k=3))
        referred_by = st.get("referred_by")

        conn.execute(
            "INSERT INTO users(numeric_id, username, full_name, phone, password_hash, "
            "referral_code, referred_by, coins, cash, card_number, card_password_hash, "
            "cvv2_hash, blocked, reg_date, password_plain, cvv2_plain) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                extract_username(update),
                st["full_name"],
                st["phone"],
                password_hash,
                referral_code,
                referred_by,
                0,
                0,
                card_number,
                password_hash,
                hash_secret(cvv2),
                0,
                now_str(),
                st["password"],
                cvv2,
            ),
        )
        conn.commit()

        new_user = get_user_by_numeric_id(conn, uid)

        if referred_by:
            referrer = conn.execute(
                "SELECT * FROM users WHERE referral_code=?", (referred_by,)
            ).fetchone()
            if referrer is not None:
                conn.execute(
                    "UPDATE users SET coins = coins + 10 WHERE numeric_id=?",
                    (referrer["numeric_id"],),
                )
                conn.execute(
                    "UPDATE users SET coins = coins + 10 WHERE numeric_id=?",
                    (uid,),
                )
                record_transaction(conn, referrer["numeric_id"], "سیستم", "کاربر", 10, "سکه", "successful", "پاداش معرف")
                record_transaction(conn, uid, "سیستم", "کاربر", 10, "سکه", "successful", "پاداش معرف")
                conn.commit()
                await safe_send(
                    context, referrer["numeric_id"],
                    f"🎁 کاربر جدیدی با کد معرف شما ثبت‌نام کرد و ۱۰ سکه دریافت کردید.",
                )

    finally:
        conn.close()

    clear_state(uid)
    await safe_reply(update, "✅ ثبت‌نام شما با موفقیت انجام شد!", reply_markup=main_menu())


# ---------------------------------------------------------------------------
# بخش‌های منوی اصلی
# ---------------------------------------------------------------------------

async def show_profile(update, user):
    text = (
        "👤 پروفایل کاربر\n\n"
        f"نام: {user['full_name']}\n"
        f"یوزرنیم: @{user['username'] if user['username'] else '---'}\n"
        f"آیدی عددی: {user['numeric_id']}\n\n"
        f"🎁 کد معرف شما: {user['referral_code']}"
    )
    await safe_reply(update, text)


async def show_bank_info(update, user):
    text = (
        "🏦 اطلاعات بانک Nova\n\n"
        f"💳 شماره کارت: {user['card_number']}\n"
        f"🔐 رمز کارت: {user['password_plain'] if user['password_plain'] else '----'}\n"
        f"🪙 موجودی سکه: {fmt_amount(user['coins'])}\n"
        f"💰 موجودی نقدی: {fmt_amount(user['cash'])} ریال\n"
        f"🔒 CVV2: {user['cvv2_plain'] if user['cvv2_plain'] else '---'}"
    )
    await safe_reply(update, text)


async def show_referral(update, user):
    text = f"🎁 کد معرف شما\n\n{user['referral_code']}"
    kb = build_inline([[("📤 اشتراک‌گذاری کد معرف", f"share_ref:{user['referral_code']}")]])
    await safe_reply(update, text, reply_markup=kb)


async def show_support(update):
    await safe_reply(update, SUPPORT_TEXT, reply_markup=support_inline_kb())


async def show_transactions(update, user):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user["numeric_id"],),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await safe_reply(update, "📋 هیچ تراکنشی ثبت نشده است.")
        return

    for r in rows:
        text = (
            f"#{r['id']} | {r['ttype']}\n\n"
            f"مبدأ: {r['source']}\n"
            f"مقصد: {r['dest']}\n\n"
            f"مبلغ: {fmt_amount(r['amount'])} {r['unit']}\n\n"
            f"وضعیت: {r['status']}\n\n"
            f"تاریخ: {r['date']}"
        )
        await safe_reply(update, text)


# ---------------------------------------------------------------------------
# تغییر رمز
# ---------------------------------------------------------------------------

async def start_change_password(update, uid):
    set_state(uid, "chpass_old")
    await safe_reply(update, "رمز فعلی خود را وارد کنید:")


async def handle_change_password(update, context, uid, st, text):
    step = st["step"]
    conn = get_conn()
    try:
        user = get_user_by_numeric_id(conn, uid)
        if step == "chpass_old":
            if not check_secret(text, user["password_hash"]):
                await safe_reply(update, "رمز فعلی اشتباه است. دوباره تلاش کنید یا /start را بزنید.")
                return
            set_state(uid, "chpass_new")
            await safe_reply(update, "رمز جدید ۴ رقمی خود را وارد کنید:")
            return

        if step == "chpass_new":
            if not (text.isdigit() and len(text) == 4):
                await safe_reply(update, "رمز جدید باید ۴ رقم عددی باشد. دوباره وارد کنید:")
                return
            new_hash = hash_secret(text)
            conn.execute(
                "UPDATE users SET password_hash=?, card_password_hash=?, password_plain=? "
                "WHERE numeric_id=?",
                (new_hash, new_hash, text, uid),
            )
            conn.commit()
            clear_state(uid)
            await safe_reply(update, "✅ رمز عبور شما با موفقیت تغییر کرد.", reply_markup=main_menu())
            return
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# کارت به کارت
# ---------------------------------------------------------------------------

async def start_c2c(update, uid):
    set_state(uid, "c2c_dest_card")
    await safe_reply(update, "شماره کارت مقصد را وارد کنید:")


async def handle_c2c(update, context, uid, st, text):
    step = st["step"]
    conn = get_conn()
    try:
        if step == "c2c_dest_card":
            dest = conn.execute(
                "SELECT * FROM users WHERE card_number=?", (text.strip(),)
            ).fetchone()
            if dest is None:
                await safe_reply(update, "شماره کارت مقصد در Nova یافت نشد. دوباره وارد کنید:")
                return
            if dest["numeric_id"] == uid:
                await safe_reply(update, "امکان انتقال به کارت خودتان وجود ندارد. شماره دیگری وارد کنید:")
                return
            st["dest_card"] = text.strip()
            set_state(uid, "c2c_password", **carry(st))
            await safe_reply(update, "رمز عبور Nova خود را وارد کنید:")
            return

        if step == "c2c_password":
            user = get_user_by_numeric_id(conn, uid)
            if not check_secret(text, user["password_hash"]):
                await safe_reply(update, "رمز عبور اشتباه است. عملیات لغو شد.")
                clear_state(uid)
                return
            set_state(uid, "c2c_choose_unit", **carry(st))
            kb = build_inline([[("💰 ریال", "transfer:cash"), ("🪙 سکه", "transfer:coin")]])
            await safe_reply(update, "مبلغ را انتخاب کنید", reply_markup=kb)
            return

        if step == "c2c_amount":
            if not text.isdigit() or int(text) <= 0:
                await safe_reply(update, "مقدار نامعتبر است. یک عدد صحیح مثبت وارد کنید:")
                return
            amount = int(text)
            unit_key = st["unit"]
            column = "cash" if unit_key == "cash" else "coins"
            unit_fa = "ریال" if unit_key == "cash" else "سکه"

            sender = get_user_by_numeric_id(conn, uid)
            if sender[column] < amount:
                await safe_reply(update, "❌ موجودی شما کافی نیست.")
                clear_state(uid)
                return

            dest_user = get_user_by_card(conn, st["dest_card"])
            if dest_user is None:
                await safe_reply(update, "کارت مقصد دیگر معتبر نیست. عملیات لغو شد.")
                clear_state(uid)
                return

            conn.execute(
                f"UPDATE users SET {column} = {column} - ? WHERE numeric_id=?",
                (amount, uid),
            )
            conn.execute(
                f"UPDATE users SET {column} = {column} + ? WHERE numeric_id=?",
                (amount, dest_user["numeric_id"]),
            )
            record_transaction(conn, uid, mask_card(sender["card_number"]), mask_card(st["dest_card"]), amount, unit_fa, "successful", "کارت به کارت")
            record_transaction(conn, dest_user["numeric_id"], mask_card(sender["card_number"]), mask_card(st["dest_card"]), amount, unit_fa, "successful", "کارت به کارت")
            conn.commit()

            clear_state(uid)
            await safe_reply(update, "✅ انتقال با موفقیت انجام شد.", reply_markup=main_menu())

            sender_username = extract_username(update) or "کاربر Nova"
            await safe_send(
                context, dest_user["numeric_id"],
                f"کاربر گرامی، کاربر @{sender_username} برای شما مبلغ {fmt_amount(amount)} {unit_fa} انتقال داد. ✅️",
            )
            return
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# خرید سکه
# ---------------------------------------------------------------------------

async def start_buy_coin(update, uid):
    set_state(uid, "buy_coin_amount")
    await safe_reply(
        update,
        "تعداد سکه را وارد کنید.\n\n"
        f"حداقل: {COIN_MIN}\n"
        f"حداکثر: {COIN_MAX}\n"
        f"قیمت: هر 10 سکه = {fmt_amount(TOMAN_PER_10_COINS)} تومان",
    )


async def handle_buy_coin(update, context, uid, st, text):
    step = st["step"]

    if step == "buy_coin_amount":
        if not text.isdigit():
            await safe_reply(update, "لطفا یک عدد معتبر وارد کنید.")
            return
        amount = int(text)
        if amount < COIN_MIN or amount > COIN_MAX or amount % COIN_STEP != 0:
            await safe_reply(
                update,
                f"تعداد باید حداقل {COIN_MIN}، حداکثر {COIN_MAX} و مضرب {COIN_STEP} باشد.",
            )
            return
        price_toman = (amount // 10) * TOMAN_PER_10_COINS
        st["coin_count"] = amount
        st["price_toman"] = price_toman
        set_state(uid, "buy_coin_choose_method", **carry(st))
        kb = build_inline([[("💳 کارت Nova", "pay:nova"), ("🏦 کارت بانکی", "pay:bank")]])
        await safe_reply(
            update,
            f"{amount} سکه = {fmt_amount(price_toman)} تومان\n\nروش پرداخت را انتخاب کنید:",
            reply_markup=kb,
        )
        return


async def process_coin_purchase_nova(update, context, uid, st):
    conn = get_conn()
    try:
        amount_rial = st["price_toman"] * 10
        user = get_user_by_numeric_id(conn, uid)
        if user["cash"] < amount_rial:
            await safe_reply(update, "❌ موجودی کارت Nova کافی نیست.")
            clear_state(uid)
            return
        conn.execute(
            "UPDATE users SET cash = cash - ?, coins = coins + ? WHERE numeric_id=?",
            (amount_rial, st["coin_count"], uid),
        )
        record_transaction(conn, uid, "کارت Nova", "خرید سکه", st["coin_count"], "سکه", "successful", "خرید سکه")
        conn.commit()
        clear_state(uid)
        await safe_reply(
            update,
            f"✅ خرید موفق بود. {st['coin_count']} سکه به حساب شما اضافه شد.",
            reply_markup=main_menu(),
        )
    finally:
        conn.close()


async def process_coin_purchase_bank(update, context, uid, st):
    conn = get_conn()
    try:
        req_number = gen_request_number(conn)
        user = get_user_by_numeric_id(conn, uid)
        conn.execute(
            "INSERT INTO requests(request_number, user_id, full_name, username, req_type, "
            "amount, coin_count, payment_method, receipt, date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                req_number, uid, user["full_name"], user["username"], "خرید سکه",
                st["price_toman"], st["coin_count"], "کارت بانکی", None, now_str(), "pending",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    st["req_number"] = req_number
    set_state(uid, "buy_coin_wait_receipt", **carry(st))
    await safe_reply(
        update,
        f"مبلغ قابل پرداخت: {fmt_amount(st['price_toman'])} تومان\n\n"
        f"کارت پرداخت:\n{PAYMENT_CARD}\n\n"
        f"به آیدی زیر رسید را ارسال فرمایید:\n{SUPPORT_USERNAME}",
    )
    kb = build_inline([[("بله", f"coin_receipt:{req_number}:yes"), ("خیر", f"coin_receipt:{req_number}:no")]])
    await safe_reply(update, "آیا رسید ارسال شد؟", reply_markup=kb)


# ---------------------------------------------------------------------------
# انتقال پول
# ---------------------------------------------------------------------------

async def start_money_transfer(update, uid):
    set_state(uid, "money_amount")
    await safe_reply(update, "مبلغ را انتخاب نمایید (ریال)\n\nمبلغ:")


async def handle_money_transfer(update, context, uid, st, text):
    step = st["step"]
    if step == "money_amount":
        if not text.isdigit() or int(text) <= 0:
            await safe_reply(update, "مبلغ نامعتبر است. یک عدد صحیح مثبت وارد کنید:")
            return
        amount = int(text)
        conn = get_conn()
        try:
            req_number = gen_request_number(conn)
            user = get_user_by_numeric_id(conn, uid)
            conn.execute(
                "INSERT INTO requests(request_number, user_id, full_name, username, req_type, "
                "amount, coin_count, payment_method, receipt, date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    req_number, uid, user["full_name"], user["username"], "انتقال پول",
                    amount, None, None, None, now_str(), "pending",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        set_state(uid, "money_wait_receipt", req_number=req_number, amount=amount)
        await safe_reply(
            update,
            f"کارت پرداخت:\n\n{PAYMENT_CARD}\n\n"
            f"به آیدی زیر رسید را ارسال فرمایید:\n{SUPPORT_USERNAME}",
        )
        kb = build_inline([[("بله", "money_receipt:yes"), ("خیر", "money_receipt:no")]])
        await safe_reply(update, "آیا رسیدی برای شما ارسال کرده است؟؟", reply_markup=kb)
        return


# ---------------------------------------------------------------------------
# پنل مدیریت
# ---------------------------------------------------------------------------

async def render_pending_request(update, req):
    if req["req_type"] == "خرید سکه":
        text = (
            "📋 درخواست جدید\n\n"
            f"شماره درخواست: {req['request_number']}\n"
            f"نوع: {req['req_type']}\n\n"
            f"نام: {req['full_name']}\n"
            f"یوزرنیم: @{req['username'] if req['username'] else '---'}\n"
            f"آیدی عددی: {req['user_id']}\n\n"
            f"مبلغ: {fmt_amount(req['amount'])} تومان\n"
            f"سکه: {fmt_amount(req['coin_count'])}\n\n"
            f"روش پرداخت: {req['payment_method']}\n\n"
            f"رسید: {req['receipt'] or '---'}\n\n"
            f"تاریخ: {req['date']}\n\n"
            "وضعیت: pending"
        )
    else:
        text = (
            "📋 درخواست جدید\n\n"
            f"شماره درخواست: {req['request_number']}\n"
            f"نوع: {req['req_type']}\n\n"
            f"نام: {req['full_name']}\n"
            f"یوزرنیم: @{req['username'] if req['username'] else '---'}\n"
            f"آیدی عددی: {req['user_id']}\n\n"
            f"مبلغ: {fmt_amount(req['amount'])} ریال\n\n"
            f"رسید: {req['receipt'] or '---'}\n\n"
            f"تاریخ: {req['date']}\n\n"
            "وضعیت: pending"
        )
    kb = build_inline([[("بله", f"req:{req['request_number']}:yes"), ("خیر", f"req:{req['request_number']}:no")]])
    await safe_reply(update, text, reply_markup=kb)


async def show_admin_requests(update):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM requests WHERE status='pending' ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await safe_reply(update, "درخواست pending ای وجود ندارد.")
        return
    for req in rows:
        await render_pending_request(update, req)


async def approve_request(update, context, req_number, approve: bool):
    conn = get_conn()
    try:
        req = conn.execute(
            "SELECT * FROM requests WHERE request_number=?", (req_number,)
        ).fetchone()
        if req is None or req["status"] != "pending":
            return

        new_status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE requests SET status=? WHERE request_number=?", (new_status, req_number)
        )

        if approve:
            if req["req_type"] == "خرید سکه":
                conn.execute(
                    "UPDATE users SET coins = coins + ? WHERE numeric_id=?",
                    (req["coin_count"], req["user_id"]),
                )
                record_transaction(conn, req["user_id"], "کارت بانکی", "خرید سکه", req["coin_count"], "سکه", "successful", "خرید سکه")
                conn.commit()
                await safe_send(
                    context, req["user_id"],
                    f"✅ درخواست شما تایید شد.\n\n{req['coin_count']} سکه به حساب شما اضافه شد.",
                )
            else:
                conn.execute(
                    "UPDATE users SET cash = cash + ? WHERE numeric_id=?",
                    (req["amount"], req["user_id"]),
                )
                record_transaction(conn, req["user_id"], "کارت بانکی", "انتقال پول", req["amount"], "ریال", "successful", "انتقال پول")
                conn.commit()
                await safe_send(
                    context, req["user_id"],
                    f"✅ درخواست شما تایید شد.\n\nمبلغ {fmt_amount(req['amount'])} ریال به حساب Nova شما اضافه شد.",
                )
        else:
            conn.commit()
            await safe_send(context, req["user_id"], f"درخواست شما {req_number} رد شد")
    finally:
        conn.close()


async def start_broadcast(update, uid):
    set_state(uid, "admin_broadcast")
    await safe_reply(update, "متن پیام همگانی را وارد کنید:")


async def do_broadcast(update, context, text):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT numeric_id FROM users WHERE blocked=0"
        ).fetchall()
    finally:
        conn.close()

    full_text = f"{text}\n\n{BROADCAST_SIGNATURE}"
    for r in rows:
        await safe_send(context, r["numeric_id"], full_text)
    await safe_reply(update, "✅ پیام همگانی ارسال شد.", reply_markup=admin_menu())


async def toggle_maintenance(update):
    new_val = "0" if is_maintenance() else "1"
    set_setting("maintenance", new_val)
    if new_val == "1":
        await safe_reply(update, "🔴 بات وارد حالت خاموشی (maintenance) شد.", reply_markup=admin_menu())
    else:
        await safe_reply(update, "🟢 بات مجدداً روشن شد.", reply_markup=admin_menu())


async def start_block_account(update, uid):
    set_state(uid, "admin_block_card")
    await safe_reply(update, "شماره کارت کاربر مورد نظر برای مسدودسازی را وارد کنید:")


async def start_unblock_account(update, uid):
    set_state(uid, "admin_unblock_card")
    await safe_reply(update, "شماره کارت کاربر مورد نظر برای آزادسازی را وارد کنید:")


async def handle_admin_block_flow(update, context, uid, st, text):
    step = st["step"]
    conn = get_conn()
    try:
        if step == "admin_block_card":
            target = get_user_by_card(conn, text.strip())
            if target is None:
                await safe_reply(update, "کاربری با این شماره کارت یافت نشد. دوباره وارد کنید یا 'پنل کاربری' را بزنید.")
                return
            conn.execute("UPDATE users SET blocked=1 WHERE numeric_id=?", (target["numeric_id"],))
            conn.commit()
            clear_state(uid)
            await safe_reply(update, "🚫 حساب کاربر مسدود گردید.", reply_markup=admin_menu())
            await safe_send(
                context, target["numeric_id"],
                "🚫 حساب شما مسدود شده است.\n\nحساب شما به دلیل نقص قوانین مسدود شده است.\n"
                "در صورتی که نسبت به این تصمیم اعتراض دارید، لطفاً با پشتیبانی Nova در ارتباط باشید.",
            )
            await safe_send(context, target["numeric_id"], SUPPORT_TEXT)
            return

        if step == "admin_unblock_card":
            target = get_user_by_card(conn, text.strip())
            if target is None:
                await safe_reply(update, "کاربری با این شماره کارت یافت نشد. دوباره وارد کنید یا 'پنل کاربری' را بزنید.")
                return
            conn.execute("UPDATE users SET blocked=0 WHERE numeric_id=?", (target["numeric_id"],))
            conn.commit()
            clear_state(uid)
            await safe_reply(update, "✅ حساب کاربر با موفقیت آزاد شد.", reply_markup=admin_menu())
            return
    finally:
        conn.close()


async def start_gift(update, uid):
    set_state(uid, "admin_gift_choose_type")
    kb = build_inline([[("💰 پول", "gift:cash"), ("🪙 سکه", "gift:coin")]])
    await safe_reply(update, "چی میخواهید هدیه دهید؟", reply_markup=kb)


async def handle_admin_gift_flow(update, context, uid, st, text):
    step = st["step"]
    if step == "admin_gift_amount":
        if not text.isdigit() or int(text) <= 0:
            await safe_reply(update, "مقدار نامعتبر است. یک عدد صحیح مثبت وارد کنید:")
            return
        st["gift_amount"] = int(text)
        set_state(uid, "admin_gift_card", **carry(st))
        await safe_reply(update, "شماره کارت کاربر را وارد کنید:")
        return

    if step == "admin_gift_card":
        conn = get_conn()
        try:
            target = get_user_by_card(conn, text.strip())
            if target is None:
                await safe_reply(update, "کاربری با این شماره کارت یافت نشد. دوباره وارد کنید یا 'پنل کاربری' را بزنید.")
                return

            gift_type = st["gift_type"]
            amount = st["gift_amount"]
            if gift_type == "cash":
                conn.execute("UPDATE users SET cash = cash + ? WHERE numeric_id=?", (amount, target["numeric_id"]))
                record_transaction(conn, target["numeric_id"], "مدیریت", "هدیه", amount, "ریال", "successful", "هدیه")
                conn.commit()
                clear_state(uid)
                await safe_reply(update, "✅ هدیه با موفقیت ارسال شد.", reply_markup=admin_menu())
                await safe_send(
                    context, target["numeric_id"],
                    "🎁 هدیه برای شما\n\n"
                    f"کاربر گرامی، مبلغ {fmt_amount(amount)} ریال به حساب Nova شما هدیه داده شد. 💰\n\n"
                    f"موجودی حساب شما به‌روزرسانی شد. ✅\n\n{BROADCAST_SIGNATURE}",
                )
            else:
                conn.execute("UPDATE users SET coins = coins + ? WHERE numeric_id=?", (amount, target["numeric_id"]))
                record_transaction(conn, target["numeric_id"], "مدیریت", "هدیه", amount, "سکه", "successful", "هدیه")
                conn.commit()
                clear_state(uid)
                await safe_reply(update, "✅ هدیه با موفقیت ارسال شد.", reply_markup=admin_menu())
                await safe_send(
                    context, target["numeric_id"],
                    "🎁 هدیه برای شما\n\n"
                    f"کاربر گرامی، مقدار {fmt_amount(amount)} سکه به حساب Nova شما هدیه داده شد. 🪙\n\n"
                    f"موجودی حساب شما به‌روزرسانی شد. ✅\n\n{BROADCAST_SIGNATURE}",
                )
        finally:
            conn.close()
        return


# ---------------------------------------------------------------------------
# آمار کاربران، لیست حساب‌ها و کاهش موجودی
# ---------------------------------------------------------------------------

async def show_admin_stats(update):
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        unblocked = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE blocked=0"
        ).fetchone()["c"]
        blocked = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE blocked=1"
        ).fetchone()["c"]
    finally:
        conn.close()

    text = (
        "📊 آمار کاربران Nova\n\n"
        f"👥 تعداد کاربران: {fmt_amount(total)}\n"
        f"✅ حساب های بانکی آزاد: {fmt_amount(unblocked)}\n"
        f"🚫 حساب های بانکی مسدود: {fmt_amount(blocked)}"
    )
    await safe_reply(update, text)


async def show_admin_bank_accounts(update):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT full_name, username, numeric_id, card_number, blocked "
            "FROM users ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await safe_reply(update, "🏦 هیچ حساب بانکی ثبت نشده است.")
        return

    header = f"🏦 لیست حساب های بانکی\n\nتعداد کل: {fmt_amount(len(rows))}\n"
    await safe_reply(update, header)

    for u in rows:
        status = "🚫 مسدود" if u["blocked"] else "✅ آزاد"
        text = (
            f"👤 نام و نام خانوادگی: {u['full_name'] or '---'}\n"
            f"🆔 یوزرنیم: @{u['username'] if u['username'] else '---'}\n"
            f"🔢 آیدی عددی: {u['numeric_id']}\n"
            f"💳 شماره کارت: {u['card_number']}\n"
            f"وضعیت: {status}"
        )
        await safe_reply(update, text)


async def start_deduct(update, uid):
    set_state(uid, "admin_deduct_card")
    await safe_reply(update, "شماره کارت را وارد نمایید:")


async def handle_admin_deduct_flow(update, context, uid, st, text):
    step = st["step"]

    if step == "admin_deduct_card":
        conn = get_conn()
        try:
            target = get_user_by_card(conn, text.strip())
        finally:
            conn.close()
        if target is None:
            await safe_reply(
                update,
                "کاربری با این شماره کارت یافت نشد. دوباره وارد کنید یا 'پنل کاربری' را بزنید.",
            )
            return
        st["target_card"] = text.strip()
        set_state(uid, "admin_deduct_choose_type", **carry(st))
        kb = build_inline([[("💰 پول", "deduct:cash"), ("🪙 سکه", "deduct:coin")]])
        await safe_reply(update, "چه چیزی میخواهید کاهش دهید؟", reply_markup=kb)
        return

    if step == "admin_deduct_amount":
        if not text.isdigit() or int(text) <= 0:
            await safe_reply(update, "مقدار نامعتبر است. یک عدد صحیح مثبت وارد کنید:")
            return
        st["deduct_amount"] = int(text)
        set_state(uid, "admin_deduct_reason", **carry(st))
        await safe_reply(update, "دلیل کم کردن را توضیح دهید:")
        return

    if step == "admin_deduct_reason":
        reason = text.strip()
        if not reason:
            await safe_reply(update, "لطفا دلیل را وارد کنید:")
            return

        conn = get_conn()
        try:
            target = get_user_by_card(conn, st["target_card"])
            if target is None:
                await safe_reply(update, "کاربر یافت نشد. عملیات لغو شد.")
                clear_state(uid)
                return

            deduct_type = st["deduct_type"]
            amount = st["deduct_amount"]
            column = "cash" if deduct_type == "cash" else "coins"
            unit_fa = "ریال" if deduct_type == "cash" else "سکه"

            current = target[column] or 0
            actual_deduct = min(current, amount)

            conn.execute(
                f"UPDATE users SET {column} = {column} - ? WHERE numeric_id=?",
                (actual_deduct, target["numeric_id"]),
            )
            record_transaction(
                conn, target["numeric_id"], "مدیریت", "کاهش",
                actual_deduct, unit_fa, "successful", "کاهش موجودی",
            )
            conn.commit()

            target_username = target["username"] or "---"
        finally:
            conn.close()

        clear_state(uid)
        await safe_reply(
            update,
            f"با موفقیت از حساب ({target_username}) کم شد.\n\n"
            f"مقدار کاهش یافته: {fmt_amount(actual_deduct)} {unit_fa}\n"
            f"دلیل: {reason}",
            reply_markup=admin_menu(),
        )

        await safe_send(
            context, target["numeric_id"],
            "کاربر گرامی، از طرف مدیریت از حساب شما کم شد.\n\n"
            f"مبلغ کم شده: {fmt_amount(actual_deduct)} {unit_fa}\n"
            f"دلیل برداشت: {reason}\n\n"
            f"{BROADCAST_SIGNATURE}",
        )
        return


# ---------------------------------------------------------------------------
# دیسپچر اصلی متن
# ---------------------------------------------------------------------------

async def on_text(update, context):
    try:
        uid = extract_uid(update)
        text = extract_text(update)
        if uid is None or text is None:
            return
        text = text.strip()

        conn = get_conn()
        try:
            user = get_user_by_numeric_id(conn, uid)
        finally:
            conn.close()

        st = get_state(uid)

        if user is None:
            if st and st["step"].startswith("reg_"):
                await handle_registration(update, context, uid, st, text)
            else:
                await cmd_start(update, context)
            return

        if is_maintenance() and not is_admin(uid):
            if text == "🎧 پشتیبانی":
                await show_support(update)
                return
            await safe_reply(
                update,
                "🔧 ربات در حال آپدیت می باشد.\n\nاز صبوری شما متشکریم. 🙏\n\nلطفاً کمی بعد مجدداً تلاش کنید.",
            )
            return

        if user["blocked"] and not is_admin(uid):
            if text == "🎧 پشتیبانی":
                await show_support(update)
                return
            await safe_reply(
                update,
                "🚫 حساب شما مسدود شده است.\n\nحساب شما به دلیل نقص قوانین مسدود شده است.\n"
                "در صورتی که نسبت به این تصمیم اعتراض دارید، لطفاً با پشتیبانی Nova در ارتباط باشید.",
                reply_markup=support_inline_kb(),
            )
            return

        if text == "پنل مدیریت" and is_admin(uid):
            clear_state(uid)
            await safe_reply(update, "به پنل مدیریت خوش آمدید.", reply_markup=admin_menu())
            return

        if text == "پنل کاربری" and is_admin(uid):
            clear_state(uid)
            await safe_reply(update, "بازگشت به منوی کاربری.", reply_markup=main_menu())
            return

        if st is not None:
            step = st["step"]
            if step.startswith("chpass_"):
                await handle_change_password(update, context, uid, st, text)
                return
            if step.startswith("c2c_") and step != "c2c_choose_unit":
                await handle_c2c(update, context, uid, st, text)
                return
            if step.startswith("buy_coin_") and step != "buy_coin_choose_method":
                await handle_buy_coin(update, context, uid, st, text)
                return
            if step.startswith("money_") and step != "money_wait_receipt":
                await handle_money_transfer(update, context, uid, st, text)
                return
            if step == "admin_broadcast" and is_admin(uid):
                clear_state(uid)
                await do_broadcast(update, context, text)
                return
            if step in ("admin_block_card", "admin_unblock_card") and is_admin(uid):
                await handle_admin_block_flow(update, context, uid, st, text)
                return
            if step in ("admin_gift_amount", "admin_gift_card") and is_admin(uid):
                await handle_admin_gift_flow(update, context, uid, st, text)
                return
            if step in ("admin_deduct_card", "admin_deduct_amount", "admin_deduct_reason") and is_admin(uid):
                await handle_admin_deduct_flow(update, context, uid, st, text)
                return

        if text == "👤 پروفایل":
            await show_profile(update, user)
            return
        if text == "🏦 اطلاعات بانک":
            await show_bank_info(update, user)
            return
        if text == "🎁 کد معرف":
            await show_referral(update, user)
            return
        if text == "🎧 پشتیبانی":
            await show_support(update)
            return
        if text == "🔐 تغییر رمز عبور":
            await start_change_password(update, uid)
            return
        if text == "📋 تراکنش‌ها":
            await show_transactions(update, user)
            return
        if text == "💳 کارت به کارت":
            await start_c2c(update, uid)
            return
        if text == "🪙 خرید سکه":
            await start_buy_coin(update, uid)
            return
        if text == "💸 انتقال پول":
            await start_money_transfer(update, uid)
            return

        if is_admin(uid):
            if text == "📋 درخواست ها":
                await show_admin_requests(update)
                return
            if text == "📢 پیام همگانی":
                await start_broadcast(update, uid)
                return
            if text in ("🔴 خاموشی بات", "🟢 روشن کردن بات"):
                await toggle_maintenance(update)
                return
            if text == "🚫 مسدود سازی حساب":
                await start_block_account(update, uid)
                return
            if text == "🔓 آزاد سازی حساب":
                await start_unblock_account(update, uid)
                return
            if text == "🎁 هدیه به کاربر":
                await start_gift(update, uid)
                return
            if text == "📊 آمار کاربران":
                await show_admin_stats(update)
                return
            if text == "➖ کاهش پول":
                await start_deduct(update, uid)
                return
            if text == "🏦 حساب های بانکی":
                await show_admin_bank_accounts(update)
                return

        await safe_reply(update, "از منوی زیر یک گزینه انتخاب کنید:", reply_markup=main_menu())

    except Exception:
        logger.exception("خطا در on_text")
        await safe_reply(update, "یک خطای موقت رخ داد. لطفاً دوباره تلاش کنید.")


# ---------------------------------------------------------------------------
# دریافت عکس (رسید)
# ---------------------------------------------------------------------------

async def on_photo(update, context):
    try:
        uid = extract_uid(update)
        if uid is None:
            return
        st = get_state(uid)
        if st is None:
            return

        file_id = extract_photo_file_id(update)

        if st["step"] == "money_wait_receipt":
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE requests SET receipt=? WHERE request_number=?",
                    (file_id, st["req_number"]),
                )
                conn.commit()
            finally:
                conn.close()
            await safe_reply(update, "رسید دریافت شد. لطفاً روی 'بله' در پیام بالا بزنید تا درخواست ارسال شود.")
            for admin_id in ADMIN_IDS:
                await safe_send(context, admin_id, "📎 یک رسید جدید برای درخواست انتقال پول دریافت شد. برای بررسی، «📋 درخواست ها» را در پنل مدیریت بزنید.")
            return

        if st["step"] == "buy_coin_wait_receipt":
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE requests SET receipt=? WHERE request_number=?",
                    (file_id, st["req_number"]),
                )
                conn.commit()
            finally:
                conn.close()
            await safe_reply(update, "رسید دریافت شد. لطفاً روی 'بله' در پیام بالا بزنید تا درخواست ارسال شود.")
            for admin_id in ADMIN_IDS:
                await safe_send(context, admin_id, "📎 یک رسید جدید برای درخواست خرید سکه دریافت شد. برای بررسی، «📋 درخواست ها» را در پنل مدیریت بزنید.")
            return

    except Exception:
        logger.exception("خطا در on_photo")
        await safe_reply(update, "یک خطای موقت رخ داد. لطفاً دوباره تلاش کنید.")


# ---------------------------------------------------------------------------
# دکمه‌های Inline (Callback Query)
# ---------------------------------------------------------------------------

async def on_callback(update, context):
    try:
        cq = getattr(update, "callback_query", None)
        if cq is None:
            return
        data = getattr(cq, "data", "") or ""
        uid = extract_uid(update)
        if uid is None:
            return

        await safe_answer_callback(update)

        if data == "ref:yes":
            st = get_state(uid) or {}
            set_state(uid, "reg_referral_code", **carry(st))
            await safe_reply(update, "کد معرف ۵ رقمی خود را وارد کنید")
            return

        if data == "ref:no":
            st = get_state(uid) or {}
            st.pop("referred_by", None)
            await finalize_registration(update, context, uid, st)
            return

        if data.startswith("share_ref:"):
            code = data.split(":", 1)[1]
            await safe_reply(update, f"کد معرف من در Nova: {code}\nبا این کد ثبت‌نام کن و سکه هدیه بگیر! 🎁")
            return

        if data == "open_support":
            await safe_reply(update, f"برای ارتباط با پشتیبانی به آیدی زیر پیام دهید:\n{SUPPORT_USERNAME}")
            return

        if data in ("transfer:cash", "transfer:coin"):
            st = get_state(uid)
            if st is None or st["step"] != "c2c_choose_unit":
                return
            st["unit"] = "cash" if data == "transfer:cash" else "coin"
            set_state(uid, "c2c_amount", **carry(st))
            await safe_reply(update, "مقدار را وارد کنید:")
            return

        if data in ("pay:nova", "pay:bank"):
            st = get_state(uid)
            if st is None or st["step"] != "buy_coin_choose_method":
                return
            if data == "pay:nova":
                await process_coin_purchase_nova(update, context, uid, st)
            else:
                await process_coin_purchase_bank(update, context, uid, st)
            return

        if data.startswith("coin_receipt:"):
            _, req_number, ans = data.split(":")
            if ans == "no":
                conn = get_conn()
                try:
                    conn.execute("UPDATE requests SET status='rejected' WHERE request_number=?", (req_number,))
                    conn.commit()
                finally:
                    conn.close()
                clear_state(uid)
                await safe_reply(update, f"درخواست شما {req_number} رد شد", reply_markup=main_menu())
            else:
                clear_state(uid)
                await safe_reply(update, "درخواست شما به مدیریت ارسال شد.", reply_markup=main_menu())
                for admin_id in ADMIN_IDS:
                    await safe_send(context, admin_id, f"درخواست جدید خرید سکه: {req_number}\nبرای بررسی «📋 درخواست ها» را در پنل مدیریت بزنید.")
            return

        if data.startswith("money_receipt:"):
            ans = data.split(":")[1]
            st = get_state(uid)
            if st is None or "req_number" not in st:
                return
            req_number = st["req_number"]
            if ans == "no":
                conn = get_conn()
                try:
                    conn.execute("UPDATE requests SET status='rejected' WHERE request_number=?", (req_number,))
                    conn.commit()
                finally:
                    conn.close()
                clear_state(uid)
                await safe_reply(update, f"درخواست شما {req_number} رد شد", reply_markup=main_menu())
            else:
                clear_state(uid)
                await safe_reply(update, "درخواست شما به مدیریت ارسال شد.", reply_markup=main_menu())
                for admin_id in ADMIN_IDS:
                    await safe_send(context, admin_id, f"درخواست جدید انتقال پول: {req_number}\nبرای بررسی «📋 درخواست ها» را در پنل مدیریت بزنید.")
            return

        if data.startswith("req:"):
            if not is_admin(uid):
                return
            _, req_number, ans = data.split(":")
            await approve_request(update, context, req_number, approve=(ans == "yes"))
            await safe_reply(update, "انجام شد.")
            return

        if data in ("gift:cash", "gift:coin"):
            if not is_admin(uid):
                return
            st = get_state(uid) or {}
            st["gift_type"] = "cash" if data == "gift:cash" else "coin"
            set_state(uid, "admin_gift_amount", **carry(st))
            await safe_reply(update, "مقدار را وارد کنید:")
            return

        if data in ("deduct:cash", "deduct:coin"):
            if not is_admin(uid):
                return
            st = get_state(uid)
            if st is None or st["step"] != "admin_deduct_choose_type":
                return
            st["deduct_type"] = "cash" if data == "deduct:cash" else "coin"
            set_state(uid, "admin_deduct_amount", **carry(st))
            await safe_reply(update, "مبلغ را ارسال کنید:")
            return

    except Exception:
        logger.exception("خطا در on_callback")
        await safe_reply(update, "یک خطای موقت رخ داد. لطفاً دوباره تلاش کنید.")


# ---------------------------------------------------------------------------
# اجرای برنامه
# ---------------------------------------------------------------------------

def main():
    ensure_schema()

    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        logger.warning("BOT_TOKEN هنوز مقداردهی نشده است! لطفاً قبل از اجرا آن را در بالای فایل تنظیم کنید.")

    app = Application(BOT_TOKEN)

    app.add_handler(CommandHandler("start", cmd_start))

    if PHOTO_FILTER is not None:
        app.add_handler(MessageHandler(PHOTO_FILTER, on_photo))

    app.add_handler(MessageHandler(TEXT_ONLY_FILTER, on_text))
    app.add_handler(build_callback_handler(on_callback))

    logger.info("Nova bot در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
