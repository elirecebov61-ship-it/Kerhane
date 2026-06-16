# -*- coding: utf-8 -*-
"""
KERHANE EGLENCE BOT
--------------------
Tam funksiyali Telegram oyun botu.
Kitabxana: python-telegram-bot v20+ (async)
Verilenler bazasi: PostgreSQL (psycopg2)
Token: BOT_TOKEN environment variable-dan oxunur.
DB elaqesi: DATABASE_URL environment variable-dan oxunur.

Qurulus:
    pip install -r requirements.txt
    export BOT_TOKEN="123456:ABC-..."           (Linux/Mac)
    export DATABASE_URL="postgresql://user:pass@host:port/dbname"
    python bot.py

Oyunlar:
    1. Slot Makinesi
    2. Bayrak Yarisi (10 tur, 15 saniye, 3 hak)
    3. Tas Kagiz Makas (Bot ile / PvP Duello)
    4. Yazi Tura
    5. Bul Beni (Kutu) - 3x3
    6. X0X Duello (PvP)
    7. Sayi Tahmin (1-100, pot sistemi)
    8. Bulmaca (Adam Asmaca - zorluk secimli)
    9. Kelime Zinciri
    10. Rus Ruleti
    11. At Yarisi
    12. Profilim + Saatlik Odul
    13. En Zenginler (Leaderboard)
    14. Para Transferi (/yolla - reply ile)
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------------------------------------------------------------
# AYARLAR
# ----------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

START_BALANCE = 5999          # Yeni uzvun basliyici bakiyesi
HOURLY_REWARD = 5000          # Saatlik odul mebedi
HOURLY_COOLDOWN_SECONDS = 60 * 60  # 1 saat

ADMIN_CONTACT = "@korunan"    # Bakiye almaq ucun elaqe

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# YADDASDAKI AKTIV OYUN VEZIYYETLERI
# ----------------------------------------------------------------------

PENDING_GAMES = {}          # bayraq / x0x / tkm duello / rus ruleti gozleme ve s.
WORD_CHAIN_STATE = {}        # chat_id -> kelime zinciri veziyyeti
GUESS_NUMBER_STATE = {}      # chat_id -> sayi tahmin veziyyeti
HANGMAN_STATE = {}           # chat_id -> bulmaca (adam asmaca) veziyyeti
BAYRAK_TIMERS = {}           # f"bayrak_{user_id}" -> asyncio.Task (geri sayim)

# ----------------------------------------------------------------------
# VERILENLER BAZASI (PostgreSQL)
# ----------------------------------------------------------------------

_connection_pool = None


def get_pool():
    global _connection_pool
    if _connection_pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "❌ DATABASE_URL tapilmadi! Railway-de PostgreSQL elave et, "
                "ya da export DATABASE_URL='postgresql://...' ile elaqeni ver."
            )
        _connection_pool = pg_pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    return _connection_pool


def db_connect():
    return get_pool().getconn()


def db_release(conn):
    get_pool().putconn(conn)


def init_db():
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance BIGINT DEFAULT 0,
                last_reward_ts BIGINT DEFAULT 0
            )
            """
        )
        conn.commit()
    finally:
        db_release(conn)


def ensure_user(user_id: int, first_name: str, username: str):
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users (user_id, first_name, username, balance, last_reward_ts) "
                "VALUES (%s,%s,%s,%s,0)",
                (user_id, first_name, username, START_BALANCE),
            )
        else:
            cur.execute(
                "UPDATE users SET first_name=%s, username=%s WHERE user_id=%s",
                (first_name, username, user_id),
            )
        conn.commit()
    finally:
        db_release(conn)


def get_balance(user_id: int) -> int:
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        db_release(conn)


def change_balance(user_id: int, amount: int) -> int:
    """Bakiyeye amount elave edir (menfi ede biler). Yeni balansi qaytarir."""
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET balance = balance + %s WHERE user_id=%s RETURNING balance",
            (amount, user_id),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else 0
    finally:
        db_release(conn)


def get_last_reward_ts(user_id: int) -> int:
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT last_reward_ts FROM users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        db_release(conn)


def set_last_reward_ts(user_id: int, ts: int):
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_reward_ts=%s WHERE user_id=%s", (ts, user_id))
        conn.commit()
    finally:
        db_release(conn)


def get_user_row(user_id: int):
    conn = db_connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
        return cur.fetchone()
    finally:
        db_release(conn)


def get_top_users(limit: int = 15):
    conn = db_connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT user_id, first_name, username, balance FROM users "
            "ORDER BY balance DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()
    finally:
        db_release(conn)


# ----------------------------------------------------------------------
# KOMEKCI FUNKSIYALAR
# ----------------------------------------------------------------------

def fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


async def reply(update: Update, text: str, **kwargs):
    return await update.effective_message.reply_text(text, **kwargs)


def parse_bet(args, default=100):
    """Komanda arqumentlerinden bahis mebedini oxuyur."""
    if args:
        try:
            v = int(args[0])
            if v > 0:
                return v
        except (ValueError, IndexError):
            pass
    return default


def parse_bet_strict(args):
    """Bahis mebedini oxuyur, yoxdursa None qaytarir (format xebardarligi ucun)."""
    if not args:
        return None
    try:
        v = int(args[0])
        if v > 0:
            return v
    except (ValueError, IndexError):
        pass
    return None


async def check_and_take_bet(update: Update, user_id: int, bet: int) -> bool:
    """Bakiye kifayet edirse bahisi cixir, true qaytarir. Yoxdursa xeberdarliq edib false qaytarir."""
    bal = get_balance(user_id)
    if bal < bet:
        await reply(
            update,
            f"❌ Bakiyən kifayət etmir!\n💳 Bakiyən: {fmt(bal)} TL, bahis: {fmt(bet)} TL.",
        )
        return False
    change_balance(user_id, -bet)
    return True


async def safe_edit(query, text, reply_markup=None, parse_mode=None):
    """edit_message_text cagirir, 'message is not modified' xetasini sessiz keçir."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise

# ----------------------------------------------------------------------
# /start VE /menu
# ----------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    text = (
        f"👋 Salam {user.first_name}! Kerhane Eğlence Botuna Hoş Geldin!\n"
        f"🎮 Eğlenirken bakiye kazanabileceğin oyunlar burada seni bekliyor.\n\n"
        f"👉 Oyunların listesini görmek ve başlamak için lütfen /menu yaz!"
    )
    await reply(update, text)


def build_menu_keyboard():
    buttons = [
        [
            InlineKeyboardButton("🎰 Slot", callback_data="info_slot"),
            InlineKeyboardButton("🚩 Bayrak", callback_data="info_bayrak"),
            InlineKeyboardButton("✂️ Taş Kağıt Makas", callback_data="info_tkm"),
        ],
        [
            InlineKeyboardButton("🪙 Yazı Tura", callback_data="info_yazitura"),
            InlineKeyboardButton("🔍 Bul Beni", callback_data="info_kutu"),
            InlineKeyboardButton("⚔️ XOX Düello", callback_data="info_x0x"),
        ],
        [
            InlineKeyboardButton("🔢 Sayı Tahmin", callback_data="info_sayitahmin"),
            InlineKeyboardButton("🧩 Bulmaca", callback_data="info_bulmaca"),
            InlineKeyboardButton("📝 Kelime Zinciri", callback_data="info_kelime"),
        ],
        [
            InlineKeyboardButton("🔫 Rus Ruleti", callback_data="info_rusruleti"),
            InlineKeyboardButton("🐎 At Yarışı", callback_data="info_atyarisi"),
        ],
        [
            InlineKeyboardButton("👤 Profilim", callback_data="info_profil"),
            InlineKeyboardButton("🏆 En Zenginler", callback_data="info_zenginler"),
            InlineKeyboardButton("💸 Para Transferi", callback_data="info_transfer"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    text = (
        "🎰 KERHANE EĞLENCE BOT - OYUN PANELİ\n"
        "Eğlenceli oyunlar, keyifli vakit. Aşağıdaki butonlardan oyunların "
        "komutlarını öğrenebilirsin.\n\n"
        f"⚠️ Bakiye almak için {ADMIN_CONTACT} ile iletişime geçin."
    )
    await reply(update, text, reply_markup=build_menu_keyboard())


GAME_INFO_TEXT = {
    "info_slot": (
        "🎰 Slot oynamak için lütfen şu formatta yazın:\n`/slot [miktar]`\n"
        "Örnek: `/slot 100`"
    ),
    "info_bayrak": (
        "🚩 Bayrak yarışı oynamak için lütfen şu formatta yazın:\n`/bayrak [miktar]`\n"
        "Örnek: `/bayrak 150`"
    ),
    "info_tkm": (
        "✂️ Taş Kağıt Makas oynamak için lütfen şu formatta yazın:\n`/tkm [miktar]`\n"
        "Örnek: `/tkm 200`"
    ),
    "info_yazitura": (
        "🪙 Yazı-Tura oynamak için lütfen şu formatta yazın:\n`/ytsans [miktar]`\n"
        "Örnek: `/ytsans 100`"
    ),
    "info_kutu": (
        "🔍 Bul beni kutu oyunu için lütfen şu formatta yazın:\n`/bulbeni [miktar]`\n"
        "Örnek: `/bulbeni 300`"
    ),
    "info_x0x": (
        "❌ XOX düellosu başlatmak için lütfen şu formatta yazın:\n`/xox [miktar]`\n"
        "Örnek: `/xox 500`"
    ),
    "info_sayitahmin": (
        "🔢 Sayı tahmin oyunu için lütfen şu formatta yazın:\n`/tahminet [miktar]`\n"
        "Örnek: `/tahminet 50`"
    ),
    "info_bulmaca": (
        "🧩 Adam asmaca oyunu için lütfen şu formatta yazın:\n`/bulmaca [miktar]`\n"
        "Örnek: `/bulmaca 100`"
    ),
    "info_kelime": (
        "📝 Kelime zinciri oyunu için lütfen şu formatta yazın:\n`/kelime [miktar]`\n"
        "Örnek: `/kelime 100`"
    ),
    "info_rusruleti": (
        "🔫 Rus ruleti oynamak için lütfen şu formatta yazın:\n`/ruleti [miktar]`\n"
        "Örnek: `/ruleti 100`"
    ),
    "info_atyarisi": (
        "🐎 At yarışı oynamak için lütfen şu formatta yazın:\n`/atyarisi [miktar]`\n"
        "Örnek: `/atyarisi 100`"
    ),
    "info_profil": None,       # ayrıca handle olunur
    "info_zenginler": None,    # ayrıca handle olunur
    "info_transfer": (
        "💸 Para Transferi Nasıl Yapılır?\n\n"
        "Başka bir kullanıcıya bakiye göndermek için şu adımları izleyin:\n\n"
        "1️⃣ Para göndermek istediğiniz kişinin bir mesajını yanıtlayın (Reply).\n"
        "2️⃣ Yanıt olarak şu komutu yazıp gönderin:\n`/yolla [miktar]`\n\n"
        "💡 Örnek: Birinin mesajını yanıtlayarak `/yolla 500` yazarsanız, "
        "o kişiye 500 TL gönderilir."
    ),
}


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    # Menu duymesine basilanda menyu silinir, hemin oyuna aid mesaj gelir
    if data == "info_profil":
        await query.message.delete()
        await send_profile(update, context)
        return

    if data == "info_zenginler":
        await query.message.delete()
        await send_leaderboard(update, context)
        return

    text = GAME_INFO_TEXT.get(data)
    if text:
        await query.message.delete()
        await query.message.chat.send_message(text, parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------------
# PROFIL VE SAATLIK ODUL
# ----------------------------------------------------------------------

async def send_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bal = get_balance(user.id)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎁 Saatlik Ödül", callback_data="odul_panel")]]
    )
    text = (
        "👤 PROFİL\n"
        f"📛 İsim: {user.first_name}\n"
        f"💳 Bakiye: {fmt(bal)} TL"
    )
    await update.effective_chat.send_message(text, reply_markup=keyboard)


async def ben_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_profile(update, context)


async def odul_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🎁 Saatlik Ödül Paneli\n"
        "Ödülünüzü buradan butonla alamazsınız.\n"
        f"💰 Saatlik bedava {fmt(HOURLY_REWARD)} TL bakiyenizi talep etmek için "
        "sohbete lütfen şu komutu yazın:\n\n/odulum"
    )
    await query.message.reply_text(text)


async def odulum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    now = int(time.time())
    last_ts = get_last_reward_ts(user.id)
    elapsed = now - last_ts

    if elapsed < HOURLY_COOLDOWN_SECONDS:
        remaining = HOURLY_COOLDOWN_SECONDS - elapsed
        minutes = remaining // 60
        await reply(update, f"❌ Henüz vaktin dolmadı! {minutes} dakika sonra tekrar dene.")
        return

    set_last_reward_ts(user.id, now)
    new_balance = change_balance(user.id, HOURLY_REWARD)
    await reply(
        update,
        f"🎁 Harika! Saatlik ödülünüz olan {fmt(HOURLY_REWARD)} TL hesabınıza eklendi!\n"
        f"💳 Güncel Bakiye: {fmt(new_balance)} TL",
    )


# ----------------------------------------------------------------------
# EN ZENGINLER (LEADERBOARD)
# ----------------------------------------------------------------------

def _leaderboard_time_left():
    """Saat ve dakika olaraq qalan vaxti dekorativ hesablar (gunun sonuna qeder)."""
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = tomorrow - now
    total_minutes = int(remaining.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return hours, minutes


async def send_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_users(limit=15)
    hours, minutes = _leaderboard_time_left()

    lines = [
        f"🏆 GÜNLÜK EN ZENGİN {len(top)}",
        f"⏳ Yenilenmeye Kalan: {hours}s {minutes}dk",
        "",
    ]
    for i, row in enumerate(top, start=1):
        name = row["first_name"] or row["username"] or "Oyuncu"
        lines.append(f"{i}. {name} -")
        lines.append(f"`{fmt(row['balance'])} TL`")
        lines.append("")

    text = "\n".join(lines).strip()
    await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)


async def zenginler_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_leaderboard(update, context)


# ----------------------------------------------------------------------
# PARA TRANSFERI (/yolla - reply ile)
# ----------------------------------------------------------------------

async def yolla_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    msg = update.effective_message
    if not msg.reply_to_message:
        await reply(update, "❌ Yanıtlayarak yaz!")
        return

    amount = parse_bet_strict(context.args)
    if amount is None:
        await reply(update, "❌ Yanıtlayarak yaz!")
        return

    target_user = msg.reply_to_message.from_user
    ensure_user(target_user.id, target_user.first_name or "Oyuncu", target_user.username or "")

    bal = get_balance(user.id)
    if bal < amount:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    change_balance(user.id, -amount)
    change_balance(target_user.id, amount)

    sender_name = user.first_name or "Oyuncu"
    target_name = target_user.first_name or "Oyuncu"

    if target_user.id == user.id:
        await reply(update, f"💸 {sender_name} -> {sender_name}: {fmt(amount)} TL gönderildi!")
    else:
        await reply(update, f"💸 {sender_name} -> {target_name}: {fmt(amount)} TL gönderildi!")

# ----------------------------------------------------------------------
# 1) SLOT MAKINESI (animasiyali)
# ----------------------------------------------------------------------

SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "🍎", "💎", "🎰", "⏳"]
SLOT_MULTIPLIERS = {
    "💎💎💎": 10,
    "🎰🎰🎰": 8,
    "🔔🔔🔔": 5,
    "🍇🍇🍇": 4,
    "🍎🍎🍎": 3,
    "🍋🍋🍋": 2.5,
    "🍒🍒🍒": 2,
}


async def slot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🎰 Slot oynamak için lütfen şu formatta yazın:\n`/slot [miktar]`\n"
            "Örnek: `/slot 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not await check_and_take_bet(update, user.id, bet):
        return

    msg = await reply(update, "🎰 SLOT DÖNÜYOR...\n[ ⏳ | ⏳ | ⏳ ]")

    # Animasiya: bir nece "firlanma" kadri
    for _ in range(3):
        await asyncio.sleep(0.5)
        spin = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        try:
            await msg.edit_text(f"🎰 SLOT DÖNÜYOR...\n[ {spin[0]} | {spin[1]} | {spin[2]} ]")
        except BadRequest:
            pass

    await asyncio.sleep(0.5)

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    combo = "".join(reels)
    display = " | ".join(reels)

    multiplier = SLOT_MULTIPLIERS.get(combo, 0)
    if multiplier == 0 and reels[0] == reels[1] == reels[2]:
        multiplier = 2  # diger ucluk uyğunlasmalari ucun tehlukesizlik
    if multiplier == 0 and len(set(reels)) == 2:
        multiplier = 0.5  # 2 eyni simvol ucun kicik mukafat

    win = int(bet * multiplier)

    if win > 0:
        new_bal = change_balance(user.id, win)
        text = (
            f"🎰 SONUÇ\n[ {display} ]\n\n"
            f"✅ KAZANDIN! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        )
    else:
        new_bal = get_balance(user.id)
        text = (
            f"🎰 SONUÇ\n[ {display} ]\n\n"
            f"💀 KAYBETTİN! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        )

    try:
        await msg.edit_text(text)
    except BadRequest:
        await update.effective_chat.send_message(text)

# ----------------------------------------------------------------------
# 2) BAYRAK YARISI (10 tur, 3 hak, 15 saniye canli sayim)
# ----------------------------------------------------------------------

# 30 bayraq -> (emoji, dogru cevab). Bezileri "zor" (az taninan) olaraq qarisdirilib.
FLAG_POOL = [
    ("🇹🇷", "Türkiye"), ("🇦🇿", "Azerbaycan"), ("🇩🇪", "Almanya"), ("🇫🇷", "Fransa"),
    ("🇧🇷", "Brezilya"), ("🇯🇵", "Japonya"), ("🇮🇹", "İtalya"), ("🇪🇸", "İspanya"),
    ("🇬🇧", "İngiltere"), ("🇺🇸", "Amerika"), ("🇷🇺", "Rusya"), ("🇨🇳", "Çin"),
    ("🇰🇷", "Güney Kore"), ("🇮🇳", "Hindistan"), ("🇨🇦", "Kanada"), ("🇲🇽", "Meksika"),
    ("🇳🇱", "Hollanda"), ("🇸🇪", "İsveç"), ("🇳🇴", "Norveç"), ("🇵🇹", "Portekiz"),
    ("🇬🇷", "Yunanistan"), ("🇪🇬", "Mısır"), ("🇸🇦", "Suudi Arabistan"), ("🇦🇷", "Arjantin"),
    # zor bayraqlar
    ("🇲🇳", "Moğolistan"), ("🇰🇿", "Kazakistan"), ("🇱🇻", "Letonya"), ("🇪🇪", "Estonya"),
    ("🇧🇾", "Belarus"), ("🇺🇾", "Uruguay"),
]

BAYRAK_ROUNDS = 10
BAYRAK_LIVES = 3
BAYRAK_TIME = 15


def build_bayrak_round():
    correct = random.choice(FLAG_POOL)
    others = random.sample([f for f in FLAG_POOL if f != correct], 3)
    options = others + [correct]
    random.shuffle(options)
    return correct, options


async def bayrak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🚩 Bayrak yarışı oynamak için lütfen şu formatta yazın:\n`/bayrak [miktar]`\n"
            "Örnek: `/bayrak 150`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not await check_and_take_bet(update, user.id, bet):
        return

    key = f"bayrak_{user.id}"
    correct, options = build_bayrak_round()
    PENDING_GAMES[key] = {
        "user_id": user.id,
        "bet": bet,
        "round": 1,
        "lives": BAYRAK_LIVES,
        "correct": correct,
        "options": options,
        "chat_id": update.effective_chat.id,
        "message_id": None,
    }

    msg = await _bayrak_send_round(update.effective_chat, key)
    PENDING_GAMES[key]["message_id"] = msg.message_id
    _bayrak_start_timer(context, key)


def _bayrak_keyboard(options, key):
    row = [
        InlineKeyboardButton(emoji, callback_data=f"bayrak_pick_{key}_{i}")
        for i, (emoji, _name) in enumerate(options)
    ]
    return InlineKeyboardMarkup([row])


async def _bayrak_send_round(chat, key):
    game = PENDING_GAMES[key]
    text = (
        f"🚩 Hangi ülkenin bayrağı bu?\n\n"
        f"🚩 TUR: {game['round']}/{BAYRAK_ROUNDS} | ❤️: {game['lives']}\n"
        f"⏱️ Süre: {BAYRAK_TIME} Saniye"
    )
    return await chat.send_message(text, reply_markup=_bayrak_keyboard(game["options"], key))


def _bayrak_start_timer(context: ContextTypes.DEFAULT_TYPE, key):
    old = BAYRAK_TIMERS.get(key)
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(_bayrak_timer_loop(context, key))
    BAYRAK_TIMERS[key] = task


async def _bayrak_timer_loop(context: ContextTypes.DEFAULT_TYPE, key):
    try:
        game = PENDING_GAMES.get(key)
        if not game:
            return
        chat_id = game["chat_id"]
        message_id = game["message_id"]

        for remaining in range(BAYRAK_TIME - 1, -1, -1):
            await asyncio.sleep(1)
            game = PENDING_GAMES.get(key)
            if not game:
                return  # oyun artiq cavablandi / bitdi
            text = (
                f"🚩 Hangi ülkenin bayrağı bu?\n\n"
                f"🚩 TUR: {game['round']}/{BAYRAK_ROUNDS} | ❤️: {game['lives']}\n"
                f"⏱️ Süre: {remaining} Saniye"
            )
            try:
                await context.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=_bayrak_keyboard(game["options"], key),
                )
            except BadRequest:
                pass

        # vaxt bitdi
        game = PENDING_GAMES.pop(key, None)
        if not game:
            return
        new_bal = change_balance(game["user_id"], game["bet"])  # bahis iade
        text = (
            "⏱️ Süre Doldu!\n"
            "15 saniye içinde cevap verilmediği için oyun iptal edildi ve bakiye iade edildi.\n"
            f"💳 Bakiye: {fmt(new_bal)} TL"
        )
        try:
            await context.bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
    except asyncio.CancelledError:
        pass


async def bayrak_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, _, key, idx_str = query.data.split("_", 3)
    game = PENDING_GAMES.get(key)

    if not game or game["user_id"] != user.id:
        await query.answer("⚠️ Bu yarış artık geçerli değil.", show_alert=True)
        return

    await query.answer()

    idx = int(idx_str)
    picked = game["options"][idx]
    correct = game["correct"]

    timer_task = BAYRAK_TIMERS.get(key)
    if timer_task and not timer_task.done():
        timer_task.cancel()

    if picked == correct:
        await safe_edit(query, "✅ Doğru Cevap!")
        await asyncio.sleep(0.7)

        if game["round"] >= BAYRAK_ROUNDS:
            win = int(game["bet"] * 2.5)
            new_bal = change_balance(user.id, win)
            del PENDING_GAMES[key]
            await query.message.chat.send_message(
                f"🏁 TÜM TURLAR BİTTİ!\n✅ KAZANDIN! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
            )
            return

        game["round"] += 1
        new_correct, new_options = build_bayrak_round()
        game["correct"] = new_correct
        game["options"] = new_options
        msg = await _bayrak_send_round(query.message.chat, key)
        game["message_id"] = msg.message_id
        _bayrak_start_timer(context, key)
    else:
        game["lives"] -= 1
        if game["lives"] <= 0:
            bet = game["bet"]
            del PENDING_GAMES[key]
            await safe_edit(
                query,
                f"❌ Yanlış! Doğru Cevap: {correct[1]}\n\n"
                f"💀 BİTTİ! ❌: 3\n📉: -{fmt(bet)} TL",
            )
            return

        await safe_edit(query, f"❌ Yanlış! Doğru Cevap: {correct[1]}")
        await asyncio.sleep(0.7)

        new_correct, new_options = build_bayrak_round()
        game["correct"] = new_correct
        game["options"] = new_options
        msg = await _bayrak_send_round(query.message.chat, key)
        game["message_id"] = msg.message_id
        _bayrak_start_timer(context, key)

# ----------------------------------------------------------------------
# 3) TAS KAGIT MAKAS (Bot ile / PvP Duello)
# ----------------------------------------------------------------------

TKM_EMOJI = {"tas": "✊", "kagit": "📄", "makas": "✂️"}
TKM_NAME = {"tas": "Taş", "kagit": "Kağıt", "makas": "Makas"}
TKM_BEATS = {"tas": "makas", "kagit": "tas", "makas": "kagit"}


async def tkm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "✂️ Taş Kağıt Makas oynamak için lütfen şu formatta yazın:\n`/tkm [miktar]`\n"
            "Örnek: `/tkm 200`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    key = f"tkm_setup_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "user_name": user.first_name, "bet": bet}

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("1️⃣ 1 Tur", callback_data=f"tkm_rounds_{key}_1"),
            InlineKeyboardButton("3️⃣ 3 Tur", callback_data=f"tkm_rounds_{key}_3"),
            InlineKeyboardButton("5️⃣ 5 Tur", callback_data=f"tkm_rounds_{key}_5"),
        ]]
    )
    text = f"✂️ Taş Kağıt Makas\n💰 Bahis: {fmt(bet)} TL\nKaç tur oynanacak?"
    await reply(update, text, reply_markup=keyboard)


async def tkm_rounds_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, _, key, rounds_str = query.data.split("_", 3)
    setup = PENDING_GAMES.get(key)
    if not setup or setup["user_id"] != user.id:
        await safe_edit(query, "⚠️ Bu oyun artık geçerli değil.")
        return

    target_rounds = int(rounds_str)
    setup["target_rounds"] = target_rounds

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🤖 Bot İle Oyna", callback_data=f"tkm_mode_{key}_bot")],
            [InlineKeyboardButton("⚔️ Düello (PvP)", callback_data=f"tkm_mode_{key}_pvp")],
        ]
    )
    text = (
        f"🎮 Oyun Modu Seçin\n💰 Bahis: {fmt(setup['bet'])} TL\n"
        f"🏆 Hedeflenen Tur: {target_rounds}"
    )
    await safe_edit(query, text, reply_markup=keyboard)


def _tkm_choice_keyboard(prefix, key):
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✊ Taş", callback_data=f"{prefix}_{key}_tas"),
            InlineKeyboardButton("📄 Kağıt", callback_data=f"{prefix}_{key}_kagit"),
            InlineKeyboardButton("✂️ Makas", callback_data=f"{prefix}_{key}_makas"),
        ]]
    )


async def tkm_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, _, key, mode = query.data.split("_", 3)
    setup = PENDING_GAMES.get(key)
    if not setup or setup["user_id"] != user.id:
        await safe_edit(query, "⚠️ Bu oyun artık geçerli değil.")
        return

    if mode == "bot":
        bal = get_balance(user.id)
        bet = setup["bet"]
        if bal < bet:
            del PENDING_GAMES[key]
            await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
            return
        change_balance(user.id, -bet)

        game_key = f"tkmbot_{key}"
        PENDING_GAMES[game_key] = {
            "user_id": user.id,
            "user_name": setup["user_name"],
            "bet": bet,
            "target_rounds": setup["target_rounds"],
            "round": 1,
            "user_score": 0,
            "bot_score": 0,
        }
        del PENDING_GAMES[key]

        text = _tkm_bot_status_text(game_key)
        await safe_edit(query, text, reply_markup=_tkm_choice_keyboard("tkmb", game_key))
    else:
        bal = get_balance(user.id)
        bet = setup["bet"]
        if bal < bet:
            del PENDING_GAMES[key]
            await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
            return
        change_balance(user.id, -bet)

        duel_key = f"tkmduel_{key}"
        PENDING_GAMES[duel_key] = {
            "creator_id": user.id,
            "creator_name": setup["user_name"],
            "bet": bet,
            "target_rounds": setup["target_rounds"],
            "status": "waiting",
        }
        del PENDING_GAMES[key]

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🤝 Katıl", callback_data=f"tkmjoin_{duel_key}")]]
        )
        text = (
            f"🤝 TKM DÜELLO ÇAĞRISI\n👤 Kurucu: {setup['user_name']}\n"
            f"💰 Bahis: {fmt(bet)} TL\n🏆 Tur: {setup['target_rounds']}\n\n"
            "Rakip bekleniyor..."
        )
        await safe_edit(query, text, reply_markup=keyboard)


def _tkm_bot_status_text(game_key, extra=""):
    game = PENDING_GAMES[game_key]
    base = (
        f"🎮 TAŞ - KAĞIT - MAKAS\n"
        f"🔴 {game['user_name']}: {game['user_score']}\n"
        f"🔵 🤖 Bot: {game['bot_score']}\n\n"
        f"🚩 Tur: {game['round']}/{game['target_rounds']}\n"
        f"💰 Bahis: {fmt(game['bet'])} TL\n\n"
        f"👇 Seçimini yap!"
    )
    if extra:
        return f"{extra}\n\n{base}"
    return base


async def tkm_bot_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    prefix, key, choice = query.data.rsplit("_", 2)
    game = PENDING_GAMES.get(key)
    if not game or game["user_id"] != user.id:
        await safe_edit(query, "⚠️ Bu oyun artık geçerli değil.")
        return

    bot_choice = random.choice(list(TKM_EMOJI.keys()))
    rnd = game["round"]

    if choice == bot_choice:
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"🤝 Berabere! Her ikisi de {TKM_EMOJI[choice]} yaptı."
        )
    elif TKM_BEATS[choice] == bot_choice:
        game["user_score"] += 1
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"✅ {game['user_name']} kazandı! {TKM_EMOJI[choice]} vs {TKM_EMOJI[bot_choice]}"
        )
    else:
        game["bot_score"] += 1
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"❌ 🤖 Bot kazandı! {TKM_EMOJI[choice]} vs {TKM_EMOJI[bot_choice]}"
        )

    target = game["target_rounds"]
    finished = rnd >= target

    if not finished:
        game["round"] += 1
        text = _tkm_bot_status_text(key, extra=result_text)
        await safe_edit(query, text, reply_markup=_tkm_choice_keyboard("tkmb", key))
        return

    # oyun bitdi -> qalibi skor ile tesbit et
    bet = game["bet"]
    if game["user_score"] == game["bot_score"]:
        new_bal = change_balance(user.id, bet)  # bahis iade
        final_text = (
            f"{result_text}\n\n"
            "🤝 OYUN BİTTİ! Skorlar eşit, bakiye iade edildi!\n"
            f"💳 Bakiye: {fmt(new_bal)} TL"
        )
    elif game["user_score"] > game["bot_score"]:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        final_text = (
            f"{result_text}\n\n"
            f"🏆 OYUN BİTTİ! Kazanan: {game['user_name']}\n"
            f"💰 Kazanç: +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        )
    else:
        new_bal = get_balance(user.id)
        final_text = (
            f"{result_text}\n\n"
            "💀 OYUN BİTTİ! Kazanan: Bot\n"
            f"📉 Kayıp: -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        )

    del PENDING_GAMES[key]
    await safe_edit(query, final_text)


# ---- TKM DUELLO (PvP) ----

async def tkm_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    duel_key = query.data.replace("tkmjoin_", "")
    game = PENDING_GAMES.get(duel_key)

    if not game or game["status"] != "waiting":
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return

    if user.id == game["creator_id"]:
        await query.answer("⚠️ Kendi Oyunun!", show_alert=True)
        return

    await query.answer()

    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = game["bet"]
    bal = get_balance(user.id)
    if bal < bet:
        await query.answer("Bakiyən yetersiz!", show_alert=True)
        return

    change_balance(user.id, -bet)

    game.update(
        {
            "status": "active",
            "opponent_id": user.id,
            "opponent_name": user.first_name,
            "round": 1,
            "score": {game["creator_id"]: 0, user.id: 0},
            "moves": {},
        }
    )

    text = _tkm_duel_status_text(duel_key)
    await safe_edit(query, text, reply_markup=_tkm_choice_keyboard("tkmd", duel_key))


def _tkm_duel_status_text(duel_key, extra=""):
    game = PENDING_GAMES[duel_key]
    c_id, o_id = game["creator_id"], game["opponent_id"]
    base = (
        f"🎮 TAŞ - KAĞIT - MAKAS (DÜELLO)\n"
        f"🔴 {game['creator_name']}: {game['score'][c_id]}\n"
        f"🔵 {game['opponent_name']}: {game['score'][o_id]}\n\n"
        f"🚩 Tur: {game['round']}/{game['target_rounds']}\n"
        f"💰 Bahis: {fmt(game['bet'])} TL\n\n"
        f"👇 Seçimini yap!"
    )
    if extra:
        return f"{extra}\n\n{base}"
    return base


async def tkm_duel_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, duel_key, choice = query.data.split("_", 2)
    game = PENDING_GAMES.get(duel_key)

    if not game or game.get("status") != "active":
        await query.answer("⚠️ Bu oyun artık geçerli değil.", show_alert=True)
        return

    if user.id not in (game["creator_id"], game["opponent_id"]):
        await query.answer("Bu oyunda değilsin!", show_alert=True)
        return

    if user.id in game["moves"]:
        await query.answer("Bu tur için zaten seçim yaptın!", show_alert=True)
        return

    await query.answer()
    game["moves"][user.id] = choice

    c_id, o_id = game["creator_id"], game["opponent_id"]
    if len(game["moves"]) < 2:
        return  # diger oyuncu hele secmeyib

    c_choice = game["moves"][c_id]
    o_choice = game["moves"][o_id]
    rnd = game["round"]

    if c_choice == o_choice:
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"🤝 Berabere! Her ikisi de {TKM_EMOJI[c_choice]} yaptı."
        )
    elif TKM_BEATS[c_choice] == o_choice:
        game["score"][c_id] += 1
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"✅ {game['creator_name']} kazandı! {TKM_EMOJI[c_choice]} vs {TKM_EMOJI[o_choice]}"
        )
    else:
        game["score"][o_id] += 1
        result_text = (
            f"🔄 TUR {rnd} SONUCU\n"
            f"✅ {game['opponent_name']} kazandı! {TKM_EMOJI[o_choice]} vs {TKM_EMOJI[c_choice]}"
        )

    target = game["target_rounds"]
    finished = rnd >= target

    if not finished:
        game["round"] += 1
        game["moves"] = {}
        text = _tkm_duel_status_text(duel_key, extra=result_text)
        await query.message.edit_text(text, reply_markup=_tkm_choice_keyboard("tkmd", duel_key))
        return

    bet = game["bet"]
    s_c, s_o = game["score"][c_id], game["score"][o_id]

    if s_c == s_o:
        change_balance(c_id, bet)
        change_balance(o_id, bet)
        final_text = (
            f"{result_text}\n\n"
            "🤝 OYUN BİTTİ! Skorlar eşit, bakiyeler iade edildi!"
        )
    elif s_c > s_o:
        win = bet * 2
        change_balance(c_id, win)
        final_text = (
            f"{result_text}\n\n"
            f"🏆 OYUN BİTTİ! Kazanan: {game['creator_name']}\n💰 Kazanç: +{fmt(win)} TL"
        )
    else:
        win = bet * 2
        change_balance(o_id, win)
        final_text = (
            f"{result_text}\n\n"
            f"🏆 OYUN BİTTİ! Kazanan: {game['opponent_name']}\n💰 Kazanç: +{fmt(win)} TL"
        )

    del PENDING_GAMES[duel_key]
    await query.message.edit_text(final_text)

# ----------------------------------------------------------------------
# 4) YAZI TURA
# ----------------------------------------------------------------------

async def ytsans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🪙 Yazı-Tura oynamak için lütfen şu formatta yazın:\n`/ytsans [miktar]`\n"
            "Örnek: `/ytsans 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    key = f"yt_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "bet": bet}

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🪙 YAZI", callback_data=f"yt_pick_{key}_yazi"),
            InlineKeyboardButton("🪙 TURA", callback_data=f"yt_pick_{key}_tura"),
        ]]
    )
    await reply(update, f"🪙 {fmt(bet)} TL bahis. Seç:", reply_markup=keyboard)


async def ytsans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, _, key, choice = query.data.split("_", 3)
    game = PENDING_GAMES.get(key)
    if not game or game["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun artık geçerli değil.", show_alert=True)
        return

    bal = get_balance(user.id)
    bet = game["bet"]
    if bal < bet:
        del PENDING_GAMES[key]
        await query.answer("Bakiyən yetersiz!", show_alert=True)
        await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    await query.answer()
    change_balance(user.id, -bet)
    del PENDING_GAMES[key]

    await safe_edit(query, "🪙 Para havada dönüyor...")
    await asyncio.sleep(2)

    result = random.choice(["yazi", "tura"])
    result_label = "YAZI" if result == "yazi" else "TURA"

    if result == choice:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        text = f"✨ {result_label} geldi!\n✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text = f"✨ {result_label} geldi!\n❌ -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await safe_edit(query, text)


# ----------------------------------------------------------------------
# 5) BUL BENI (KUTU) - 3x3
# ----------------------------------------------------------------------

async def bulbeni_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🔍 Bul beni kutu oyunu için lütfen şu formatta yazın:\n`/bulbeni [miktar]`\n"
            "Örnek: `/bulbeni 300`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not await check_and_take_bet(update, user.id, bet):
        return

    key = f"bb_{user.id}_{int(time.time())}"
    winner_box = random.randint(0, 8)
    PENDING_GAMES[key] = {
        "user_id": user.id,
        "bet": bet,
        "winner": winner_box,
        "lives": 3,
        "opened": set(),
        "boxes": ["📦"] * 9,
    }

    text = f"🔍 {fmt(bet)} TL bahis!\n❤️ 3 Hak\nÖdül hangi kutuda?"
    await reply(update, text, reply_markup=_bulbeni_keyboard(key))


def _bulbeni_keyboard(key):
    game = PENDING_GAMES[key]
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            label = game["boxes"][i]
            row.append(InlineKeyboardButton(label, callback_data=f"bb_pick_{key}_{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def bulbeni_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, _, key, idx_str = query.data.split("_", 3)
    game = PENDING_GAMES.get(key)
    if not game or game["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun artık geçerli değil.", show_alert=True)
        return

    idx = int(idx_str)
    if idx in game["opened"]:
        await query.answer("Bu kutu zaten açıldı!", show_alert=True)
        return

    await query.answer()

    if idx == game["winner"]:
        win = game["bet"] * 2
        new_bal = change_balance(user.id, win)
        del PENDING_GAMES[key]
        game["boxes"][idx] = "💎"
        text = f"💎 BULDUN!\n✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        await safe_edit(query, text, reply_markup=_bulbeni_static_keyboard(game["boxes"]))
        return

    game["opened"].add(idx)
    game["boxes"][idx] = "❌"
    game["lives"] -= 1

    if game["lives"] <= 0:
        bet = game["bet"]
        del PENDING_GAMES[key]
        game["boxes"][game["winner"]] = "💎"
        text = f"💀 KAYIP! Ödül {fmt(bet)} idi.\n📉 -{fmt(bet)} TL"
        await safe_edit(query, text, reply_markup=_bulbeni_static_keyboard(game["boxes"]))
        return

    text = f"❌ Boş! ❤️: {game['lives']}"
    await safe_edit(query, text, reply_markup=_bulbeni_keyboard(key))


def _bulbeni_static_keyboard(boxes):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            row.append(InlineKeyboardButton(boxes[i], callback_data="bb_done"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def bulbeni_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ----------------------------------------------------------------------
# 6) XOX DUELLO (PvP)
# ----------------------------------------------------------------------

def xox_render_board(board):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            v = board[r * 3 + c]
            row.append(v if v != " " else "➖")
        rows.append(" | ".join(row))
    return "\n".join(rows)


def xox_keyboard(board, game_key):
    buttons = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            v = board[i]
            label = v if v != " " else "·"
            row.append(InlineKeyboardButton(label, callback_data=f"xoxmv_{game_key}_{i}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def xox_check_winner(board):
    lines = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for a, b, c in lines:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    if " " not in board:
        return "draw"
    return None


async def xox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "❌ XOX düellosu başlatmak için lütfen şu formatta yazın:\n`/xox [miktar]`\n"
            "Örnek: `/xox 500`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    game_key = f"{update.effective_chat.id}_{user.id}_{int(time.time())}"
    PENDING_GAMES[f"xoxwait_{game_key}"] = {
        "creator_id": user.id,
        "creator_name": user.first_name,
        "bet": bet,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚔️ Düelloya Katıl", callback_data=f"xoxjoin_{game_key}")],
            [InlineKeyboardButton("❌ Oyunu Kapat", callback_data=f"xoxcancel_{game_key}")],
        ]
    )
    text = (
        f"⚔️ XOX DÜELLO!\n💰: {fmt(bet)} TL\n👤 {user.first_name}\n\nrakip bekliyor..."
    )
    await reply(update, text, reply_markup=keyboard)


async def xox_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    game_key = query.data.replace("xoxjoin_", "")
    wait_key = f"xoxwait_{game_key}"
    pending = PENDING_GAMES.get(wait_key)

    if not pending:
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return

    if user.id == pending["creator_id"]:
        await query.answer("⚠️ Kendi Oyunun!", show_alert=True)
        return

    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = pending["bet"]
    bal = get_balance(user.id)
    if bal < bet:
        await query.answer("Bakiyən yetersiz!", show_alert=True)
        return

    await query.answer()
    change_balance(pending["creator_id"], -bet)
    change_balance(user.id, -bet)
    del PENDING_GAMES[wait_key]

    active_key = f"xoxactive_{game_key}"
    PENDING_GAMES[active_key] = {
        "board": [" "] * 9,
        "players": {pending["creator_id"]: "❌", user.id: "⭕"},
        "names": {pending["creator_id"]: pending["creator_name"], user.id: user.first_name},
        "turn": pending["creator_id"],
        "bet": bet,
    }

    game = PENDING_GAMES[active_key]
    text = (
        f"❌ {game['names'][pending['creator_id']]}  VS  ⭕ {user.first_name}\n"
        f"💰 Bahis: {fmt(bet)} TL (her oyuncudan alındı)\n\n"
        f"Sıra: {game['names'][game['turn']]} (❌)"
    )
    await safe_edit(query, text, reply_markup=xox_keyboard(game["board"], game_key))


async def xox_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    game_key = query.data.replace("xoxcancel_", "")
    wait_key = f"xoxwait_{game_key}"
    pending = PENDING_GAMES.get(wait_key)

    if not pending:
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return

    if user.id != pending["creator_id"]:
        await query.answer("Sadece kurucu kapatabilir!", show_alert=True)
        return

    await query.answer()
    del PENDING_GAMES[wait_key]
    await safe_edit(query, "❌ Oyun kurucu tarafından iptal edildi.")


async def xox_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, game_key, idx_str = query.data.split("_", 2)
    idx = int(idx_str)
    active_key = f"xoxactive_{game_key}"
    game = PENDING_GAMES.get(active_key)

    if not game:
        await query.answer("⚠️ Oyun bulunamadı veya bitti.", show_alert=True)
        return

    if user.id not in game["players"]:
        await query.answer("Bu oyunda değilsin!", show_alert=True)
        return

    if game["turn"] != user.id:
        await query.answer("Sıra sende değil!", show_alert=True)
        return

    if game["board"][idx] != " ":
        await query.answer("Burası dolu!", show_alert=True)
        return

    await query.answer()
    symbol = game["players"][user.id]
    game["board"][idx] = symbol

    result = xox_check_winner(game["board"])

    if result == "draw":
        bet = game["bet"]
        for pid in game["players"]:
            change_balance(pid, bet)
        text = f"{xox_render_board(game['board'])}\n\n🤝 Berabere! Bahisler geri verildi."
        del PENDING_GAMES[active_key]
        await safe_edit(query, text)
        return

    if result is not None:
        winner_id = [pid for pid, s in game["players"].items() if s == result][0]
        win_amount = game["bet"] * 2
        new_bal = change_balance(winner_id, win_amount)
        text = (
            f"{xox_render_board(game['board'])}\n\n"
            f"🏆 {game['names'][winner_id]} kazandı! +{fmt(win_amount)} TL"
        )
        del PENDING_GAMES[active_key]
        await safe_edit(query, text)
        return

    other_id = [pid for pid in game["players"] if pid != user.id][0]
    game["turn"] = other_id

    text = (
        f"{xox_render_board(game['board'])}\n\n"
        f"Sıra: {game['names'][other_id]} ({game['players'][other_id]})"
    )
    await safe_edit(query, text, reply_markup=xox_keyboard(game["board"], game_key))

# ----------------------------------------------------------------------
# 7) SAYI TAHMIN (1-100, pot sistemi)
# ----------------------------------------------------------------------

async def tahminet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🔢 Sayı tahmin oyunu için lütfen şu formatta yazın:\n`/tahminet [miktar]`\n"
            "Örnek: `/tahminet 50`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not await check_and_take_bet(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    number = random.randint(1, 100)
    GUESS_NUMBER_STATE[chat_id] = {
        "number": number,
        "user_id": user.id,
        "bet": bet,
        "pot": bet,
        "tries": 0,
    }

    await reply(
        update,
        f"🔢 Sayı Tahmin! (0-100)\n💰 Bahis: {fmt(bet)} TL\nSayıyı yaz!",
    )


async def handle_number_guess_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = GUESS_NUMBER_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return

    text = update.effective_message.text.strip()
    if not text.lstrip("-").isdigit():
        return

    guess = int(text)
    number = state["number"]

    if guess == number:
        win = state["pot"] * 2
        new_bal = change_balance(user.id, win)
        await reply(
            update,
            f"🎉 Tebrikler! Doğru sayı {number} idi!\n+{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
        )
        del GUESS_NUMBER_STATE[chat_id]
        return

    state["tries"] += 1
    if state["tries"] >= 10:
        new_bal = get_balance(user.id)
        await reply(
            update,
            f"💀 Elendin! Sayı: {number}\n💳 Bakiye: {fmt(new_bal)} TL",
        )
        del GUESS_NUMBER_STATE[chat_id]
        return

    # pot, her cehdden sonra azalir/cekilir; tahmin yonunu de gosterir
    pot_step = max(state["bet"] // 10, 1)
    state["pot"] = max(state["pot"] - pot_step, pot_step)

    direction = "📈 Yukarı" if guess < number else "📉 Aşağı"
    await reply(update, f"{direction}\n💰 Pot: {fmt(state['pot'])} TL")


# ----------------------------------------------------------------------
# 8) BULMACA (ADAM ASMACA) - zorluk secimli
# ----------------------------------------------------------------------

HANGMAN_WORDS = {
    "kolay": ["GÜL", "KOL", "SU", "EV", "AT", "KAR", "SAÇ", "GÖL"],
    "orta": ["MASA", "KAPI", "KEDİ", "KİTAP", "BALIK", "ORMAN", "DENİZ", "GÜNEŞ"],
    "zor": ["PENCERE", "BİLGİSAYAR", "YAĞMURLUK", "KAHVALTI", "ÖĞRETMEN", "TELEFON"],
}


async def bulmaca_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🧩 Adam asmaca oyunu için lütfen şu formatta yazın:\n`/bulmaca [miktar]`\n"
            "Örnek: `/bulmaca 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    key = f"hm_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "bet": bet}

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🟢 Kolay", callback_data=f"hmdiff_{key}_kolay"),
            InlineKeyboardButton("🟡 Orta", callback_data=f"hmdiff_{key}_orta"),
            InlineKeyboardButton("🔴 Zor", callback_data=f"hmdiff_{key}_zor"),
        ]]
    )
    text = f"🧩 Bulmaca (Adam Asmaca)\n💰 Bahis: {fmt(bet)} TL\nZorluk seçimi yapın:"
    await reply(update, text, reply_markup=keyboard)


def _hangman_render(word, guessed):
    return " ".join(c if c in guessed else "_" for c in word)


async def bulmaca_diff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, key, diff = query.data.split("_", 2)
    setup = PENDING_GAMES.get(key)
    if not setup or setup["user_id"] != user.id:
        await safe_edit(query, "⚠️ Bu oyun artık geçerli değil.")
        return

    bal = get_balance(user.id)
    bet = setup["bet"]
    if bal < bet:
        del PENDING_GAMES[key]
        await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)
    del PENDING_GAMES[key]

    word = random.choice(HANGMAN_WORDS[diff])
    diff_label = {"kolay": "Kolay", "orta": "Orta", "zor": "Zor"}[diff]

    chat_id = update.effective_chat.id
    HANGMAN_STATE[chat_id] = {
        "user_id": user.id,
        "word": word,
        "guessed": set(),
        "lives": 3,
        "bet": bet,
        "diff": diff_label,
    }

    text = (
        f"🎮 Bulmaca başladı! Zorluk: {diff_label}\n"
        f"Kelime: `{_hangman_render(word, set())}`\n"
        f"({len(word)} harf) ❤️ Hak: 3\n\n"
        f"👉 Bir harf gönderin!"
    )
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN)


async def handle_hangman_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = HANGMAN_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return

    letter = update.effective_message.text.strip().upper()
    if len(letter) != 1 or not letter.isalpha():
        return

    word = state["word"]

    if letter in state["guessed"]:
        return

    state["guessed"].add(letter)

    if letter not in word:
        state["lives"] -= 1
        if state["lives"] <= 0:
            bet = state["bet"]
            del HANGMAN_STATE[chat_id]
            await reply(update, f"💀 ELENDİN! Kelime: `{word}`\n📉 Kayıp: -{fmt(bet)} TL", parse_mode=ParseMode.MARKDOWN)
            return
        await reply(
            update,
            f"❌ Yanlış! ❤️ Kalan Hak: {state['lives']}\nKelime: `{_hangman_render(word, state['guessed'])}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # dogru herf
    if all(c in state["guessed"] for c in word):
        bet = state["bet"]
        win = bet * 3
        new_bal = change_balance(user.id, win)
        del HANGMAN_STATE[chat_id]
        await reply(
            update,
            f"🎉 BULDUN! Kelime: `{word}`\n✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await reply(
        update,
        f"✅ Doğru!\nKelime: `{_hangman_render(word, state['guessed'])}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ----------------------------------------------------------------------
# 9) KELIME ZINCIRI
# ----------------------------------------------------------------------

WORD_CHAIN_SEED_WORDS = ["MASA", "KİTAP", "ARABA", "DENİZ", "GÜNEŞ", "BAHÇE", "TELEFON", "ORMAN", "BALIK", "TARAK"]
WORD_CHAIN_FOLLOWUPS = ["ARMUT", "TARAK", "KEDİ", "İNCİ", "CİCİ", "ORMAN", "NAR", "RUYA", "ASMA", "AYNA"]


async def kelime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "📝 Kelime zinciri oyunu için lütfen şu formatta yazın:\n`/kelime [miktar]`\n"
            "Örnek: `/kelime 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    key = f"kz_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "bet": bet}

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶️ Başla", callback_data=f"kzstart_{key}")]]
    )
    text = (
        "📝 Kelime Zinciri Başlıyor!\n"
        "📖 Kural: Bot bir kelime verir, sen son harfiyle yeni bir kelime yazarsın. "
        "Sonra bot senin kelimenin son harfiyle devam eder.\n"
        "⚠️ Önemli: Sadece harflerden oluşan gerçek kelimeler yazmalısın.\n"
        f"💰 Kazanç: Her doğru kelime için bahsinin 1.3x katını kazanırsın."
    )
    await reply(update, text, reply_markup=keyboard)


async def kelime_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    key = query.data.replace("kzstart_", "")
    setup = PENDING_GAMES.get(key)
    if not setup or setup["user_id"] != user.id:
        await safe_edit(query, "⚠️ Bu oyun artık geçerli değil.")
        return

    bal = get_balance(user.id)
    bet = setup["bet"]
    if bal < bet:
        del PENDING_GAMES[key]
        await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)
    del PENDING_GAMES[key]

    chat_id = update.effective_chat.id
    seed = random.choice(WORD_CHAIN_SEED_WORDS)
    WORD_CHAIN_STATE[chat_id] = {
        "last_word": seed,
        "used_words": {seed},
        "user_id": user.id,
        "bet": bet,
    }

    stop_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Oyunu Sonlandır", callback_data=f"kzstop_{chat_id}")]]
    )
    text = f"🤖 Botun Kelimesi: {seed}\n👉 {seed[-1]} harfi ile bir kelime yaz!"
    await safe_edit(query, text)
    await query.message.chat.send_message(text, reply_markup=stop_keyboard)


async def kelime_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    chat_id_str = query.data.replace("kzstop_", "")
    chat_id = int(chat_id_str)
    state = WORD_CHAIN_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        await query.answer("⚠️ Bu oyunda değilsin veya oyun bitti.", show_alert=True)
        return

    await query.answer()
    del WORD_CHAIN_STATE[chat_id]
    await safe_edit(query, "🛑 Kelime zinciri durduruldu.")


async def handle_word_chain_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = WORD_CHAIN_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return

    word = update.effective_message.text.strip().upper()
    if not word.isalpha():
        return

    last_word = state["last_word"]
    last_letter = last_word[-1]

    if word[0] != last_letter:
        bet = state["bet"]
        del WORD_CHAIN_STATE[chat_id]
        await reply(
            update,
            f"❌ Yanlış harf! Kelime {last_letter} ile başlamalıydı.\n📉 Kayıp: -{fmt(bet)} TL",
        )
        return

    if word in state["used_words"]:
        await reply(update, "❌ Bu kelime zaten kullanıldı! Başka bir kelime dene.")
        return

    state["used_words"].add(word)
    reward = int(state["bet"] * 1.3)
    new_bal = change_balance(user.id, reward)

    bot_word = random.choice(WORD_CHAIN_FOLLOWUPS)
    tries = 0
    while (bot_word[0] != word[-1] or bot_word in state["used_words"]) and tries < 20:
        bot_word = random.choice(WORD_CHAIN_FOLLOWUPS)
        tries += 1
    if bot_word[0] != word[-1]:
        bot_word = word[-1] + "ARA"  # son care fallback

    state["used_words"].add(bot_word)
    state["last_word"] = bot_word

    stop_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Oyunu Sonlandır", callback_data=f"kzstop_{chat_id}")]]
    )
    await reply(
        update,
        f"✅ Doğru! {word} yazdın. +{fmt(reward)} TL kazandın. Bakiye: {fmt(new_bal)} TL",
    )
    await update.effective_chat.send_message(
        f"🤖 Botun Kelimesi: {bot_word}\n👉 {bot_word[-1]} harfi ile bir kelime yaz!",
        reply_markup=stop_keyboard,
    )

# ----------------------------------------------------------------------
# 10) RUS RULETI
# ----------------------------------------------------------------------

async def ruleti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🔫 Rus ruleti oynamak için lütfen şu formatta yazın:\n`/ruleti [miktar]`\n"
            "Örnek: `/ruleti 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    bullet_chamber = random.randint(1, 6)
    key = f"ruleti_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "bet": bet, "bullet": bullet_chamber}

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"rul_{key}_{i}") for i in range(1, 4)],
            [InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"rul_{key}_{i}") for i in range(4, 7)],
        ]
    )
    await reply(update, "🔫 Rus Ruleti! 6 odadan birinde mermi var. Bir oda seç:", reply_markup=keyboard)


async def ruleti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, key, picked_str = query.data.split("_", 2)
    game = PENDING_GAMES.get(key)
    if not game or game["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun artık geçerli değil.", show_alert=True)
        return

    await query.answer()
    picked = int(picked_str)
    bullet = game["bullet"]
    bet = game["bet"]

    bal = get_balance(user.id)
    if bal < bet:
        del PENDING_GAMES[key]
        await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    change_balance(user.id, -bet)
    del PENDING_GAMES[key]

    if picked == bullet:
        new_bal = get_balance(user.id)
        text = f"🔫 BANG! 💥 Oda {bullet} doluydu!\n😢 Kaybettin! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        win = int(bet * 1.8)
        new_bal = change_balance(user.id, win)
        text = f"🔫 *click* Şanslısın, mermi Oda {bullet}'daydı!\n🎉 +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await safe_edit(query, text)


# ----------------------------------------------------------------------
# 11) AT YARISI
# ----------------------------------------------------------------------

HORSES = ["🐎 Yıldırım", "🐎 Kartal", "🐎 Fırtına", "🐎 Şimşek", "🐎 Rüzgar"]


async def atyarisi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet_strict(context.args)

    if bet is None:
        await reply(
            update,
            "🐎 At yarışı oynamak için lütfen şu formatta yazın:\n`/atyarisi [miktar]`\n"
            "Örnek: `/atyarisi 100`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    winner = random.randint(0, len(HORSES) - 1)
    key = f"at_{user.id}_{int(time.time())}"
    PENDING_GAMES[key] = {"user_id": user.id, "bet": bet, "winner": winner}

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(h, callback_data=f"aty_{key}_{i}")] for i, h in enumerate(HORSES)]
    )
    await reply(update, "🐎 At Yarışı başlıyor! Bahis yapacağın atı seç:", reply_markup=keyboard)


async def atyarisi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    _, key, picked_str = query.data.split("_", 2)
    game = PENDING_GAMES.get(key)
    if not game or game["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun artık geçerli değil.", show_alert=True)
        return

    await query.answer()
    picked = int(picked_str)
    winner = game["winner"]
    bet = game["bet"]

    bal = get_balance(user.id)
    if bal < bet:
        del PENDING_GAMES[key]
        await safe_edit(query, f"❌ Bakiyən kifayət etmir! 💳 Bakiyən: {fmt(bal)} TL")
        return

    change_balance(user.id, -bet)
    del PENDING_GAMES[key]

    order = list(range(len(HORSES)))
    random.shuffle(order)
    order.remove(winner)
    order.insert(0, winner)
    race_text = "🏁 Yarış sırası:\n" + "\n".join(f"{pos+1}. {HORSES[idx]}" for pos, idx in enumerate(order))

    if picked == winner:
        win = bet * 5
        new_bal = change_balance(user.id, win)
        text = f"{race_text}\n\n🎉 Atın kazandı! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text = f"{race_text}\n\n😢 Atın kaybetti! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await safe_edit(query, text)


# ----------------------------------------------------------------------
# GENEL TEXT MESAJ YONLENDIRICI
# (sayi tahmin, kelime zinciri, bulmaca ucun)
# ----------------------------------------------------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.text:
        return
    if update.effective_message.text.startswith("/"):
        return

    chat_id = update.effective_chat.id

    if chat_id in GUESS_NUMBER_STATE:
        await handle_number_guess_message(update, context)
        return
    if chat_id in HANGMAN_STATE:
        await handle_hangman_message(update, context)
        return
    if chat_id in WORD_CHAIN_STATE:
        await handle_word_chain_message(update, context)
        return


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN tapilmadi! Once: export BOT_TOKEN='token_in' (Linux/Mac) "
            "veya set BOT_TOKEN=token_in (Windows) komutu ile token-i ekle."
        )

    if not DATABASE_URL:
        raise SystemExit(
            "❌ DATABASE_URL tapilmadi! Railway-de PostgreSQL servisi elave et "
            "(avtomatik DATABASE_URL yaranir) ya da export DATABASE_URL='postgresql://...' ver."
        )

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Esas komandalar
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("ben", ben_cmd))
    app.add_handler(CommandHandler("odulum", odulum_cmd))
    app.add_handler(CommandHandler("zenginler", zenginler_cmd))
    app.add_handler(CommandHandler("yolla", yolla_cmd))

    # Oyun komandalari
    app.add_handler(CommandHandler("slot", slot_cmd))
    app.add_handler(CommandHandler("bayrak", bayrak_cmd))
    app.add_handler(CommandHandler("tkm", tkm_cmd))
    app.add_handler(CommandHandler("ytsans", ytsans_cmd))
    app.add_handler(CommandHandler("bulbeni", bulbeni_cmd))
    app.add_handler(CommandHandler("xox", xox_cmd))
    app.add_handler(CommandHandler("tahminet", tahminet_cmd))
    app.add_handler(CommandHandler("bulmaca", bulmaca_cmd))
    app.add_handler(CommandHandler("kelime", kelime_cmd))
    app.add_handler(CommandHandler("ruleti", ruleti_cmd))
    app.add_handler(CommandHandler("atyarisi", atyarisi_cmd))

    # Callback (inline buton) handlerlar
    app.add_handler(CallbackQueryHandler(odul_panel_callback, pattern=r"^odul_panel$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^info_"))

    app.add_handler(CallbackQueryHandler(bayrak_callback, pattern=r"^bayrak_pick_"))

    app.add_handler(CallbackQueryHandler(tkm_rounds_callback, pattern=r"^tkm_rounds_"))
    app.add_handler(CallbackQueryHandler(tkm_mode_callback, pattern=r"^tkm_mode_"))
    app.add_handler(CallbackQueryHandler(tkm_bot_move_callback, pattern=r"^tkmb_"))
    app.add_handler(CallbackQueryHandler(tkm_join_callback, pattern=r"^tkmjoin_"))
    app.add_handler(CallbackQueryHandler(tkm_duel_move_callback, pattern=r"^tkmd_"))

    app.add_handler(CallbackQueryHandler(ytsans_callback, pattern=r"^yt_pick_"))

    app.add_handler(CallbackQueryHandler(bulbeni_callback, pattern=r"^bb_pick_"))
    app.add_handler(CallbackQueryHandler(bulbeni_noop_callback, pattern=r"^bb_done$"))

    app.add_handler(CallbackQueryHandler(xox_join_callback, pattern=r"^xoxjoin_"))
    app.add_handler(CallbackQueryHandler(xox_cancel_callback, pattern=r"^xoxcancel_"))
    app.add_handler(CallbackQueryHandler(xox_move_callback, pattern=r"^xoxmv_"))

    app.add_handler(CallbackQueryHandler(bulmaca_diff_callback, pattern=r"^hmdiff_"))

    app.add_handler(CallbackQueryHandler(kelime_start_callback, pattern=r"^kzstart_"))
    app.add_handler(CallbackQueryHandler(kelime_stop_callback, pattern=r"^kzstop_"))

    app.add_handler(CallbackQueryHandler(ruleti_callback, pattern=r"^rul_"))
    app.add_handler(CallbackQueryHandler(atyarisi_callback, pattern=r"^aty_"))

    # Sade text mesajlari (sayi tahmin / kelime zinciri / bulmaca ucun)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot başladı...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
