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
    2. Bayraq Yarisi
    3. Tas Kagiz Qayci
    4. Yazi Tura
    5. Bul Meni (Kutu)
    6. X0X Duello
    7. Sayi Tahmin
    8. Boslug Doldurma
    9. Kelime Zinciri
    10. Rus Ruleti
    11. At Yarisi
    12. Profilim
    13. Saatlik Odul (/odulum)
"""

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
HOURLY_REWARD = 1000          # Saatlik odul mebedi
HOURLY_COOLDOWN_SECONDS = 60 * 60  # 1 saat

ADMIN_CONTACT = "@korunan"    # Bakiye almaq ucun elaqe

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Aktiv "duello" / "X0X" ve "at yarisi" kimi coxoyunculu oyunlar ucun yaddas
PENDING_GAMES = {}

# Kelime zinciri ucun aktiv oyun veziyyeti (chat_id -> data)
WORD_CHAIN_STATE = {}

# Bosluq doldurma ucun aktiv sual (chat_id -> data)
FILL_BLANK_STATE = {}

# Sayi tahmin ucun aktiv oyun (chat_id -> data)
GUESS_NUMBER_STATE = {}


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


# ----------------------------------------------------------------------
# KOMEKCI FUNKSIYALAR
# ----------------------------------------------------------------------

def fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


async def reply(update: Update, text: str, **kwargs):
    await update.effective_message.reply_text(text, **kwargs)


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


async def check_and_take_bet(update: Update, user_id: int, bet: int) -> bool:
    """Bakiye kifayet edirse bahisi cixir, true qaytarir. Yoxdursa xeberdarliq edib false qaytarir."""
    bal = get_balance(user_id)
    if bal < bet:
        await reply(
            update,
            f"❌ Bakiyen kifayet etmir!\n💳 Bakiyen: {fmt(bal)} TL, bahis: {fmt(bet)} TL.",
        )
        return False
    change_balance(user_id, -bet)
    return True


# ----------------------------------------------------------------------
# /start VE /menu
# ----------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    text = (
        f"👋 Selam {user.first_name}! kerhane eğlence botuna Hoş Geldin!\n"
        f"🎮 Eğlenirken bakiye kazanabileceğin oyunlar burada seni bekliyor.\n\n"
        f"👉 Oyunların listesini görmek ve başlamak için lütfen /menu yaz!"
    )
    await reply(update, text)


def build_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("1️⃣ Slot Makinesi", callback_data="info_slot")],
        [InlineKeyboardButton("2️⃣ Bayrak Yarışı", callback_data="info_bayrak")],
        [InlineKeyboardButton("3️⃣ Taş Kağıt Makas", callback_data="info_tkm")],
        [InlineKeyboardButton("4️⃣ Yazı Tura", callback_data="info_yazitura")],
        [InlineKeyboardButton("5️⃣ Bul Beni (Kutu)", callback_data="info_kutu")],
        [InlineKeyboardButton("6️⃣ X0X Duello", callback_data="info_x0x")],
        [InlineKeyboardButton("7️⃣ Sayı Tahmin", callback_data="info_sayitahmin")],
        [InlineKeyboardButton("8️⃣ Boşluk Doldurma", callback_data="info_boslukdoldurma")],
        [InlineKeyboardButton("9️⃣ Kelime Zincir", callback_data="info_kelimezincir")],
        [InlineKeyboardButton("🔟 Rus Ruleti", callback_data="info_rusruleti")],
        [InlineKeyboardButton("🐎 At Yarışı", callback_data="info_atyarisi")],
        [InlineKeyboardButton("👤 Profilim", callback_data="info_profil")],
    ]
    return InlineKeyboardMarkup(buttons)


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    text = (
        "🎰 KERHANE EGLENCE BOT - OYUN PANELİ\n"
        "Eğlenceli oyunlar, keyifli vakit. Aşağıdaki butonlardan oyunların "
        "komutlarını öğrenebilir, /ben yazıp bakiyenizi görebilirsiniz. "
        "(Veya profilim butonuna basın)\n\n"
        f"⚠️bakiye almak için {ADMIN_CONTACT} ile iletişime geçin."
    )
    await reply(update, text, reply_markup=build_menu_keyboard())


GAME_INFO_TEXT = {
    "info_slot": "🎰 *Slot Makinesi*\nKomanda: `/slot <bahis>`\nÖrnek: `/slot 200`",
    "info_bayrak": "🏁 *Bayrak Yarışı*\nKomanda: `/bayrak <bahis>`\nÖrnek: `/bayrak 100`",
    "info_tkm": "✊✋✌️ *Taş Kağıt Makas*\nKomanda: `/tkm <bahis>`\nSonra taş/kağıt/makas seç.",
    "info_yazitura": "🪙 *Yazı Tura*\nKomanda: `/yazitura <bahis> <yazi|tura>`\nÖrnek: `/yazitura 100 yazi`",
    "info_kutu": "📦 *Bul Beni (Kutu)*\nKomanda: `/kutu <bahis>`\n3 kutudan 1'ini seç, ödülü bul.",
    "info_x0x": "❌⭕ *X0X Duello*\nKomanda: `/x0x <bahis>`\nBaşka bir oyuncu katılana kadar bekler.",
    "info_sayitahmin": "🔢 *Sayı Tahmin*\nKomanda: `/sayitahmin <bahis>`\n1-100 arası sayıyı tahmin et.",
    "info_boslukdoldurma": "✏️ *Boşluk Doldurma*\nKomanda: `/boslukdoldurma <bahis>`\nCümledeki boşluğu doldur.",
    "info_kelimezincir": "🔗 *Kelime Zinciri*\nKomanda: `/kelimezincir <bahis>`\nSon harfle başlayan kelime yaz.",
    "info_rusruleti": "🔫 *Rus Ruleti*\nKomanda: `/rusruleti <bahis>`\n6 odadan 1'i dolu, şansını dene.",
    "info_atyarisi": "🐎 *At Yarışı*\nKomanda: `/atyarisi <bahis>`\n5 attan birine oyna.",
    "info_profil": None,  # ayriica handle olunur
}


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "info_profil":
        await send_profile(update, context, via_callback=True)
        return

    text = GAME_INFO_TEXT.get(data)
    if text:
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------------
# PROFIL VE SAATLIK ODUL
# ----------------------------------------------------------------------

async def send_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, via_callback=False):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bal = get_balance(user.id)
    text = (
        "👤 PROFİL\n"
        f"📛 İsim: {user.first_name}\n"
        f"💳 Bakiye: {fmt(bal)} TL"
    )
    if via_callback:
        await update.callback_query.message.reply_text(text)
    else:
        await reply(update, text)


async def ben_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_profile(update, context)


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
        "🎁 Saatlik Ödül Paneli\n"
        f"💰 {fmt(HOURLY_REWARD)} TL bakiyene eklendi!\n"
        f"💳 Yeni bakiyen: {fmt(new_balance)} TL",
    )


# ----------------------------------------------------------------------
# 1) SLOT MAKINESI
# ----------------------------------------------------------------------

SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "💎"]
SLOT_MULTIPLIERS = {
    "💎💎💎": 10,
    "⭐⭐⭐": 7,
    "🔔🔔🔔": 5,
    "🍇🍇🍇": 4,
    "🍋🍋🍋": 3,
    "🍒🍒🍒": 2,
}


async def slot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    if not await check_and_take_bet(update, user.id, bet):
        return

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    combo = "".join(reels)
    display = " | ".join(reels)

    multiplier = SLOT_MULTIPLIERS.get(combo, 0)
    if multiplier == 0 and reels[0] == reels[1] == reels[2]:
        multiplier = 2  # tehlukesizlik ucun
    if multiplier == 0 and len(set(reels)) == 2:
        multiplier = 0.5  # 2 eyni simvol ucun kicik mukafat

    win = int(bet * multiplier)

    text = f"🎰 [ {display} ]\n"
    if win > 0:
        new_bal = change_balance(user.id, win)
        text += f"🎉 Tebrikler! {fmt(win)} TL qazandın!\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text += f"😢 Bu sefer uğursuz oldun. -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await reply(update, text)


# ----------------------------------------------------------------------
# 2) BAYRAK YARISI
# ----------------------------------------------------------------------

FLAGS = ["🇹🇷", "🇦🇿", "🇩🇪", "🇫🇷", "🇧🇷", "🇯🇵"]


async def bayrak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    if not await check_and_take_bet(update, user.id, bet):
        return

    chosen_flags = random.sample(FLAGS, 4)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f, callback_data=f"bayrak_pick_{i}_{bet}") for i, f in enumerate(chosen_flags)]]
    )
    PENDING_GAMES[f"bayrak_{user.id}"] = {"flags": chosen_flags, "winner": random.randint(0, 3), "bet": bet}

    await reply(update, "🏁 Yarış başlıyor! Kazanacağını düşündüğün bayrağı seç:", reply_markup=keyboard)


async def bayrak_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, _, idx_str, bet_str = query.data.split("_")
    idx = int(idx_str)
    bet = int(bet_str)

    game = PENDING_GAMES.pop(f"bayrak_{user.id}", None)
    if not game:
        await query.edit_message_text("⚠️ Bu yarış artık geçerli değil.")
        return

    winner_idx = game["winner"]
    flags = game["flags"]

    if idx == winner_idx:
        win = bet * 4
        new_bal = change_balance(user.id, win)
        text = (
            f"🏁 Kazanan bayrak: {flags[winner_idx]}\n"
            f"🎉 Doğru tahmin! {fmt(win)} TL qazandın!\n💳 Bakiye: {fmt(new_bal)} TL"
        )
    else:
        new_bal = get_balance(user.id)
        text = (
            f"🏁 Kazanan bayrak: {flags[winner_idx]}\n"
            f"😢 Yanlış tahmin. -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        )

    await query.edit_message_text(text)


# ----------------------------------------------------------------------
# 3) TAS KAGIZ MAKAS
# ----------------------------------------------------------------------

TKM_OPTIONS = {"tas": "✊", "kagit": "✋", "makas": "✌️"}
TKM_BEATS = {"tas": "makas", "kagit": "tas", "makas": "kagit"}


async def tkm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✊ Taş", callback_data=f"tkm_tas_{bet}"),
            InlineKeyboardButton("✋ Kağıt", callback_data=f"tkm_kagit_{bet}"),
            InlineKeyboardButton("✌️ Makas", callback_data=f"tkm_makas_{bet}"),
        ]]
    )
    await reply(update, "✊✋✌️ Seçimini yap:", reply_markup=keyboard)


async def tkm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, user_choice, bet_str = query.data.split("_")
    bet = int(bet_str)

    bal = get_balance(user.id)
    if bal < bet:
        await query.edit_message_text(f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

    bot_choice = random.choice(list(TKM_OPTIONS.keys()))

    if user_choice == bot_choice:
        new_bal = change_balance(user.id, bet)  # bahis geri qaytarilir
        result = "🤝 Berabere! Bahisin geri qaytarıldı."
    elif TKM_BEATS[user_choice] == bot_choice:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        result = f"🎉 Kazandın! +{fmt(win)} TL"
    else:
        new_bal = get_balance(user.id)
        result = f"😢 Kaybettin! -{fmt(bet)} TL"

    text = (
        f"Sen: {TKM_OPTIONS[user_choice]}   Bot: {TKM_OPTIONS[bot_choice]}\n"
        f"{result}\n💳 Bakiye: {fmt(new_bal)} TL"
    )
    await query.edit_message_text(text)


# ----------------------------------------------------------------------
# 4) YAZI TURA
# ----------------------------------------------------------------------

async def yazitura_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    args = context.args
    bet = 100
    choice = None

    for a in args:
        if a.lower() in ("yazi", "tura"):
            choice = a.lower()
        else:
            try:
                bet = int(a)
            except ValueError:
                pass

    if choice is None:
        await reply(update, "Kullanım: /yazitura <bahis> <yazi|tura>\nÖrnek: /yazitura 100 yazi")
        return

    if not await check_and_take_bet(update, user.id, bet):
        return

    result = random.choice(["yazi", "tura"])
    if result == choice:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        text = f"🪙 Sonuç: {result.upper()}\n🎉 Kazandın! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text = f"🪙 Sonuç: {result.upper()}\n😢 Kaybettin! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await reply(update, text)


# ----------------------------------------------------------------------
# 5) BUL BENI (KUTU)
# ----------------------------------------------------------------------

async def kutu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return

    winner_box = random.randint(0, 2)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"📦 Kutu {i+1}", callback_data=f"kutu_{i}_{winner_box}_{bet}") for i in range(3)]]
    )
    await reply(update, "📦 Ödülün hangi kutuda olduğunu bul!", reply_markup=keyboard)


async def kutu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, picked_str, winner_str, bet_str = query.data.split("_")
    picked = int(picked_str)
    winner = int(winner_str)
    bet = int(bet_str)

    bal = get_balance(user.id)
    if bal < bet:
        await query.edit_message_text(f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

    if picked == winner:
        win = bet * 3
        new_bal = change_balance(user.id, win)
        text = f"📦 Kutu {winner+1} doğruydu!\n🎉 Kazandın! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text = f"📦 Doğru kutu: {winner+1}\n😢 Kaybettin! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await query.edit_message_text(text)


# ----------------------------------------------------------------------
# 6) X0X DUELLO (2 oyuncu)
# ----------------------------------------------------------------------

def x0x_render_board(board):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            v = board[r * 3 + c]
            row.append(v if v != " " else "➖")
        rows.append(" | ".join(row))
    return "\n".join(rows)


def x0x_keyboard(board, game_key):
    buttons = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            v = board[i]
            label = v if v != " " else "·"
            row.append(InlineKeyboardButton(label, callback_data=f"x0x_{game_key}_{i}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def x0x_check_winner(board):
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


async def x0x_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return

    game_key = f"{update.effective_chat.id}_{user.id}_{int(time.time())}"
    PENDING_GAMES[f"x0x_wait_{game_key}"] = {
        "creator_id": user.id,
        "creator_name": user.first_name,
        "bet": bet,
    }

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚔️ Düelloya Katıl", callback_data=f"x0xjoin_{game_key}")]]
    )
    await reply(
        update,
        f"❌⭕ {user.first_name} bir X0X düellosu başlattı!\n💰 Bahis: {fmt(bet)} TL\n"
        f"Katılmak için butona bas.",
        reply_markup=keyboard,
    )


async def x0x_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game_key = query.data.replace("x0xjoin_", "")
    wait_key = f"x0x_wait_{game_key}"
    pending = PENDING_GAMES.get(wait_key)

    if not pending:
        await query.edit_message_text("⚠️ Bu düello artık geçerli değil.")
        return

    if user.id == pending["creator_id"]:
        await query.answer("Kendi düellona katılamazsın!", show_alert=True)
        return

    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = pending["bet"]
    bal = get_balance(user.id)
    if bal < bet:
        await query.answer("Bakiyen yetersiz!", show_alert=True)
        return

    change_balance(pending["creator_id"], -bet)
    change_balance(user.id, -bet)
    del PENDING_GAMES[wait_key]

    active_key = f"x0x_active_{game_key}"
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
    await query.edit_message_text(text, reply_markup=x0x_keyboard(game["board"], game_key))


async def x0x_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, game_key, idx_str = query.data.split("_", 2)
    idx = int(idx_str)
    active_key = f"x0x_active_{game_key}"
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

    symbol = game["players"][user.id]
    game["board"][idx] = symbol

    result = x0x_check_winner(game["board"])

    if result == "draw":
        bet = game["bet"]
        for pid in game["players"]:
            change_balance(pid, bet)  # her ikisine de bahisi geri ver
        text = (
            f"{x0x_render_board(game['board'])}\n\n"
            f"🤝 Berabere! Bahisler geri verildi."
        )
        del PENDING_GAMES[active_key]
        await query.edit_message_text(text)
        return

    if result is not None:
        winner_id = [pid for pid, s in game["players"].items() if s == result][0]
        win_amount = game["bet"] * 2
        new_bal = change_balance(winner_id, win_amount)
        text = (
            f"{x0x_render_board(game['board'])}\n\n"
            f"🏆 {game['names'][winner_id]} kazandı! +{fmt(win_amount)} TL"
        )
        del PENDING_GAMES[active_key]
        await query.edit_message_text(text)
        return

    other_id = [pid for pid in game["players"] if pid != user.id][0]
    game["turn"] = other_id

    text = (
        f"{x0x_render_board(game['board'])}\n\n"
        f"Sıra: {game['names'][other_id]} ({game['players'][other_id]})"
    )
    await query.edit_message_text(text, reply_markup=x0x_keyboard(game["board"], game_key))


# ----------------------------------------------------------------------
# 7) SAYI TAHMIN
# ----------------------------------------------------------------------

async def sayitahmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    if not await check_and_take_bet(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    number = random.randint(1, 100)
    GUESS_NUMBER_STATE[chat_id] = {"number": number, "user_id": user.id, "bet": bet, "tries": 0}

    await reply(
        update,
        "🔢 1 ile 100 arasında bir sayı tuttum!\n"
        "Tahminini mesaj olarak yaz (sadece sayı).\n"
        "En fazla 5 hakkın var. Doğru bilirsen 5 katı kazanırsın!",
    )


async def handle_number_guess_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sade text mesajlarini sayi tahmin oyunu ucun yoxlayir."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = GUESS_NUMBER_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return  # bu oyunla elaqesi yoxdur

    text = update.effective_message.text.strip()
    if not text.lstrip("-").isdigit():
        return

    guess = int(text)
    state["tries"] += 1
    number = state["number"]
    bet = state["bet"]

    if guess == number:
        win = bet * 5
        new_bal = change_balance(user.id, win)
        await reply(
            update,
            f"🎉 Tebrikler! Doğru sayı {number} idi!\n+{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
        )
        del GUESS_NUMBER_STATE[chat_id]
        return

    if state["tries"] >= 5:
        new_bal = get_balance(user.id)
        await reply(
            update,
            f"😢 Hakların bitti! Doğru sayı {number} idi.\n-{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
        )
        del GUESS_NUMBER_STATE[chat_id]
        return

    hint = "🔼 Daha büyük" if guess < number else "🔽 Daha küçük"
    remaining = 5 - state["tries"]
    await reply(update, f"{hint}! Kalan hakkın: {remaining}")


# ----------------------------------------------------------------------
# 8) BOSLUK DOLDURMA
# ----------------------------------------------------------------------

FILL_BLANK_QUESTIONS = [
    ("Türkiye'nin başkenti ____'dır.", "ankara"),
    ("Güneş ____'dan doğar.", "doğu"),
    ("Bir yılda ____ ay vardır.", "12"),
    ("İnsan vücudunun en büyük organı ____'dir.", "deri"),
    ("Su, ____ derecede kaynar (santigrat).", "100"),
    ("Azerbaycan'ın başkenti ____'dır.", "bakü"),
    ("Bir haftada ____ gün vardır.", "7"),
    ("Dünyanın en büyük okyanusu ____ Okyanusu'dur.", "pasifik"),
]


async def boslukdoldurma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    if not await check_and_take_bet(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    question, answer = random.choice(FILL_BLANK_QUESTIONS)
    FILL_BLANK_STATE[chat_id] = {"answer": answer, "user_id": user.id, "bet": bet}

    await reply(update, f"✏️ Boşluğu doldur:\n\n「 {question} 」\n\nCevabını yaz!")


async def handle_fill_blank_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = FILL_BLANK_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return

    guess = update.effective_message.text.strip().lower()
    answer = state["answer"]
    bet = state["bet"]

    if guess == answer:
        win = bet * 3
        new_bal = change_balance(user.id, win)
        await reply(update, f"🎉 Doğru cevap! +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL")
        del FILL_BLANK_STATE[chat_id]
    elif guess in ("pas", "vazgec", "vazgeç"):
        new_bal = get_balance(user.id)
        await reply(update, f"😢 Vazgeçtin. Doğru cevap: {answer}\n💳 Bakiye: {fmt(new_bal)} TL")
        del FILL_BLANK_STATE[chat_id]
    # yanlissa hec ne demirik, davam ede biler (basqa mesaj qarisiqligini azaltmaq ucun)


# ----------------------------------------------------------------------
# 9) KELIME ZINCIRI
# ----------------------------------------------------------------------

WORD_CHAIN_SEED_WORDS = ["kitap", "araba", "deniz", "güneş", "bahçe", "telefon", "orman", "balık"]


async def kelimezincir_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    if not await check_and_take_bet(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    seed = random.choice(WORD_CHAIN_SEED_WORDS)
    WORD_CHAIN_STATE[chat_id] = {
        "last_word": seed,
        "used_words": {seed},
        "user_id": user.id,
        "bet": bet,
        "score": 0,
    }

    await reply(
        update,
        f"🔗 Kelime Zinciri başladı!\nİlk kelime: *{seed}*\n"
        f"Bu kelimenin son harfiyle başlayan bir kelime yaz.\n"
        f"Her doğru kelime için bahisinin yarısı kadar kazanırsın. 'dur' yazarak bitirebilirsin.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_word_chain_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = WORD_CHAIN_STATE.get(chat_id)

    if not state or state["user_id"] != user.id:
        return

    word = update.effective_message.text.strip().lower()

    if word == "dur":
        win = state["score"]
        new_bal = change_balance(user.id, win) if win else get_balance(user.id)
        await reply(update, f"🏁 Oyun bitti! Toplam kazanç: {fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL")
        del WORD_CHAIN_STATE[chat_id]
        return

    if not word.isalpha():
        return

    last_word = state["last_word"]
    last_letter = last_word[-1]

    if word[0] != last_letter:
        await reply(update, f"❌ Kelime '{last_letter}' harfiyle başlamalı! Tekrar dene.")
        return

    if word in state["used_words"]:
        await reply(update, "❌ Bu kelime zaten kullanıldı! Başka bir kelime dene.")
        return

    state["used_words"].add(word)
    state["last_word"] = word
    reward = state["bet"] // 4
    state["score"] += reward

    await reply(
        update,
        f"✅ Doğru! +{fmt(reward)} TL (toplam: {fmt(state['score'])} TL)\n"
        f"Şimdi '{word[-1]}' harfiyle başlayan kelime yaz. Bitirmek için 'dur' yaz.",
    )


# ----------------------------------------------------------------------
# 10) RUS RULETI
# ----------------------------------------------------------------------

async def rusruleti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return

    bullet_chamber = random.randint(1, 6)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"ruleti_{i}_{bullet_chamber}_{bet}") for i in range(1, 4)],
         [InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"ruleti_{i}_{bullet_chamber}_{bet}") for i in range(4, 7)]]
    )
    await reply(update, "🔫 Rus Ruleti! 6 odadan birinde mermi var. Bir oda seç:", reply_markup=keyboard)


async def rusruleti_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, picked_str, bullet_str, bet_str = query.data.split("_")
    picked = int(picked_str)
    bullet = int(bullet_str)
    bet = int(bet_str)

    bal = get_balance(user.id)
    if bal < bet:
        await query.edit_message_text(f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

    if picked == bullet:
        new_bal = get_balance(user.id)
        text = f"🔫 BANG! 💥 Oda {bullet} doluydu!\n😢 Kaybettin! -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        win = int(bet * 1.8)
        new_bal = change_balance(user.id, win)
        text = f"🔫 *click* Şanslısın, mermi Oda {bullet}'daydı!\n🎉 +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await query.edit_message_text(text)


# ----------------------------------------------------------------------
# 11) AT YARISI
# ----------------------------------------------------------------------

HORSES = ["🐎 Yıldırım", "🐎 Kartal", "🐎 Fırtına", "🐎 Şimşek", "🐎 Rüzgar"]


async def atyarisi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = parse_bet(context.args, default=100)

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return

    winner = random.randint(0, len(HORSES) - 1)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(h, callback_data=f"atyar_{i}_{winner}_{bet}")] for i, h in enumerate(HORSES)]
    )
    await reply(update, "🐎 At Yarışı başlıyor! Bahis yapacağın atı seç:", reply_markup=keyboard)


async def atyarisi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, picked_str, winner_str, bet_str = query.data.split("_")
    picked = int(picked_str)
    winner = int(winner_str)
    bet = int(bet_str)

    bal = get_balance(user.id)
    if bal < bet:
        await query.edit_message_text(f"❌ Bakiyen kifayet etmir! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

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

    await query.edit_message_text(text)


# ----------------------------------------------------------------------
# GENEL TEXT MESAJ YONLENDIRICI
# (sayi tahmin, kelime zinciri, bosluk doldurma ucun)
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
    if chat_id in FILL_BLANK_STATE:
        await handle_fill_blank_message(update, context)
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

    # Oyun komandalari
    app.add_handler(CommandHandler("slot", slot_cmd))
    app.add_handler(CommandHandler("bayrak", bayrak_cmd))
    app.add_handler(CommandHandler("tkm", tkm_cmd))
    app.add_handler(CommandHandler("yazitura", yazitura_cmd))
    app.add_handler(CommandHandler("kutu", kutu_cmd))
    app.add_handler(CommandHandler("x0x", x0x_cmd))
    app.add_handler(CommandHandler("sayitahmin", sayitahmin_cmd))
    app.add_handler(CommandHandler("boslukdoldurma", boslukdoldurma_cmd))
    app.add_handler(CommandHandler("kelimezincir", kelimezincir_cmd))
    app.add_handler(CommandHandler("rusruleti", rusruleti_cmd))
    app.add_handler(CommandHandler("atyarisi", atyarisi_cmd))

    # Callback (inline buton) handlerlar
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^info_"))
    app.add_handler(CallbackQueryHandler(bayrak_callback, pattern=r"^bayrak_pick_"))
    app.add_handler(CallbackQueryHandler(tkm_callback, pattern=r"^tkm_"))
    app.add_handler(CallbackQueryHandler(kutu_callback, pattern=r"^kutu_"))
    app.add_handler(CallbackQueryHandler(x0x_join_callback, pattern=r"^x0xjoin_"))
    app.add_handler(CallbackQueryHandler(x0x_move_callback, pattern=r"^x0x_"))
    app.add_handler(CallbackQueryHandler(rusruleti_callback, pattern=r"^ruleti_"))
    app.add_handler(CallbackQueryHandler(atyarisi_callback, pattern=r"^atyar_"))

    # Sade text mesajlari (sayi tahmin / kelime zinciri / bosluq doldurma ucun)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot başladı...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
