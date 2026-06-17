# -*- coding: utf-8 -*-
"""
KERHANE EGLENCE BOT
--------------------
Tam fonksiyonlu Telegram oyun botu.
Kitabxana: python-telegram-bot v20+ (async)
Veritabanı: PostgreSQL (psycopg2)
Token: BOT_TOKEN ortam değişkeninden okunur.
DB bağlantısı: DATABASE_URL ortam değişkeninden okunur.

Qurulus:
    pip install -r requirements.txt
    export BOT_TOKEN="123456:ABC-..."
    export DATABASE_URL="postgresql://user:pass@host:port/dbname"
    python bot.py
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

START_BALANCE = 5999
HOURLY_REWARD = 5000
HOURLY_COOLDOWN_SECONDS = 60 * 60  # 1 saat

LEADERBOARD_SIZE = 15

ADMIN_CONTACT = "@korunan"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# YADDASDA SAXLANAN AKTIV OYUN VEZIYYETLERI
# ----------------------------------------------------------------------

# Her bir deyer bir oyunun "state" obyektidir. Acari oyun tipine gore ferqlenir.
PENDING_GAMES = {}        # umumi: bayraq, tkm duello, xox, vs -> key bele qurulur (asagida izah olunur)
TEXT_GAME_STATE = {}      # chat_id -> {"type": "...", ...}  (bayraq, sayitahmin, kelime, bulmaca ucun)

# ----------------------------------------------------------------------
# SOZ / BAYRAQ / BULMACA BANKLARI
# ----------------------------------------------------------------------

# (bayraq_emoji, dogru_cavablar_listesi)  -- ilk cavab gosterilen, qalanlari alternativ qebul olunur
FLAGS_BANK = [
    ("tr", ["türkiye", "turkiye"]),
    ("az", ["azerbaycan", "azerbaycan"]),
    ("de", ["almanya"]),
    ("fr", ["fransa"]),
    ("br", ["brezilya"]),
    ("jp", ["japonya"]),
    ("it", ["italya"]),
    ("es", ["ispanya"]),
    ("gb", ["ingiltere", "birlesik krallik"]),
    ("us", ["amerika", "abd"]),
    ("ru", ["rusya"]),
    ("cn", ["cin"]),
    ("kr", ["güney kore", "guney kore"]),
    ("in", ["hindistan"]),
    ("ca", ["kanada"]),
    ("mx", ["meksika"]),
    ("ar", ["arjantin"]),
    ("nl", ["hollanda"]),
    ("pt", ["portekiz"]),
    ("gr", ["yunanistan"]),
    ("ch", ["isvicre"]),
    ("se", ["isvec"]),
    ("no", ["norvec"]),
    ("pl", ["polonya"]),
    ("eg", ["misir"]),
    ("sa", ["suudi arabistan"]),
    ("ir", ["iran"]),
    ("iq", ["irak"]),
    ("ua", ["ukrayna"]),
    ("qa", ["katar"]),
    # bazı daha zor bayraklar
    ("kz", ["kazakistan"]),
    ("ma", ["fas"]),
    ("lb", ["lübnan", "lubnan"]),
    ("vn", ["vietnam"]),
    ("cl", ["sili"]),
    ("co", ["kolombiya"]),
    ("fi", ["finlandiya"]),
    ("ie", ["irlanda"]),
    ("id", ["endonezya"]),
    ("ng", ["nijerya"]),
]

# Kelime zinciri ve bulmaca ucun ortaq turkce soz banki (100+ soz)
WORD_BANK = [
    "masa", "araba", "kapı", "kitap", "bardak", "kalem", "telefon", "bilgisayar",
    "pencere", "duvar", "yatak", "yastık", "halı", "perde", "lamba", "saat",
    "ayna", "dolap", "sandalye", "mutfak", "buzdolabı", "fırın", "tencere",
    "tabak", "çatal", "kaşık", "bıçak", "peçete", "ekmek", "peynir", "zeytin",
    "domates", "salatalık", "biber", "patates", "soğan", "sarımsak", "limon",
    "elma", "armut", "muz", "çilek", "karpuz", "kavun", "üzüm", "şeftali",
    "kiraz", "vişne", "ayva", "nar", "incir", "ceviz", "fındık", "badem",
    "fıstık", "deniz", "dağ", "orman", "nehir", "göl", "çöl", "ada", "vadi",
    "tepe", "ova", "bulut", "yağmur", "kar", "rüzgar", "fırtına", "gökyüzü",
    "güneş", "yıldız", "gezegen", "uzay", "dünya", "ülke", "şehir", "köy",
    "sokak", "cadde", "meydan", "park", "bahçe", "okul", "üniversite",
    "hastane", "market", "restoran", "otel", "havalimanı", "istasyon",
    "köprü", "tünel", "fabrika", "ofis", "kütüphane", "müze", "sinema",
    "tiyatro", "stadyum", "kale", "saray", "cami", "kilise", "tapınak",
    "ağaç", "çiçek", "yaprak", "kök", "dal", "tohum", "meyve", "sebze",
    "köpek", "kedi", "kuş", "balık", "aslan", "kaplan", "fil", "zürafa",
    "maymun", "ayı", "kurt", "tilki", "tavşan", "sincap", "kartal", "baykuş",
    "yılan", "kaplumbağa", "timsah", "yunus", "balina", "köpekbalığı",
    "arı", "kelebek", "karınca", "örümcek", "böcek", "sinek", "uçurtma",
    "balon", "top", "oyuncak", "bisiklet", "motosiklet", "tren", "gemi",
    "uçak", "helikopter", "roket", "anahtar", "kilit", "çanta", "valiz",
    "şemsiye", "gözlük", "saat", "yüzük", "kolye", "bilezik", "ayakkabı",
    "çorap", "gömlek", "pantolon", "ceket", "şapka", "eldiven", "kemer",
]


def normalize(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir, karşılaştırma için."""
    text = text.strip().lower()
    repl = {
        "ı": "i", "İ": "i", "ş": "s", "ğ": "g",
        "ü": "u", "ö": "o", "ç": "c", "â": "a",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


def pick_word(min_len=3, max_len=99):
    candidates = [w for w in WORD_BANK if min_len <= len(w) <= max_len]
    return random.choice(candidates) if candidates else random.choice(WORD_BANK)


def find_word_for_letter(letter: str, used: set):
    letter_n = normalize(letter)
    candidates = [
        w for w in WORD_BANK
        if normalize(w[0]) == letter_n and w not in used
    ]
    return random.choice(candidates) if candidates else None


HANGMAN_DIFFICULTY = {
    "kolay": (3, 4),
    "orta": (5, 6),
    "zor": (7, 12),
}


# ----------------------------------------------------------------------
# VERILENLER BAZASI (PostgreSQL)
# ----------------------------------------------------------------------

_connection_pool = None


def get_pool():
    global _connection_pool
    if _connection_pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "❌ DATABASE_URL bulunamadı! Railway'de PostgreSQL ekle, "
                "ya da export DATABASE_URL='postgresql://...' ile bağlantıyı ver."
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


def get_top_users(limit: int = LEADERBOARD_SIZE):
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT first_name, balance FROM users ORDER BY balance DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()
    finally:
        db_release(conn)


def find_user_by_username(username: str):
    """@ işareti olmadan, sade username ile arar."""
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, first_name, balance FROM users WHERE LOWER(username)=LOWER(%s)",
            (username,),
        )
        return cur.fetchone()
    finally:
        db_release(conn)


def get_user_by_id(user_id: int):
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, first_name, balance FROM users WHERE user_id=%s", (user_id,))
        return cur.fetchone()
    finally:
        db_release(conn)


# ----------------------------------------------------------------------
# KOMEKCI FUNKSIYALAR
# ----------------------------------------------------------------------

def fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


async def reply(update: Update, text: str, **kwargs):
    return await update.effective_message.reply_text(text, **kwargs)


def parse_amount_arg(args):
    """/komut [miktar] formatından bahis miktarını okur. Yoksa None döndürür."""
    if not args:
        return None
    try:
        v = int(args[0])
        if v > 0:
            return v
    except (ValueError, IndexError):
        pass
    return None


async def require_amount(update: Update, args, usage_text: str):
    """Miktar yok/yanlışsa kullanım mesajı gösterir, None döndürür."""
    amount = parse_amount_arg(args)
    if amount is None:
        await reply(update, usage_text, parse_mode=ParseMode.MARKDOWN)
        return None
    return amount


async def take_bet_or_warn(update: Update, user_id: int, bet: int) -> bool:
    bal = get_balance(user_id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz!\n💳 Bakiyen: {fmt(bal)} TL, bahis: {fmt(bet)} TL.")
        return False
    change_balance(user_id, -bet)
    return True


async def safe_edit(query, text, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"edit_message_text failed: {e}")


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
        [
            InlineKeyboardButton("🎰 Slot Makinesi", callback_data="info_slot"),
            InlineKeyboardButton("🚩 Bayrak Yarışı", callback_data="info_bayrak"),
        ],
        [
            InlineKeyboardButton("✂️ Taş Kağıt Makas", callback_data="info_tkm"),
            InlineKeyboardButton("🪙 Yazı Tura", callback_data="info_yazitura"),
        ],
        [
            InlineKeyboardButton("🔍 Bul Beni (Kutu)", callback_data="info_kutu"),
            InlineKeyboardButton("❌ X0X Duello", callback_data="info_xox"),
        ],
        [
            InlineKeyboardButton("🔢 Sayı Tahmin", callback_data="info_sayitahmin"),
            InlineKeyboardButton("🧩 Boşluk Doldurma", callback_data="info_bulmaca"),
        ],
        [
            InlineKeyboardButton("📝 Kelime Zincir", callback_data="info_kelime"),
            InlineKeyboardButton("🔫 Rus Ruleti", callback_data="info_rusruleti"),
        ],
        [
            InlineKeyboardButton("🐎 At Yarışı", callback_data="info_atyarisi"),
            InlineKeyboardButton("👤 Profilim", callback_data="info_profil"),
        ],
        [
            InlineKeyboardButton("🏆 En Zenginler", callback_data="info_zenginler"),
            InlineKeyboardButton("💸 Para Transferi", callback_data="info_transfer"),
        ],
        [
            InlineKeyboardButton("🎁 Saatlik Ödül", callback_data="profil_odul"),
        ],
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
    "info_slot": "🎰 Slot oynamak için lütfen şu formatta yazın:\n`/slot [miktar]`\nÖrnek: `/slot 100`",
    "info_bayrak": "🚩 Bayrak yarışı oynamak için lütfen şu formatta yazın:\n`/bayrak [miktar]`\nÖrnek: `/bayrak 150`",
    "info_tkm": "✂️ Taş Kağıt Makas oynamak için lütfen şu formatta yazın:\n`/tkm [miktar]`\nÖrnek: `/tkm 200`",
    "info_yazitura": "🪙 Yazı-Tura oynamak için lütfen şu formatta yazın:\n`/ytsans [miktar]`\nÖrnek: `/ytsans 100`",
    "info_kutu": "🔍 Bul beni kutu oyunu için lütfen şu formatta yazın:\n`/bulbeni [miktar]`\nÖrnek: `/bulbeni 300`",
    "info_xox": "❌ XOX düellosu başlatmak için lütfen şu formatta yazın:\n`/xox [miktar]`\nÖrnek: `/xox 500`",
    "info_sayitahmin": "🔢 Sayı tahmin oyunu için lütfen şu formatta yazın:\n`/tahminet [miktar]`\nÖrnek: `/tahminet 50`",
    "info_bulmaca": "🧩 Adam asmaca oyunu için lütfen şu formatta yazın:\n`/bulmaca [miktar]`\nÖrnek: `/bulmaca 100`",
    "info_kelime": "📝 Kelime zinciri oyunu için lütfen şu formatta yazın:\n`/kelime [miktar]`\nÖrnek: `/kelime 100`",
    "info_rusruleti": "🔫 Rus ruleti oynamak için lütfen şu formatta yazın:\n`/rusruleti [miktar]`\nÖrnek: `/rusruleti 100`",
    "info_atyarisi": "🐎 At yarışı oynamak için lütfen şu formatta yazın:\n`/atyarisi [miktar]`\nÖrnek: `/atyarisi 100`",
}


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "info_profil":
        await send_profile_panel(update, context, via_callback=True)
        return
    if data == "info_zenginler":
        await send_leaderboard(update, context, via_callback=True)
        return
    if data == "info_transfer":
        await send_transfer_info(update, context, via_callback=True)
        return

    text = GAME_INFO_TEXT.get(data)
    if text:
        await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------------
# PROFIL / SAATLIK ODUL / ZENGINLER / TRANSFER
# ----------------------------------------------------------------------

def build_profile_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎁 Saatlik Ödül", callback_data="profil_odul")]]
    )


async def send_profile_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, via_callback=False):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bal = get_balance(user.id)
    text = f"👤 PROFİL\n📛 İsim: {user.first_name}\n💳 Bakiye: {fmt(bal)} TL"
    kb = build_profile_keyboard()
    if via_callback:
        await update.callback_query.message.edit_text(text, reply_markup=kb)
    else:
        await reply(update, text, reply_markup=kb)


async def ben_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_profile_panel(update, context)


async def profil_odul_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🎁 Saatlik Ödül Paneli\n"
        "Ödülünüzü buradan butonla alamazsınız.\n"
        "💰 Saatlik bedava 5000 TL bakiyenizi talep etmek için sohbete lütfen şu komutu yazın:\n\n"
        "`/odulum`"
    )
    await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)


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


async def build_leaderboard_text() -> str:
    rows = get_top_users(LEADERBOARD_SIZE)
    lines = [f"🏆 EN ZENGİN {LEADERBOARD_SIZE}", ""]
    for i, row in enumerate(rows, start=1):
        name, balance = row[0], row[1]
        lines.append(f"{i}. {name} -\n```\n{fmt(balance)} TL\n```")
    return "\n".join(lines)


async def send_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, via_callback=False):
    text = await build_leaderboard_text()
    if via_callback:
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await reply(update, text, parse_mode=ParseMode.MARKDOWN)


async def zenginler_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_leaderboard(update, context)


TRANSFER_INFO_TEXT = (
    "💸 Para Transferi Nasıl Yapılır?\n"
    "Başka bir kullanıcıya bakiye göndermek için şu adımları izleyin:\n\n"
    "1️⃣ Para göndermek istediğiniz kişinin bir mesajını yanıtlayın (Reply).\n"
    "2️⃣ Yanıt olarak şu komutu yazıp gönderin:\n\n"
    "`/yolla [miktar]`\n\n"
    "💡 Örnek: Birinin mesajını yanıtlayarak `/yolla 500` yazarsanız, o kişiye 500 TL gönderilir."
)


async def send_transfer_info(update: Update, context: ContextTypes.DEFAULT_TYPE, via_callback=False):
    if via_callback:
        await update.callback_query.message.edit_text(TRANSFER_INFO_TEXT, parse_mode=ParseMode.MARKDOWN)
    else:
        await reply(update, TRANSFER_INFO_TEXT, parse_mode=ParseMode.MARKDOWN)


async def yolla_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    replied = update.effective_message.reply_to_message
    if not replied:
        await reply(update, "❌ Yanıtlayarak yaz!")
        return

    amount = parse_amount_arg(context.args)
    if amount is None:
        await send_transfer_info(update, context)
        return

    target_user = replied.from_user
    ensure_user(target_user.id, target_user.first_name or "Oyuncu", target_user.username or "")

    bal = get_balance(user.id)
    if bal < amount:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    change_balance(user.id, -amount)
    change_balance(target_user.id, amount)

    await reply(update, f"💸 {user.first_name} -> {target_user.first_name}: {fmt(amount)} TL gönderildi!")


# ----------------------------------------------------------------------
# 1) SLOT MAKINESI
# ----------------------------------------------------------------------

SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "💎", "🍎"]
SLOT_MULTIPLIERS = {
    "💎💎💎": 10,
    "⭐⭐⭐": 7,
    "🔔🔔🔔": 5,
    "🍇🍇🍇": 4,
    "🍋🍋🍋": 3,
    "🍒🍒🍒": 2,
    "🍎🍎🍎": 2,
}

SLOT_USAGE = "🎰 Slot oynamak için lütfen şu formatta yazın:\n`/slot [miktar]`\nÖrnek: `/slot 100`"


async def slot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, SLOT_USAGE)
    if bet is None:
        return
    if not await take_bet_or_warn(update, user.id, bet):
        return

    spinning = ["⏳", "🔄", "🎲"]
    msg = await reply(update, f"🎰 SLOT DÖNÜYOR...\n[ {spinning[0]} | {spinning[1]} | {spinning[2]} ]")

    final_reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]

    # animasiya: hər addımda biraz daha az dəyişən simvol göstər
    frames = [
        [random.choice(SLOT_SYMBOLS), random.choice(SLOT_SYMBOLS), random.choice(SLOT_SYMBOLS)],
        [final_reels[0], random.choice(SLOT_SYMBOLS), random.choice(SLOT_SYMBOLS)],
        [final_reels[0], final_reels[1], random.choice(SLOT_SYMBOLS)],
    ]
    for frame in frames:
        await asyncio.sleep(0.5)
        try:
            await msg.edit_text(f"🎰 SLOT DÖNÜYOR...\n[ {frame[0]} | {frame[1]} | {frame[2]} ]")
        except BadRequest:
            pass

    await asyncio.sleep(0.5)
    combo = "".join(final_reels)
    display = " | ".join(final_reels)

    multiplier = SLOT_MULTIPLIERS.get(combo, 0)
    if multiplier == 0 and len(set(final_reels)) == 2:
        multiplier = 0.5

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
        await reply(update, text)


# ----------------------------------------------------------------------
# 2) BAYRAK YARISI (mesaj-bazli, 10 tur, 15 saniye sure, 3 can)
# ----------------------------------------------------------------------

BAYRAK_USAGE = "🚩 Bayrak yarışı oynamak için lütfen şu formatta yazın:\n`/bayrak [miktar]`\nÖrnek: `/bayrak 150`"
BAYRAK_ROUNDS = 10
BAYRAK_LIVES = 3
BAYRAK_TIMEOUT = 15


async def bayrak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, BAYRAK_USAGE)
    if bet is None:
        return
    if not await take_bet_or_warn(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    flag, answers = random.choice(FLAGS_BANK)

    state = {
        "type": "bayrak",
        "user_id": user.id,
        "bet": bet,
        "round": 1,
        "lives": BAYRAK_LIVES,
        "flag": flag,
        "answers": answers,
        "task": None,
    }
    TEXT_GAME_STATE[chat_id] = state

    await send_bayrak_round(update.effective_chat.id, context, first=True)


async def send_bayrak_round(chat_id, context: ContextTypes.DEFAULT_TYPE, first=False):
    state = TEXT_GAME_STATE.get(chat_id)
    if not state or state["type"] != "bayrak":
        return

    flag_code = state["flag"]
    flag_url = f"https://flagcdn.com/w320/{flag_code}.png"
    caption = (
        f"🚩 TUR: {state['round']}/{BAYRAK_ROUNDS} | ❤️: {state['lives']}\n"
        f"⏱️ Süre: {BAYRAK_TIMEOUT} Saniye"
    )
    try:
        await context.bot.send_photo(chat_id, photo=flag_url, caption=caption)
    except BadRequest:
        # foto basarisiz olarsa metin olarak gonder
        await context.bot.send_message(chat_id, caption)

    if state.get("task"):
        state["task"].cancel()
    state["task"] = asyncio.create_task(bayrak_timeout_watcher(chat_id, context, state["round"]))


async def bayrak_timeout_watcher(chat_id, context: ContextTypes.DEFAULT_TYPE, round_no):
    await asyncio.sleep(BAYRAK_TIMEOUT)
    state = TEXT_GAME_STATE.get(chat_id)
    if not state or state["type"] != "bayrak" or state["round"] != round_no:
        return  # oyun artiq deyisib / bitib

    user_id = state["user_id"]
    bet = state["bet"]
    change_balance(user_id, bet)  # bahisi geri qaytar
    del TEXT_GAME_STATE[chat_id]

    await context.bot.send_message(
        chat_id,
        "⏱️ Süre Doldu! 15 saniye içinde cevap verilmediği için oyun iptal edildi ve bakiye iade edildi.",
    )


async def handle_bayrak_message(update: Update, context: ContextTypes.DEFAULT_TYPE, state):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.id != state["user_id"]:
        return

    guess = normalize(update.effective_message.text)
    correct_answers = [normalize(a) for a in state["answers"]]

    if state.get("task"):
        state["task"].cancel()

    if guess in correct_answers:
        await reply(update, "✅ Doğru Cevap!")
        state["round"] += 1
        if state["round"] > BAYRAK_ROUNDS:
            win = state["bet"] * 3
            new_bal = change_balance(user.id, win)
            del TEXT_GAME_STATE[chat_id]
            await reply(
                update,
                f"🏆 TEBRİKLER! {BAYRAK_ROUNDS} turu tamamladın!\n"
                f"✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
            )
            return
        flag, answers = random.choice(FLAGS_BANK)
        state["flag"] = flag
        state["answers"] = answers
        await send_bayrak_round(chat_id, context)
    else:
        state["lives"] -= 1
        correct_name = state["answers"][0].title()
        if state["lives"] <= 0:
            bet = state["bet"]
            del TEXT_GAME_STATE[chat_id]
            await reply(
                update,
                f"❌ Yanlış! Doğru Cevap: {correct_name}\n\n"
                f"💀 BİTTİ! ❌: {BAYRAK_LIVES} 📉: -{fmt(bet)} TL",
            )
            return
        await reply(update, f"❌ Yanlış! Doğru Cevap: {correct_name}")
        state["round"] += 1
        if state["round"] > BAYRAK_ROUNDS:
            win = state["bet"] * 3
            new_bal = change_balance(user.id, win)
            del TEXT_GAME_STATE[chat_id]
            await reply(
                update,
                f"🏆 TEBRİKLER! {BAYRAK_ROUNDS} turu tamamladın!\n"
                f"✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL",
            )
            return
        flag, answers = random.choice(FLAGS_BANK)
        state["flag"] = flag
        state["answers"] = answers
        await send_bayrak_round(chat_id, context)


# ----------------------------------------------------------------------
# 3) TAS KAGIT MAKAS (mod secimi -> bot ile / duello PvP)
# ----------------------------------------------------------------------

TKM_USAGE = "✂️ Taş Kağıt Makas oynamak için lütfen şu formatta yazın:\n`/tkm [miktar]`\nÖrnek: `/tkm 200`"
TKM_EMOJI = {"tas": "✊", "kagit": "📄", "makas": "✂️"}
TKM_NAME = {"tas": "Taş", "kagit": "Kağıt", "makas": "Makas"}
TKM_BEATS = {"tas": "makas", "kagit": "tas", "makas": "kagit"}


async def tkm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, TKM_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"tkm_setup_{game_key}"] = {"bet": bet, "creator_id": user.id, "creator_name": user.first_name}

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("1️⃣ 1 Tur", callback_data=f"tkmrounds_{game_key}_1"),
        InlineKeyboardButton("3️⃣ 3 Tur", callback_data=f"tkmrounds_{game_key}_3"),
        InlineKeyboardButton("5️⃣ 5 Tur", callback_data=f"tkmrounds_{game_key}_5"),
    ]])
    await reply(update, f"✂️ Taş Kağıt Makas\n💰 Bahis: {fmt(bet)} TL\nKaç tur oynanacak?", reply_markup=keyboard)


async def tkm_rounds_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    body = query.data[len("tkmrounds_"):]
    game_key, rounds_str = body.rsplit("_", 1)
    rounds = int(rounds_str)
    setup_key = f"tkm_setup_{game_key}"
    setup = PENDING_GAMES.get(setup_key)
    if not setup or setup["creator_id"] != user.id:
        await query.answer("⚠️ Bu oyun sana ait değil veya süresi geçti.", show_alert=True)
        return

    setup["rounds"] = rounds

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Bot İle Oyna", callback_data=f"tkmmode_{game_key}_bot")],
        [InlineKeyboardButton("⚔️ Düello (PvP)", callback_data=f"tkmmode_{game_key}_pvp")],
    ])
    await safe_edit(
        query,
        f"🎮 Oyun Modu Seçin\n💰 Bahis: {fmt(setup['bet'])} TL\n🏆 Hedeflenen Tur: {rounds}",
        reply_markup=keyboard,
    )


def tkm_choice_keyboard(game_key):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✊ Taş", callback_data=f"tkmpick_{game_key}_tas"),
        InlineKeyboardButton("📄 Kağıt", callback_data=f"tkmpick_{game_key}_kagit"),
        InlineKeyboardButton("✂️ Makas", callback_data=f"tkmmpick_{game_key}_makas"),
    ]])


def tkm_render_status(game):
    p1_name = game["names"][game["p1"]]
    p2_label = "🤖 Bot" if game["p2"] == "bot" else game["names"][game["p2"]]
    return (
        f"🎮 TAŞ - KAĞIT - MAKAS\n"
        f"🔴 {p1_name}: {game['score'][game['p1']]}   🔵 {p2_label}: {game['score'].get(game['p2'], 0)}\n\n"
        f"🚩 Tur: {game['current_round']}/{game['rounds']}\n"
        f"💰 Bahis: {fmt(game['bet'])} TL\n\n"
        f"👇 Seçimini yap!"
    )


async def tkm_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    body = query.data[len("tkmmode_"):]
    game_key, mode = body.rsplit("_", 1)
    setup_key = f"tkm_setup_{game_key}"
    setup = PENDING_GAMES.get(setup_key)
    if not setup or setup["creator_id"] != user.id:
        await query.answer("⚠️ Bu oyun sana ait değil veya süresi geçti.", show_alert=True)
        return

    bal = get_balance(user.id)
    if bal < setup["bet"]:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        del PENDING_GAMES[setup_key]
        return

    if mode == "bot":
        change_balance(user.id, -setup["bet"])
        del PENDING_GAMES[setup_key]

        active_key = f"tkm_active_{game_key}"
        PENDING_GAMES[active_key] = {
            "bet": setup["bet"],
            "rounds": setup["rounds"],
            "current_round": 1,
            "p1": user.id,
            "p2": "bot",
            "names": {user.id: user.first_name},
            "score": {user.id: 0, "bot": 0},
            "pending_pick": {},
        }
        game = PENDING_GAMES[active_key]
        await safe_edit(query, tkm_render_status(game), reply_markup=tkm_choice_keyboard(game_key))
    else:
        active_key = f"tkm_pvpwait_{game_key}"
        PENDING_GAMES[active_key] = {
            "bet": setup["bet"],
            "rounds": setup["rounds"],
            "creator_id": user.id,
            "creator_name": user.first_name,
        }
        del PENDING_GAMES[setup_key]
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤝 Katıl", callback_data=f"tkmjoin_{game_key}")]])
        await safe_edit(
            query,
            f"🤝 TKM DÜELLO ÇAĞRISI\n👤 Kurucu: {user.first_name}\n💰 Bahis: {fmt(setup['bet'])} TL\n"
            f"🏆 Tur: {setup['rounds']}\n\nRakip bekleniyor...",
            reply_markup=keyboard,
        )


async def tkm_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game_key = query.data.replace("tkmjoin_", "")
    wait_key = f"tkm_pvpwait_{game_key}"
    wait = PENDING_GAMES.get(wait_key)

    if not wait:
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return

    if user.id == wait["creator_id"]:
        await query.answer("Kendi Oyunun!", show_alert=True)
        return

    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bal = get_balance(user.id)
    if bal < wait["bet"]:
        await query.answer("Bakiyen yetersiz!", show_alert=True)
        return

    change_balance(wait["creator_id"], -wait["bet"])
    change_balance(user.id, -wait["bet"])
    del PENDING_GAMES[wait_key]

    active_key = f"tkm_active_{game_key}"
    PENDING_GAMES[active_key] = {
        "bet": wait["bet"],
        "rounds": wait["rounds"],
        "current_round": 1,
        "p1": wait["creator_id"],
        "p2": user.id,
        "names": {wait["creator_id"]: wait["creator_name"], user.id: user.first_name},
        "score": {wait["creator_id"]: 0, user.id: 0},
        "pending_pick": {},
    }
    game = PENDING_GAMES[active_key]
    await safe_edit(query, tkm_render_status(game), reply_markup=tkm_choice_keyboard(game_key))


async def tkm_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    raw = query.data
    raw = raw.replace("tkmmpick_", "tkmpick_")
    # raw format: tkmpick_{game_key}_{choice}  -- game_key kendisi alt cizgi icerebilir
    body = raw[len("tkmpick_"):]
    game_key, choice = body.rsplit("_", 1)

    active_key = f"tkm_active_{game_key}"
    game = PENDING_GAMES.get(active_key)
    if not game:
        await query.answer("⚠️ Oyun bulunamadı veya bitti.", show_alert=True)
        return

    if user.id not in (game["p1"], game["p2"]):
        await query.answer("Bu oyunda değilsin!", show_alert=True)
        return

    if user.id in game["pending_pick"]:
        await query.answer("Zaten seçim yaptın, rakip bekleniyor.", show_alert=True)
        return

    game["pending_pick"][user.id] = choice

    if game["p2"] == "bot":
        bot_choice = random.choice(list(TKM_EMOJI.keys()))
        game["pending_pick"]["bot"] = bot_choice
    else:
        if len(game["pending_pick"]) < 2:
            await query.answer("Seçimin alındı, rakip bekleniyor...", show_alert=True)
            return

    p1, p2 = game["p1"], game["p2"]
    c1, c2 = game["pending_pick"][p1], game["pending_pick"][p2]
    p1_name = game["names"][p1]
    p2_name = "🤖 Bot" if p2 == "bot" else game["names"][p2]

    round_no = game["current_round"]

    if c1 == c2:
        result_line = f"🤝 Berabere! Her ikisi de {TKM_EMOJI[c1]} yaptı."
        winner = None
    elif TKM_BEATS[c1] == c2:
        winner = p1
        result_line = f"✅ {p1_name} kazandı! {TKM_EMOJI[c1]} vs {TKM_EMOJI[c2]}"
        game["score"][p1] += 1
    else:
        winner = p2
        result_line = f"❌ {p2_name} kazandı! {TKM_EMOJI[c1]} vs {TKM_EMOJI[c2]}"
        game["score"][p2] += 1

    text = f"🔄 TUR {round_no} SONUCU\n{result_line}"

    game["current_round"] += 1
    game["pending_pick"] = {}

    bet = game["bet"]

    if game["current_round"] > game["rounds"]:
        s1, s2 = game["score"][p1], game["score"].get(p2, 0)
        del PENDING_GAMES[active_key]

        if s1 == s2:
            if p2 != "bot":
                change_balance(p1, bet)
                change_balance(p2, bet)
            else:
                change_balance(p1, bet)
            text += "\n\n🤝 OYUN BİTTİ!\nSkorlar eşit, bakiye iade edildi!"
        elif s1 > s2:
            win = bet * 2
            change_balance(p1, win)
            text += f"\n\n🏆 OYUN BİTTİ!\nKazanan: {p1_name}\n💰 Kazanç: +{fmt(win)} TL"
        else:
            if p2 == "bot":
                text += f"\n\n💀 OYUN BİTTİ!\nKazanan: Bot\n📉 Kayıp: -{fmt(bet)} TL"
            else:
                win = bet * 2
                change_balance(p2, win)
                text += f"\n\n💀 OYUN BİTTİ!\nKazanan: {p2_name}\n📉 Kayıp: -{fmt(bet)} TL"

        await safe_edit(query, text)
        return

    await safe_edit(query, text)
    await asyncio.sleep(1.2)
    try:
        await query.message.reply_text(tkm_render_status(game), reply_markup=tkm_choice_keyboard(game_key))
    except BadRequest:
        pass


# ----------------------------------------------------------------------
# 4) YAZI TURA
# ----------------------------------------------------------------------

YT_USAGE = "🪙 Yazı-Tura oynamak için lütfen şu formatta yazın:\n`/ytsans [miktar]`\nÖrnek: `/ytsans 100`"


async def ytsans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, YT_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✨ YAZI", callback_data=f"yt_{user.id}_{bet}_yazi"),
        InlineKeyboardButton("🌙 TURA", callback_data=f"yt_{user.id}_{bet}_tura"),
    ]])
    await reply(update, f"🪙 {fmt(bet)} TL bahis. Seç:", reply_markup=keyboard)


async def yt_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    _, owner_id_str, bet_str, choice = query.data.split("_")
    owner_id = int(owner_id_str)
    bet = int(bet_str)

    if user.id != owner_id:
        await query.answer("Bu senin oyunun değil!", show_alert=True)
        return

    bal = get_balance(user.id)
    if bal < bet:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

    await safe_edit(query, "🪙 Para havada dönüyor...")
    await asyncio.sleep(2)

    result = random.choice(["yazi", "tura"])
    result_label = "✨ YAZI" if result == "yazi" else "🌙 TURA"

    if result == choice:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        text = f"{result_label} geldi! ✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
    else:
        new_bal = get_balance(user.id)
        text = f"{result_label} geldi! ❌ -{fmt(bet)} TL\n💳 Bakiye: {fmt(new_bal)} TL"

    await safe_edit(query, text)


# ----------------------------------------------------------------------
# 5) BUL BENI (KUTU) - 3x3, 1 dolu 8 bos, 3 hak
# ----------------------------------------------------------------------

KUTU_USAGE = "🔍 Bul beni kutu oyunu için lütfen şu formatta yazın:\n`/bulbeni [miktar]`\nÖrnek: `/bulbeni 300`"
KUTU_LIVES = 3


async def bulbeni_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, KUTU_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return
    change_balance(user.id, -bet)

    winner_box = random.randint(0, 8)
    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"kutu_{game_key}"] = {
        "user_id": user.id,
        "bet": bet,
        "winner": winner_box,
        "lives": KUTU_LIVES,
        "opened": set(),
    }

    text = f"🔍 {fmt(bet)} TL bahis!\n❤️ {KUTU_LIVES} Hak\nÖdül hangi kutuda?"
    await reply(update, text, reply_markup=kutu_keyboard(game_key, set()))


def kutu_keyboard(game_key, opened):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            label = "❌" if i in opened else "📦"
            row.append(InlineKeyboardButton(label, callback_data=f"kutu_{game_key}_{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def kutu_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    parts = query.data.split("_")
    idx = int(parts[-1])
    game_key = "_".join(parts[1:-1])
    state_key = f"kutu_{game_key}"
    game = PENDING_GAMES.get(state_key)

    if not game:
        await query.answer("⚠️ Oyun bulunamadı veya bitti.", show_alert=True)
        return
    if user.id != game["user_id"]:
        await query.answer("Bu senin oyunun değil!", show_alert=True)
        return
    if idx in game["opened"]:
        await query.answer("Bu kutu zaten açıldı!", show_alert=True)
        return

    bet = game["bet"]

    if idx == game["winner"]:
        win = bet * 2
        new_bal = change_balance(user.id, win)
        del PENDING_GAMES[state_key]
        game["opened"].add(idx)
        text = f"💎 BULDUN! ✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL"
        await safe_edit(query, text, reply_markup=kutu_keyboard(game_key, game["opened"]))
        return

    game["opened"].add(idx)
    game["lives"] -= 1

    if game["lives"] <= 0:
        del PENDING_GAMES[state_key]
        text = f"💀 KAYIP! Ödül {game['winner']+1}. kutudaydı."
        await safe_edit(query, text, reply_markup=kutu_keyboard(game_key, game["opened"]))
        return

    text = f"❌ Boş! ❤️: {game['lives']}"
    await safe_edit(query, text, reply_markup=kutu_keyboard(game_key, game["opened"]))


# ----------------------------------------------------------------------
# 6) X0X DUELLO (2 oyuncu)
# ----------------------------------------------------------------------

XOX_USAGE = "❌ XOX düellosu başlatmak için lütfen şu formatta yazın:\n`/xox [miktar]`\nÖrnek: `/xox 500`"


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
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            v = board[i]
            label = v if v != " " else "·"
            row.append(InlineKeyboardButton(label, callback_data=f"xoxmv_{game_key}_{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


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

    bet = await require_amount(update, context.args, XOX_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"xoxwait_{game_key}"] = {
        "creator_id": user.id,
        "creator_name": user.first_name,
        "bet": bet,
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Düelloya Katıl", callback_data=f"xoxjoin_{game_key}")],
        [InlineKeyboardButton("🚫 Oyunu Kapat", callback_data=f"xoxcancel_{game_key}")],
    ])
    await reply(
        update,
        f"⚔️ XOX DÜELLO!\n💰: {fmt(bet)} TL\n👤 {user.first_name} rakip bekliyor...",
        reply_markup=keyboard,
    )


async def xox_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game_key = query.data.replace("xoxcancel_", "")
    wait_key = f"xoxwait_{game_key}"
    wait = PENDING_GAMES.get(wait_key)

    if not wait:
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return
    if user.id != wait["creator_id"]:
        await query.answer("Sadece kurucu kapatabilir!", show_alert=True)
        return

    change_balance(user.id, wait["bet"])
    del PENDING_GAMES[wait_key]
    await safe_edit(query, "❌ Oyun kurucu tarafından iptal edildi.")


async def xox_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game_key = query.data.replace("xoxjoin_", "")
    wait_key = f"xoxwait_{game_key}"
    wait = PENDING_GAMES.get(wait_key)

    if not wait:
        await query.answer("⚠️ Bu düello artık geçerli değil.", show_alert=True)
        return
    if user.id == wait["creator_id"]:
        await query.answer("Kendi Oyunun!", show_alert=True)
        return

    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")
    bet = wait["bet"]
    bal = get_balance(user.id)
    if bal < bet:
        await query.answer("Bakiyen yetersiz!", show_alert=True)
        return

    change_balance(wait["creator_id"], -bet)
    change_balance(user.id, -bet)
    del PENDING_GAMES[wait_key]

    active_key = f"xoxactive_{game_key}"
    PENDING_GAMES[active_key] = {
        "board": [" "] * 9,
        "players": {wait["creator_id"]: "❌", user.id: "⭕"},
        "names": {wait["creator_id"]: wait["creator_name"], user.id: user.first_name},
        "turn": wait["creator_id"],
        "bet": bet,
    }

    game = PENDING_GAMES[active_key]
    text = (
        f"❌ {game['names'][wait['creator_id']]}  VS  ⭕ {user.first_name}\n"
        f"💰 Bahis: {fmt(bet)} TL (her oyuncudan alındı)\n\n"
        f"Sıra: {game['names'][game['turn']]} (❌)"
    )
    await safe_edit(query, text, reply_markup=xox_keyboard(game["board"], game_key))


async def xox_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    parts = query.data.split("_")
    idx = int(parts[-1])
    game_key = "_".join(parts[1:-1])
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
# 7) SAYI TAHMIN (1-100, mesaj-bazli, pot azalir hint-le)
# ----------------------------------------------------------------------

TAHMIN_USAGE = "🔢 Sayı tahmin oyunu için lütfen şu formatta yazın:\n`/tahminet [miktar]`\nÖrnek: `/tahminet 50`"


async def tahminet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, TAHMIN_USAGE)
    if bet is None:
        return
    if not await take_bet_or_warn(update, user.id, bet):
        return

    chat_id = update.effective_chat.id
    number = random.randint(1, 100)
    TEXT_GAME_STATE[chat_id] = {
        "type": "tahmin",
        "user_id": user.id,
        "number": number,
        "bet": bet,
        "pot": bet,
        "tries": 0,
    }

    await reply(update, f"🔢 Sayı Tahmin! (0-100)\n💰 Bahis: {fmt(bet)} TL\nSayıyı yaz!")


async def handle_tahmin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, state):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.id != state["user_id"]:
        return

    text = update.effective_message.text.strip()
    if not text.lstrip("-").isdigit():
        return

    guess = int(text)
    number = state["number"]

    if guess == number:
        win = state["bet"] * 2
        new_bal = change_balance(user.id, win)
        await reply(update, f"🎉 Doğru! Sayı {number} idi.\n✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL")
        del TEXT_GAME_STATE[chat_id]
        return

    state["tries"] += 1
    state["pot"] = max(0, state["pot"] - max(1, state["bet"] // 10))

    if state["pot"] <= 0 or state["tries"] >= 8:
        new_bal = get_balance(user.id)
        await reply(update, f"💀 Elendin! Sayı: {number}\n💳 Bakiye: {fmt(new_bal)} TL")
        del TEXT_GAME_STATE[chat_id]
        return

    if guess < number:
        await reply(update, f"📈 Yukarı\n💰 Pot: {fmt(state['pot'])} TL")
    else:
        await reply(update, f"📉 Aşağı\n💰 Pot: {fmt(state['pot'])} TL")


# ----------------------------------------------------------------------
# 8) BOSLUK DOLDURMA / BULMACA (Adam Asmaca tarzi)
# ----------------------------------------------------------------------

BULMACA_USAGE = "🧩 Adam asmaca oyunu için lütfen şu formatta yazın:\n`/bulmaca [miktar]`\nÖrnek: `/bulmaca 100`"
BULMACA_LIVES = 3


async def bulmaca_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, BULMACA_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"bulmacasetup_{game_key}"] = {"user_id": user.id, "bet": bet}

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Kolay", callback_data=f"bulmacadiff_{game_key}_kolay"),
        InlineKeyboardButton("🟡 Orta", callback_data=f"bulmacadiff_{game_key}_orta"),
        InlineKeyboardButton("🔴 Zor", callback_data=f"bulmacadiff_{game_key}_zor"),
    ]])
    await reply(update, f"🧩 Bulmaca (Adam Asmaca)\n💰 Bahis: {fmt(bet)} TL\nZorluk seçimi yapın:", reply_markup=keyboard)


def render_bulmaca_word(word, guessed_letters):
    cells = []
    for ch in word:
        if normalize(ch) in guessed_letters:
            cells.append(ch.upper())
        else:
            cells.append("_")
    return " ".join(cells)


async def bulmaca_diff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    body = query.data[len("bulmacadiff_"):]
    game_key, diff = body.rsplit("_", 1)
    setup_key = f"bulmacasetup_{game_key}"
    setup = PENDING_GAMES.get(setup_key)
    if not setup or setup["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun sana ait değil veya süresi geçti.", show_alert=True)
        return

    bal = get_balance(user.id)
    if bal < setup["bet"]:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        del PENDING_GAMES[setup_key]
        return
    change_balance(user.id, -setup["bet"])
    del PENDING_GAMES[setup_key]

    min_len, max_len = HANGMAN_DIFFICULTY[diff]
    word = pick_word(min_len, max_len)

    chat_id = update.effective_chat.id
    TEXT_GAME_STATE[chat_id] = {
        "type": "bulmaca",
        "user_id": user.id,
        "bet": setup["bet"],
        "word": word,
        "guessed": set(),
        "lives": BULMACA_LIVES,
        "diff": diff.title(),
    }

    masked = render_bulmaca_word(word, set())
    text = (
        f"🎮 Bulmaca başladı!\nZorluk: {diff.title()}\nKelime:\n\n`{masked}`\n\n"
        f"({len(word)} harf) ❤️ Hak: {BULMACA_LIVES}\n\n👉 Bir harf gönderin!"
    )
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN)


async def handle_bulmaca_message(update: Update, context: ContextTypes.DEFAULT_TYPE, state):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.id != state["user_id"]:
        return

    raw = update.effective_message.text.strip()
    if len(raw) != 1 or not raw.isalpha():
        return

    letter_n = normalize(raw)
    word = state["word"]
    bet = state["bet"]

    if letter_n in [normalize(c) for c in word]:
        state["guessed"].add(letter_n)
        masked = render_bulmaca_word(word, state["guessed"])
        if "_" not in masked:
            win = int(bet * 2)
            new_bal = change_balance(user.id, win)
            await reply(update, f"🎉 BULDUN! Kelime: `{word.upper()}`\n✅ +{fmt(win)} TL\n💳 Bakiye: {fmt(new_bal)} TL", parse_mode=ParseMode.MARKDOWN)
            del TEXT_GAME_STATE[chat_id]
            return
        await reply(update, f"✅ Doğru!\nKelime: `{masked}`", parse_mode=ParseMode.MARKDOWN)
    else:
        state["lives"] -= 1
        if state["lives"] <= 0:
            del TEXT_GAME_STATE[chat_id]
            await reply(update, f"💀 ELENDİN!\nKelime: `{word.upper()}`\n📉 Kayıp: -{fmt(bet)} TL", parse_mode=ParseMode.MARKDOWN)
            return
        masked = render_bulmaca_word(word, state["guessed"])
        await reply(update, f"❌ Yanlış! ❤️ Kalan Hak: {state['lives']}\nKelime: `{masked}`", parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------------
# 9) KELIME ZINCIRI
# ----------------------------------------------------------------------

KELIME_USAGE = "📝 Kelime zinciri oyunu için lütfen şu formatta yazın:\n`/kelime [miktar]`\nÖrnek: `/kelime 100`"


def kelime_stop_keyboard(game_key):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Oyunu Sonlandır", callback_data=f"kelimestop_{game_key}")]])


async def kelime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, KELIME_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"kelimesetup_{game_key}"] = {"user_id": user.id, "bet": bet}

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Başla", callback_data=f"kelimestart_{game_key}")]])
    text = (
        "📝 Kelime Zinciri Başlıyor!\n"
        "📖 Kural: Bot bir kelime verir, sen son harfiyle yeni bir kelime yazarsın. "
        "Sonra bot senin kelimenin son harfiyle devam eder.\n"
        "⚠️ Önemli: Sadece harflerden oluşan gerçek kelimeler yazmalısın.\n"
        "💰 Kazanç: Her doğru kelime için bahsinin 1.3x katını kazanırsın."
    )
    await reply(update, text, reply_markup=keyboard)


async def kelime_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    game_key = query.data.replace("kelimestart_", "")
    setup_key = f"kelimesetup_{game_key}"
    setup = PENDING_GAMES.get(setup_key)
    if not setup or setup["user_id"] != user.id:
        await query.answer("⚠️ Bu oyun sana ait değil veya süresi geçti.", show_alert=True)
        return

    bal = get_balance(user.id)
    if bal < setup["bet"]:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        del PENDING_GAMES[setup_key]
        return
    change_balance(user.id, -setup["bet"])
    del PENDING_GAMES[setup_key]

    chat_id = update.effective_chat.id
    bot_word = pick_word()
    TEXT_GAME_STATE[chat_id] = {
        "type": "kelime",
        "user_id": user.id,
        "bet": setup["bet"],
        "used_words": {bot_word},
        "last_word": bot_word,
        "game_key": game_key,
    }

    last_letter = bot_word[-1].upper()
    text = f"🤖 Botun Kelimesi: {bot_word.upper()}\n👉 {last_letter} harfi ile bir kelime yaz!"
    await safe_edit(query, text, reply_markup=kelime_stop_keyboard(game_key))


async def kelime_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    chat_id = update.effective_chat.id
    state = TEXT_GAME_STATE.get(chat_id)
    if not state or state["type"] != "kelime" or state["user_id"] != user.id:
        await query.answer("⚠️ Aktif oyunun yok.", show_alert=True)
        return

    del TEXT_GAME_STATE[chat_id]
    await safe_edit(query, "🛑 Kelime zinciri durduruldu.")


async def handle_kelime_message(update: Update, context: ContextTypes.DEFAULT_TYPE, state):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.id != state["user_id"]:
        return

    word = update.effective_message.text.strip().lower()
    if not word.isalpha():
        return

    last_word = state["last_word"]
    last_letter = normalize(last_word[-1])
    bet = state["bet"]

    if normalize(word[0]) != last_letter:
        del TEXT_GAME_STATE[chat_id]
        await reply(
            update,
            f"❌ Yanlış harf! Kelime {last_word[-1].upper()} ile başlamalıydı.\n📉 Kayıp: -{fmt(bet)} TL",
        )
        return

    if word in state["used_words"]:
        await reply(update, "❌ Bu kelime zaten kullanıldı! Başka bir kelime dene.")
        return

    state["used_words"].add(word)
    reward = int(bet * 0.3)
    new_bal = change_balance(user.id, reward)

    bot_word = find_word_for_letter(word[-1], state["used_words"])
    if not bot_word:
        del TEXT_GAME_STATE[chat_id]
        win_total = int(bet * 1.3)
        await reply(
            update,
            f"✅ Doğru! {word.upper()} yazdın. +{fmt(reward)} TL kazandın.\n"
            f"Bakiye: {fmt(new_bal)} TL\n\n🏆 Bot başka kelime bulamadı, oyunu kazandın!",
        )
        return

    state["used_words"].add(bot_word)
    state["last_word"] = bot_word

    await reply(
        update,
        f"✅ Doğru! {word.upper()} yazdın. +{fmt(reward)} TL kazandın.\nBakiye: {fmt(new_bal)} TL",
    )
    last_letter_disp = bot_word[-1].upper()
    text = f"🤖 Botun Kelimesi: {bot_word.upper()}\n👉 {last_letter_disp} harfi ile bir kelime yaz!"
    await reply(update, text, reply_markup=kelime_stop_keyboard(state["game_key"]))


# ----------------------------------------------------------------------
# 10) RUS RULETI
# ----------------------------------------------------------------------

RULETI_USAGE = "🔫 Rus ruleti oynamak için lütfen şu formatta yazın:\n`/rusruleti [miktar]`\nÖrnek: `/rusruleti 100`"


async def rusruleti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, RULETI_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    bullet_chamber = random.randint(1, 6)
    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"ruleti_{game_key}"] = {"user_id": user.id, "bet": bet, "bullet": bullet_chamber}

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"ruletipick_{game_key}_{i}") for i in range(1, 4)],
        [InlineKeyboardButton(f"🔘 Oda {i}", callback_data=f"ruletipick_{game_key}_{i}") for i in range(4, 7)],
    ])
    await reply(update, f"🔫 Rus Ruleti! 6 odadan birinde mermi var.\n💰 Bahis: {fmt(bet)} TL\nBir oda seç:", reply_markup=keyboard)


async def rusruleti_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    parts = query.data.split("_")
    picked = int(parts[-1])
    game_key = "_".join(parts[1:-1])
    state_key = f"ruleti_{game_key}"
    game = PENDING_GAMES.get(state_key)

    if not game:
        await query.answer("⚠️ Oyun bulunamadı veya bitti.", show_alert=True)
        return
    if user.id != game["user_id"]:
        await query.answer("Bu senin oyunun değil!", show_alert=True)
        return

    bal = get_balance(user.id)
    bet = game["bet"]
    if bal < bet:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        del PENDING_GAMES[state_key]
        return
    change_balance(user.id, -bet)
    del PENDING_GAMES[state_key]

    bullet = game["bullet"]
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

ATYARISI_USAGE = "🐎 At yarışı oynamak için lütfen şu formatta yazın:\n`/atyarisi [miktar]`\nÖrnek: `/atyarisi 100`"
HORSES = ["🐎 Yıldırım", "🐎 Kartal", "🐎 Fırtına", "🐎 Şimşek", "🐎 Rüzgar"]


async def atyarisi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name or "Oyuncu", user.username or "")

    bet = await require_amount(update, context.args, ATYARISI_USAGE)
    if bet is None:
        return

    bal = get_balance(user.id)
    if bal < bet:
        await reply(update, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        return

    winner = random.randint(0, len(HORSES) - 1)
    game_key = f"{user.id}_{int(time.time()*1000)}"
    PENDING_GAMES[f"atyar_{game_key}"] = {"user_id": user.id, "bet": bet, "winner": winner}

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(h, callback_data=f"atyarpick_{game_key}_{i}")] for i, h in enumerate(HORSES)]
    )
    await reply(update, f"🐎 At Yarışı başlıyor!\n💰 Bahis: {fmt(bet)} TL\nBahis yapacağın atı seç:", reply_markup=keyboard)


async def atyarisi_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    parts = query.data.split("_")
    picked = int(parts[-1])
    game_key = "_".join(parts[1:-1])
    state_key = f"atyar_{game_key}"
    game = PENDING_GAMES.get(state_key)

    if not game:
        await query.answer("⚠️ Oyun bulunamadı veya bitti.", show_alert=True)
        return
    if user.id != game["user_id"]:
        await query.answer("Bu senin oyunun değil!", show_alert=True)
        return

    bal = get_balance(user.id)
    bet = game["bet"]
    if bal < bet:
        await safe_edit(query, f"❌ Bakiyen yetersiz! 💳 Bakiyen: {fmt(bal)} TL")
        del PENDING_GAMES[state_key]
        return
    change_balance(user.id, -bet)
    del PENDING_GAMES[state_key]

    winner = game["winner"]
    await safe_edit(query, "🐎 Atlar koşuyor... 🏁")
    await asyncio.sleep(1.5)

    order = list(range(len(HORSES)))
    random.shuffle(order)
    if winner in order:
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
# ----------------------------------------------------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.text:
        return
    if update.effective_message.text.startswith("/"):
        return

    chat_id = update.effective_chat.id
    state = TEXT_GAME_STATE.get(chat_id)
    if not state:
        return

    game_type = state["type"]
    if game_type == "bayrak":
        await handle_bayrak_message(update, context, state)
    elif game_type == "tahmin":
        await handle_tahmin_message(update, context, state)
    elif game_type == "bulmaca":
        await handle_bulmaca_message(update, context, state)
    elif game_type == "kelime":
        await handle_kelime_message(update, context, state)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN bulunamadı! export BOT_TOKEN='token_in' komutu ile token'i ekle."
        )
    if not DATABASE_URL:
        raise SystemExit(
            "❌ DATABASE_URL bulunamadı! Railway'de PostgreSQL servisi ekle "
            "(otomatik DATABASE_URL oluşur) ya da export DATABASE_URL='postgresql://...' ver."
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
    app.add_handler(CommandHandler("rusruleti", rusruleti_cmd))
    app.add_handler(CommandHandler("atyarisi", atyarisi_cmd))

    # Menu / profil / zenginler / transfer callback-leri
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^info_"))
    app.add_handler(CallbackQueryHandler(profil_odul_callback, pattern=r"^profil_odul$"))

    # Slot (animasiya daxilen islenir, ayrica callback yoxdur)

    # Bayrak (mesaj-bazli, callback yoxdur)

    # TKM
    app.add_handler(CallbackQueryHandler(tkm_rounds_callback, pattern=r"^tkmrounds_"))
    app.add_handler(CallbackQueryHandler(tkm_mode_callback, pattern=r"^tkmmode_"))
    app.add_handler(CallbackQueryHandler(tkm_join_callback, pattern=r"^tkmjoin_"))
    app.add_handler(CallbackQueryHandler(tkm_pick_callback, pattern=r"^tkmpick_|^tkmmpick_"))

    # Yazi Tura
    app.add_handler(CallbackQueryHandler(yt_pick_callback, pattern=r"^yt_"))

    # Kutu
    app.add_handler(CallbackQueryHandler(kutu_pick_callback, pattern=r"^kutu_"))

    # XOX
    app.add_handler(CallbackQueryHandler(xox_cancel_callback, pattern=r"^xoxcancel_"))
    app.add_handler(CallbackQueryHandler(xox_join_callback, pattern=r"^xoxjoin_"))
    app.add_handler(CallbackQueryHandler(xox_move_callback, pattern=r"^xoxmv_"))

    # Bulmaca
    app.add_handler(CallbackQueryHandler(bulmaca_diff_callback, pattern=r"^bulmacadiff_"))

    # Kelime
    app.add_handler(CallbackQueryHandler(kelime_start_callback, pattern=r"^kelimestart_"))
    app.add_handler(CallbackQueryHandler(kelime_stop_callback, pattern=r"^kelimestop_"))

    # Rus Ruleti
    app.add_handler(CallbackQueryHandler(rusruleti_pick_callback, pattern=r"^ruletipick_"))

    # At Yarisi
    app.add_handler(CallbackQueryHandler(atyarisi_pick_callback, pattern=r"^atyarpick_"))

    # Sade text mesajlari (bayrak / sayi tahmin / bulmaca / kelime zinciri ucun)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot başladı...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
