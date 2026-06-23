from dotenv import load_dotenv
load_dotenv("/root/.env_aura")
import asyncio
import sqlite3
import logging
import uuid
import os
import json
import re
import math
import unicodedata
from pathlib import Path
import hashlib
import random
from urllib.parse import quote
from zoneinfo import ZoneInfo
import httpx
from datetime import datetime, timedelta, timezone
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
import anthropic
import gspread
from google.oauth2.service_account import Credentials

try:
    from astronomy import Body, Ecliptic, GeoVector, MoonPhase, SiderealTime, Time as AstroTime
except Exception:  # optional premium calculations dependency
    Body = Ecliptic = GeoVector = MoonPhase = SiderealTime = AstroTime = None

try:
    from timezonefinder import TimezoneFinder
except Exception:  # optional premium calculations dependency
    TimezoneFinder = None

# ========== КОНФИГ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = "myaura_mystic_bot"
CHANNEL_ID = "@aurabot_mystic"
OPENAI_KEY = os.getenv("OPENAI_KEY", "").strip()
CLAUDE_KEY = os.getenv("CLAUDE_KEY", "").strip()
PHOTO_AI_PROVIDER = os.getenv("PHOTO_AI_PROVIDER", "openai").strip().lower()
PHOTO_VISION_MODEL = os.getenv("PHOTO_VISION_MODEL", "gpt-4o-mini").strip()
CLAUDE_PHOTO_MODEL = os.getenv("CLAUDE_PHOTO_MODEL", "claude-opus-4-6").strip()
OWNER_ID = 549639607
SUPPORT_URL = "https://t.me/Boss023rus"

ONE_TIME_PRODUCTS = {
    "once_money_code": {"feature": "money_code", "title": "Денежный код", "amount": 199},
    "once_matrix": {"feature": "matrix", "title": "Матрица судьбы", "amount": 249},
    "once_forecast": {"feature": "annual_forecast", "title": "Прогноз на год", "amount": 299},
    "once_natal": {"feature": "natal", "title": "Натальная карта", "amount": 349},
}
FEATURE_TO_PRODUCT = {v["feature"]: k for k, v in ONE_TIME_PRODUCTS.items()}
PLATFORM_NAME = "Telegram"


def clean_display_text(text):
    """Remove raw Markdown artifacts from bot answers and channel posts."""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value, flags=re.S)
    value = re.sub(r"__(.*?)__", r"\1", value, flags=re.S)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = value.replace("```", "").replace("`", "")
    value = value.replace("**", "").replace("__", "").replace("*", "")
    value = re.sub(r"(?m)^\s*[-–—]\s+", "• ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


BASE_DIR = Path(__file__).resolve().parent
AURA_ASSET_DIR = Path(os.getenv("AURA_ASSET_DIR", str(BASE_DIR / "aura_assets")))
INTRO_IMAGE_PATH = AURA_ASSET_DIR / "aura_intro_premium.png"

ZODIAC_SIGNS = (
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы",
)
RUSSIAN_LETTERS = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"


def truncate_at_sentence(text, limit):
    value = clean_display_text(text)
    if len(value) <= limit:
        return value
    cut = value[: max(1, limit - 1)]
    candidates = [cut.rfind(mark) for mark in (". ", "! ", "? ", "\n")]
    boundary = max(candidates)
    if boundary >= int(limit * 0.55):
        cut = cut[: boundary + 1]
    else:
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
    return cut.rstrip(" ,;:-") + "…"


def _digits_sum(value):
    return sum(int(ch) for ch in str(value) if ch.isdigit())


def reduce_number(value, preserve_master=True):
    number = abs(int(value))
    while number > 9 and not (preserve_master and number in (11, 22, 33)):
        number = _digits_sum(number)
    return number


def reduce_to_22(value):
    number = abs(int(value))
    while number > 22:
        number = _digits_sum(number)
    return number or 22


def parse_birth_date(text):
    match = re.search(r"\b(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/]((?:19|20)\d{2})\b", text or "")
    if not match:
        return None
    try:
        return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def calculate_numerology_data(birth_dt, current_dt=None):
    current_dt = current_dt or datetime.now()
    digit_total = _digits_sum(birth_dt.strftime("%d%m%Y"))
    life_path = reduce_number(digit_total)
    birthday = reduce_number(birth_dt.day)
    attitude = reduce_number(birth_dt.day + birth_dt.month)
    personal_year = reduce_number(birth_dt.day + birth_dt.month + _digits_sum(current_dt.year))
    personal_month = reduce_number(personal_year + current_dt.month)
    personal_day = reduce_number(personal_month + current_dt.day)
    return {
        "life_path": life_path,
        "birthday": birthday,
        "attitude": attitude,
        "personal_year": personal_year,
        "personal_month": personal_month,
        "personal_day": personal_day,
    }


def calculate_matrix22_data(birth_dt):
    a = reduce_to_22(birth_dt.day)
    b = reduce_to_22(birth_dt.month)
    c = reduce_to_22(_digits_sum(birth_dt.year))
    d = reduce_to_22(a + b + c)
    center = reduce_to_22(a + b + c + d)
    return {
        "day_arcana": a,
        "month_arcana": b,
        "year_arcana": c,
        "destiny_arcana": d,
        "center_arcana": center,
        "money_arcana": reduce_to_22(b + c + d),
        "relationship_arcana": reduce_to_22(a + b + d),
        "talent_arcana": reduce_to_22(a + c),
    }


def normalize_name(value):
    normalized = unicodedata.normalize("NFKD", (value or "").upper().replace("Ё", "Е"))
    return "".join(ch for ch in normalized if ch in RUSSIAN_LETTERS)


def calculate_name_number(full_name):
    letters = normalize_name(full_name)
    total = 0
    for ch in letters:
        index = RUSSIAN_LETTERS.index(ch) + 1
        total += ((index - 1) % 9) + 1
    return reduce_number(total, preserve_master=False) if total else 0


def calculate_money_code_data(full_name, birth_dt, current_dt=None):
    current_dt = current_dt or datetime.now()
    numerology = calculate_numerology_data(birth_dt, current_dt)
    name_number = calculate_name_number(full_name)
    day_number = reduce_number(birth_dt.day, preserve_master=False)
    year_number = reduce_number(numerology["personal_year"], preserve_master=False)
    life_digit = reduce_number(numerology["life_path"], preserve_master=False)
    code = f"{life_digit}{name_number or 1}{day_number}{year_number}"
    return {**numerology, "name_number": name_number, "money_code": code}


def longitude_to_sign(longitude):
    lon = float(longitude) % 360.0
    index = int(lon // 30)
    degree = lon % 30
    return f"{ZODIAC_SIGNS[index]} {int(degree)}°{int((degree % 1) * 60):02d}′"


def angular_distance(a, b):
    diff = abs((float(a) - float(b)) % 360.0)
    return min(diff, 360.0 - diff)


def moon_phase_snapshot(dt=None):
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if AstroTime is None or MoonPhase is None:
        return {"name": "фаза не рассчитана", "angle": None}
    utc = dt.astimezone(timezone.utc)
    astro_time = AstroTime.Make(utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second)
    angle = float(MoonPhase(astro_time)) % 360.0
    if angle < 22.5 or angle >= 337.5:
        name = "новолуние"
    elif angle < 67.5:
        name = "растущий серп"
    elif angle < 112.5:
        name = "первая четверть"
    elif angle < 157.5:
        name = "растущая Луна"
    elif angle < 202.5:
        name = "полнолуние"
    elif angle < 247.5:
        name = "убывающая Луна"
    elif angle < 292.5:
        name = "последняя четверть"
    else:
        name = "убывающий серп"
    return {"name": name, "angle": round(angle, 1)}

# Лимиты
FREE_REQUESTS = 5
FREE_PSYCHO = 15
START_PSYCHO = 100
START_PHOTO = 5

# ЮКасса
YOOKASSA_SHOP_ID = "1363324"
YOOKASSA_SECRET = os.getenv("YOOKASSA_SECRET", "").strip()

# ========== GOOGLE SHEETS — КОМПАКТНАЯ КОММЕРЧЕСКАЯ АНАЛИТИКА ==========
GOOGLE_CREDS_PATH = "/root/google_credentials.json"
SPREADSHEET_NAME = "PostGenius Users"
GOOGLE_SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
USERS_SHEET_NAME = "Aura Telegram"
SALES_SHEET_NAME = "Продажи Aura"

USERS_HEADERS = ["Последнее посещение", "ID", "Имя", "Username", "Запросы", "Подписка", "До", "Отзыв"]
SALES_HEADERS = ["Дата", "Платформа", "ID", "Имя", "Тариф", "Сумма", "Подписка до", "Источник"]

def _open_spreadsheet():
    if GOOGLE_SPREADSHEET_ID:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        return gc.open_by_key(GOOGLE_SPREADSHEET_ID)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(SPREADSHEET_NAME)

def _get_or_create_sheet(title, headers, rows=2000):
    spreadsheet = _open_spreadsheet()
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=rows, cols=max(10, len(headers) + 2))
        ws.append_row(headers)
    current = ws.row_values(1)
    if current != headers:
        ws.update("A1", [headers])
    return ws

def _sheet_row_by_user(ws, user_id):
    ids = ws.col_values(2)
    uid = str(user_id)
    for index, value in enumerate(ids[1:], start=2):
        if value == uid:
            return index
    return None

def _usage_count(user_id):
    try:
        lim = get_limits(user_id)
        return int(lim.get("requests", 0)) + int(lim.get("psycho", 0)) + int(lim.get("chiromancy", 0)) + int(lim.get("physio", 0)) + int(lim.get("grapho", 0))
    except Exception:
        return 0

def sheets_sync_user(user_id, first_name="", username="", review_text=None):
    """Одна строка на пользователя: посещение, запросы, подписка и отзыв."""
    try:
        ws = _get_or_create_sheet(USERS_SHEET_NAME, USERS_HEADERS)
        plan, sub_end = get_subscription(user_id)
        plan_name = {"aura_start": "Старт", "aura_pro": "Про"}.get(plan, "Бесплатный")
        until = sub_end.strftime("%d.%m.%Y") if sub_end else "—"
        row = _sheet_row_by_user(ws, user_id)
        old_review = ""
        if row:
            old_review = ws.cell(row, 8).value or ""
        values = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            str(user_id),
            first_name or "—",
            ("@" + username) if username else "—",
            str(_usage_count(user_id)),
            plan_name,
            until,
            review_text if review_text is not None else old_review,
        ]
        if row:
            ws.update(f"A{row}:H{row}", [values])
        else:
            ws.append_row(values)
    except Exception as e:
        logging.error(f"Google Sheets user sync: {e}")

def sheets_log_visit(user_id, first_name, username, plan=None):
    sheets_sync_user(user_id, first_name, username)

def sheets_log_review(user_id, first_name, username, review_text):
    sheets_sync_user(user_id, first_name, username, review_text=review_text[:1000])

def get_user_source(user_id):
    try:
        with sqlite3.connect(DB) as conn:
            row = conn.execute("SELECT source FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        return (row[0] or "—") if row else "—"
    except Exception:
        return "—"


def sheets_log_sale(user_id, first_name, plan, amount, sub_end, platform):
    try:
        ws = _get_or_create_sheet(SALES_SHEET_NAME, SALES_HEADERS)
        plan_name = {"aura_start": "Старт", "aura_pro": "Про", "aura_pro_year": "Про на год"}.get(plan, plan)
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"), platform, str(user_id), first_name or "—",
            plan_name, f"{amount} ₽", sub_end.strftime("%d.%m.%Y") if sub_end else "—", get_user_source(user_id)
        ])
        sheets_sync_user(user_id, first_name, "")
    except Exception as e:
        logging.error(f"Google Sheets sale log: {e}")


# ========== ЛОГИ ==========
logging.basicConfig(level=logging.INFO)

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY, base_url="https://api.proxyapi.ru/openai/v1")
claude_client = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None

# ========== TELEGRAM API ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def telegram_keyboard(buttons):
    if not buttons:
        return None
    rows = []
    for row in buttons:
        tg_row = []
        for button in row:
            if button.get("type") == "link":
                tg_row.append(InlineKeyboardButton(text=button["text"], url=button["url"]))
            else:
                tg_row.append(InlineKeyboardButton(
                    text=button["text"],
                    callback_data=button.get("payload", "noop")
                ))
        rows.append(tg_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def send_message(chat_id, text, buttons=None):
    return await bot.send_message(
        chat_id,
        clean_display_text(text)[:4096],
        reply_markup=telegram_keyboard(buttons),
        disable_web_page_preview=True
    )

async def get_photo(photo_url):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(photo_url)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
            logging.error(f"Ошибка скачивания фото: {r.status_code}")
            return None
    except Exception as e:
        logging.error(f"Ошибка get_photo: {e}")
        return None

# ========== КНОПКИ ==========
def main_menu_buttons():
    return [
        [{"type": "callback", "text": "🔮 Разобрать ситуацию", "payload": "cat_situation"}],
        [{"type": "callback", "text": "❤️ Отношения", "payload": "cat_love"},
         {"type": "callback", "text": "💰 Деньги", "payload": "cat_money"}],
        [{"type": "callback", "text": "🧠 Психолог", "payload": "psycho"},
         {"type": "callback", "text": "📔 Дневник", "payload": "diary"}],
        [{"type": "callback", "text": "✨ Узнать себя", "payload": "cat_self"}],
        [{"type": "callback", "text": "🌟 Мой день", "payload": "my_day"},
         {"type": "callback", "text": "👤 Профиль", "payload": "profile"}],
        [{"type": "callback", "text": "💎 Тарифы", "payload": "tariffs"},
         {"type": "callback", "text": "🎁 Пригласить", "payload": "referral"}],
        [{"type": "callback", "text": "⭐️ Отзыв", "payload": "review"}],
        [{"type": "callback", "text": "💬 Поддержка", "payload": "support"}],
    ]

def category_buttons(category):
    menus = {
        "situation": [
            [{"type":"callback","text":"🃏 Таро на ситуацию","payload":"taro"}],
            [{"type":"callback","text":"💤 Толкование сна","payload":"dreams"}],
            [{"type":"callback","text":"📅 Прогноз на период","payload":"forecast_period"}],
            [{"type":"callback","text":"📝 Вечерняя рефлексия","payload":"evening_reflection"}],
        ],
        "love": [
            [{"type":"callback","text":"❤️ Совместимость по датам","payload":"compatibility"}],
            [{"type":"callback","text":"👫 Совместимость по фото","payload":"compat_photo"}],
            [{"type":"callback","text":"🃏 Таро на отношения","payload":"taro"}],
        ],
        "money": [
            [{"type":"callback","text":"💬 Разобрать денежный сценарий","payload":"money_scenario"}],
            [{"type":"callback","text":"💰 Денежный код","payload":"money_code"}],
            [{"type":"callback","text":"🌌 Матрица судьбы","payload":"matrix"}],
            [{"type":"callback","text":"📊 Прогноз на год","payload":"annual_forecast"}],
        ],
        "self": [
            [{"type":"callback","text":"🔢 Нумерология","payload":"numerology"}],
            [{"type":"callback","text":"🌈 Энергия по дате","payload":"aura"}],
            [{"type":"callback","text":"🔮 Аура по фото","payload":"aura_photo"}],
            [{"type":"callback","text":"🖐 Хиромантия","payload":"chiromancy"}],
            [{"type":"callback","text":"😊 Впечатление по фото","payload":"physio"}],
            [{"type":"callback","text":"✍️ Графология","payload":"grapho"}],
            [{"type":"callback","text":"♈ Натальная карта","payload":"natal"}],
        ],
    }
    return menus.get(category, []) + [[{"type":"callback","text":"🔙 В меню","payload":"back_menu"}]]

def back_button():
    return [[{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]]

def upgrade_buttons(plan="any"):
    if plan == "start":
        return [
            [{"type": "callback", "text": "🔥 Открыть Про · 390 ₽", "payload": "pay_pro"}],
            [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
        ]
    return [
        [{"type": "callback", "text": "🟢 Старт · 190 ₽", "payload": "pay_start"}],
        [{"type": "callback", "text": "🔥 Про · 390 ₽", "payload": "pay_pro"}],
        [{"type": "callback", "text": "💜 Про на год · 2 990 ₽", "payload": "pay_year"}],
        [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
    ]

def psycho_buttons():
    return [
        [{"type": "callback", "text": "🔄 Новый разговор", "payload": "psycho_new"}],
        [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
    ]

# ========== БАЗА ДАННЫХ ==========
DB = "/root/aura.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        step TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        registered_at TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS limits (
        user_id INTEGER PRIMARY KEY,
        requests INTEGER DEFAULT 0,
        psycho_messages INTEGER DEFAULT 0,
        photo_chiromancy INTEGER DEFAULT 0,
        photo_physio INTEGER DEFAULT 0,
        photo_grapho INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY,
        plan TEXT DEFAULT '',
        sub_end TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS psycho_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        content TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS diary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        entry TEXT,
        response TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER,
        plan TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS payment_history (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        plan TEXT NOT NULL,
        platform TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'succeeded',
        processed_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referral_rewards (
        payment_id TEXT PRIMARY KEY,
        referred_user_id INTEGER NOT NULL,
        referrer_id INTEGER NOT NULL,
        rewarded_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        review TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, event TEXT,
        feature TEXT DEFAULT '', source TEXT DEFAULT '', value TEXT DEFAULT '', created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS processed_updates (
        update_key TEXT PRIMARY KEY, processed_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slot_key TEXT UNIQUE, rubric TEXT,
        topic TEXT DEFAULT '', content TEXT, status TEXT DEFAULT 'pending', published_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY, birth_date TEXT DEFAULT '', birth_time TEXT DEFAULT '',
        birth_place TEXT DEFAULT '', focus TEXT DEFAULT '', source TEXT DEFAULT '',
        referrer_id INTEGER, stopped INTEGER DEFAULT 0, consent_photo INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS usage_periods (
        user_id INTEGER PRIMARY KEY, period_start TEXT, period_end TEXT,
        requests INTEGER DEFAULT 0, psycho INTEGER DEFAULT 0, photo INTEGER DEFAULT 0)""")

    c.execute("""CREATE TABLE IF NOT EXISTS one_time_credits (
        user_id INTEGER NOT NULL,
        feature TEXT NOT NULL,
        credits INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, feature)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sales_prompts (
        user_id INTEGER NOT NULL,
        milestone TEXT NOT NULL,
        shown_at TEXT NOT NULL,
        PRIMARY KEY (user_id, milestone)
    )""")
    # Миграция старых разовых кредитов: ранее прогноз на год хранился как feature='forecast'.
    c.execute("""INSERT INTO one_time_credits(user_id, feature, credits, updated_at)
                 SELECT user_id, 'annual_forecast', credits, updated_at
                 FROM one_time_credits
                 WHERE feature='forecast'
                 ON CONFLICT(user_id, feature) DO UPDATE SET
                    credits=one_time_credits.credits + excluded.credits,
                    updated_at=excluded.updated_at""")
    c.execute("DELETE FROM one_time_credits WHERE feature='forecast'")
    c.execute("""CREATE TABLE IF NOT EXISTS geo_cache (
        place_key TEXT PRIMARY KEY,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        timezone TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    conn.commit()
    conn.close()

def get_user(user_id, username="", first_name=""):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at) VALUES (?,?,?,?)",
              (user_id, username, first_name, datetime.now().isoformat()))
    c.execute("INSERT OR IGNORE INTO limits (user_id) VALUES (?)", (user_id,))
    conn.commit()
    c.execute("SELECT step, birth_date FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {"step": row[0], "birth_date": row[1]}


def parse_natal_input(text):
    match = re.search(
        r"\b(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/]((?:19|20)\d{2})\s+([01]?\d|2[0-3]):([0-5]\d)\s+(.+)$",
        (text or "").strip(),
        flags=re.S,
    )
    if not match:
        return None
    try:
        local_dt = datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)), int(match.group(4)), int(match.group(5)))
    except ValueError:
        return None
    place = " ".join(match.group(6).strip().split())
    if len(place) < 2:
        return None
    return local_dt, place


async def geocode_place(place):
    key = " ".join((place or "").lower().split())
    if not key:
        raise ValueError("Не указано место рождения")
    with db_connect() as conn:
        cached = conn.execute(
            "SELECT latitude,longitude,timezone,display_name FROM geo_cache WHERE place_key=?",
            (key,),
        ).fetchone()
    if cached:
        return {"lat": float(cached[0]), "lon": float(cached[1]), "timezone": cached[2], "display_name": cached[3] or place}
    if TimezoneFinder is None:
        raise RuntimeError("Не установлена библиотека timezonefinder")
    headers = {"User-Agent": "AuraBot/10.0 (birth-chart geocoder)"}
    params = {"q": place, "format": "jsonv2", "limit": 1, "accept-language": "ru"}
    async with httpx.AsyncClient(timeout=25, headers=headers) as client:
        response = await client.get("https://nominatim.openstreetmap.org/search", params=params)
        response.raise_for_status()
        rows = response.json()
    if not rows:
        raise ValueError("Не удалось найти место рождения. Укажи город и страну, например: Москва, Россия")
    lat = float(rows[0]["lat"]); lon = float(rows[0]["lon"])
    timezone_name = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    if not timezone_name:
        raise ValueError("Не удалось определить часовой пояс места рождения")
    display_name = rows[0].get("display_name") or place
    with db_connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO geo_cache(place_key,latitude,longitude,timezone,display_name,updated_at) VALUES (?,?,?,?,?,?)",
            (key, lat, lon, timezone_name, display_name, datetime.now(timezone.utc).isoformat()),
        )
    return {"lat": lat, "lon": lon, "timezone": timezone_name, "display_name": display_name}


def _mean_obliquity_deg(utc_dt):
    y = utc_dt.year + (utc_dt.timetuple().tm_yday - 1) / 365.2425
    t = (y - 2000.0) / 100.0
    return 23.43929111 - 0.013004167 * t - 0.000000164 * t * t + 0.000000504 * t * t * t


def _ecliptic_ra_dec(lon_deg, obliquity_deg):
    lam = math.radians(lon_deg)
    eps = math.radians(obliquity_deg)
    ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam)) % (2 * math.pi)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    return ra, dec


def _altitude_for_ecliptic(lon_deg, local_sidereal_deg, latitude_deg, obliquity_deg):
    ra, dec = _ecliptic_ra_dec(lon_deg, obliquity_deg)
    hour_angle = math.radians(local_sidereal_deg) - ra
    hour_angle = (hour_angle + math.pi) % (2 * math.pi) - math.pi
    lat = math.radians(latitude_deg)
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt)))), hour_angle


def calculate_ascendant(local_sidereal_deg, latitude_deg, obliquity_deg):
    roots = []
    previous_lon = 0.0
    previous_alt, _ = _altitude_for_ecliptic(previous_lon, local_sidereal_deg, latitude_deg, obliquity_deg)
    for index in range(1, 3601):
        lon = index / 10.0
        altitude, _ = _altitude_for_ecliptic(lon, local_sidereal_deg, latitude_deg, obliquity_deg)
        if previous_alt == 0 or altitude == 0 or previous_alt * altitude < 0:
            lo, hi = previous_lon, lon
            for _ in range(35):
                mid = (lo + hi) / 2
                mid_alt, _ = _altitude_for_ecliptic(mid, local_sidereal_deg, latitude_deg, obliquity_deg)
                lo_alt, _ = _altitude_for_ecliptic(lo, local_sidereal_deg, latitude_deg, obliquity_deg)
                if lo_alt * mid_alt <= 0:
                    hi = mid
                else:
                    lo = mid
            root = ((lo + hi) / 2) % 360
            _, hour_angle = _altitude_for_ecliptic(root, local_sidereal_deg, latitude_deg, obliquity_deg)
            roots.append((root, hour_angle))
        previous_lon, previous_alt = lon, altitude
    eastern = [root for root, hour_angle in roots if hour_angle < 0]
    if not eastern:
        raise RuntimeError("Не удалось рассчитать асцендент")
    return eastern[0] % 360


def _major_aspects(positions):
    aspect_defs = ((0, "соединение", 8), (60, "секстиль", 5), (90, "квадрат", 6), (120, "трин", 6), (180, "оппозиция", 8))
    items = list(positions.items())
    results = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            name_a, lon_a = items[i]; name_b, lon_b = items[j]
            distance = angular_distance(lon_a, lon_b)
            for exact, label, orb in aspect_defs:
                delta = abs(distance - exact)
                if delta <= orb:
                    results.append((delta, f"{name_a} — {label} — {name_b} (орб {delta:.1f}°)"))
                    break
    results.sort(key=lambda item: item[0])
    return [item[1] for item in results[:10]]


async def calculate_natal_chart(text):
    parsed = parse_natal_input(text)
    if not parsed:
        raise ValueError("Напиши дату, точное время и место рождения. Пример: 15.03.1990 14:30 Москва, Россия")
    if AstroTime is None or GeoVector is None or Ecliptic is None or SiderealTime is None:
        raise RuntimeError("Не установлена библиотека astronomy-engine")
    local_dt, place = parsed
    geo = await geocode_place(place)
    tz = ZoneInfo(geo["timezone"])
    aware_local = local_dt.replace(tzinfo=tz)
    utc_dt = aware_local.astimezone(timezone.utc)
    astro_time = AstroTime.Make(utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.hour, utc_dt.minute, utc_dt.second)
    bodies = {
        "Солнце": Body.Sun, "Луна": Body.Moon, "Меркурий": Body.Mercury,
        "Венера": Body.Venus, "Марс": Body.Mars, "Юпитер": Body.Jupiter,
        "Сатурн": Body.Saturn, "Уран": Body.Uranus, "Нептун": Body.Neptune,
        "Плутон": Body.Pluto,
    }
    positions = {}
    for title, body in bodies.items():
        positions[title] = float(Ecliptic(GeoVector(body, astro_time, True)).elon) % 360.0
    local_sidereal_deg = (float(SiderealTime(astro_time)) * 15.0 + geo["lon"]) % 360.0
    ascendant = calculate_ascendant(local_sidereal_deg, geo["lat"], _mean_obliquity_deg(utc_dt))
    houses = {index: (ascendant + (index - 1) * 30.0) % 360.0 for index in range(1, 13)}
    planet_houses = {}
    for title, longitude in positions.items():
        planet_houses[title] = int(((longitude - ascendant) % 360.0) // 30.0) + 1
    return {
        "input": text,
        "place": geo["display_name"],
        "timezone": geo["timezone"],
        "utc": utc_dt.isoformat(),
        "positions": positions,
        "ascendant": ascendant,
        "houses": houses,
        "planet_houses": planet_houses,
        "aspects": _major_aspects(positions),
        "system": "равнодомная система домов",
    }


def format_natal_chart_data(data):
    planets = "\n".join(
        f"• {name}: {longitude_to_sign(lon)}, дом {data['planet_houses'][name]}"
        for name, lon in data["positions"].items()
    )
    aspects = "\n".join(f"• {item}" for item in data["aspects"]) or "• точных мажорных аспектов в выбранных орбах не найдено"
    return (
        f"Место: {data['place']}\nЧасовой пояс: {data['timezone']}\n"
        f"Система домов: {data['system']}\nАсцендент: {longitude_to_sign(data['ascendant'])}\n\n"
        f"Планеты:\n{planets}\n\nМажорные аспекты:\n{aspects}"
    )

def set_step(user_id, step):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def get_subscription(user_id):
    """Return only an active subscription; expired access is revoked immediately."""
    conn = sqlite3.connect(DB, timeout=10)
    row = conn.execute(
        "SELECT plan, sub_end FROM subscriptions WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row or not row[1]:
        conn.close()
        return None, None
    try:
        sub_end = datetime.fromisoformat(row[1])
    except (TypeError, ValueError):
        conn.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return None, None
    if sub_end > datetime.now():
        conn.close()
        return row[0], sub_end
    conn.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return None, None


def set_subscription(user_id, plan, days):
    """Activate or extend a plan and start a fresh usage period."""
    normalized_plan = "aura_pro" if plan == "aura_pro_year" else plan
    now = datetime.now()
    conn = sqlite3.connect(DB, timeout=10)
    current = conn.execute(
        "SELECT plan, sub_end FROM subscriptions WHERE user_id=?",
        (user_id,),
    ).fetchone()
    base = now
    if current and current[1]:
        try:
            old_end = datetime.fromisoformat(current[1])
            if old_end > now and current[0] == normalized_plan:
                base = old_end
        except (TypeError, ValueError):
            pass
    end = (base + timedelta(days=days)).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO subscriptions (user_id, plan, sub_end) VALUES (?,?,?)",
        (user_id, normalized_plan, end),
    )
    conn.execute(
        "UPDATE limits SET requests=0, psycho_messages=0, photo_chiromancy=0, "
        "photo_physio=0, photo_grapho=0 WHERE user_id=?",
        (user_id,),
    )
    try:
        conn.execute(
            "INSERT OR REPLACE INTO usage_periods "
            "(user_id, period_start, period_end, requests, psycho, photo) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, now.isoformat(), end, 0, 0, 0),
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()
    return datetime.fromisoformat(end)


def get_limits(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO limits (user_id) VALUES (?)", (user_id,))
    c.execute("SELECT requests, psycho_messages, photo_chiromancy, photo_physio, photo_grapho FROM limits WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return {"requests": row[0], "psycho": row[1], "chiromancy": row[2], "physio": row[3], "grapho": row[4]}

def increment_limit(user_id, field):
    conn = sqlite3.connect(DB)
    conn.execute(f"UPDATE limits SET {field}={field}+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_psycho_history(user_id, limit=20):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT role, content FROM psycho_history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def add_psycho_message(user_id, role, content):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO psycho_history (user_id, role, content, created_at) VALUES (?,?,?,?)",
                 (user_id, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def clear_psycho_history(user_id):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM psycho_history WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_diary_history(user_id, limit=5):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT entry, response, created_at FROM diary WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def add_diary_entry(user_id, entry, response):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO diary (user_id, entry, response, created_at) VALUES (?,?,?,?)",
                 (user_id, entry, response, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def save_pending_payment(payment_id, user_id, plan):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO pending_payments (payment_id, user_id, plan, created_at) VALUES (?,?,?,?)",
                 (payment_id, user_id, plan, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_pending_payments():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT payment_id, user_id, plan FROM pending_payments")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_pending_payment(payment_id):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM pending_payments WHERE payment_id=?", (payment_id,))
    conn.commit()
    conn.close()

def save_review(user_id, username, first_name, review_text):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO reviews (user_id, username, first_name, review, created_at) VALUES (?,?,?,?,?)",
                 (user_id, username or "", first_name or "", review_text, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_one_time_credit(user_id, feature):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT credits FROM one_time_credits WHERE user_id=? AND feature=?",
            (user_id, feature),
        ).fetchone()
    return int(row[0]) if row else 0


def add_one_time_credit(user_id, feature, count=1):
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO one_time_credits(user_id,feature,credits,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id,feature) DO UPDATE SET credits=credits+excluded.credits, updated_at=excluded.updated_at",
            (user_id, feature, int(count), datetime.now().isoformat()),
        )


def consume_one_time_credit(user_id, feature):
    """Atomically consume one credit. Called only after a successful AI result."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT credits FROM one_time_credits WHERE user_id=? AND feature=?",
            (user_id, feature),
        ).fetchone()
        if not row or int(row[0]) <= 0:
            return False
        conn.execute(
            "UPDATE one_time_credits SET credits=credits-1, updated_at=? WHERE user_id=? AND feature=?",
            (datetime.now().isoformat(), user_id, feature),
        )
    return True


def one_time_offer_buttons(feature):
    product_code = FEATURE_TO_PRODUCT.get(feature)
    product = ONE_TIME_PRODUCTS.get(product_code)
    if not product:
        return upgrade_buttons()
    return [
        [{"type":"callback", "text":f"✨ Купить один разбор — {product['amount']} ₽", "payload":f"pay_{product_code}"}],
        [{"type":"callback", "text":"🔥 Открыть всё на месяц — 390 ₽", "payload":"pay_pro"}],
        [{"type":"callback", "text":"🔙 В меню", "payload":"back_menu"}],
    ]


async def send_one_time_offer(chat_id, feature):
    product_code = FEATURE_TO_PRODUCT[feature]
    product = ONE_TIME_PRODUCTS[product_code]
    await send_message(
        chat_id,
        f"{product['title']}\n\nПолучить один полный персональный разбор — {product['amount']} ₽.\n\n"
        "Или открыть все глубокие функции Ауры на 30 дней в тарифе Про — 390 ₽.\n"
        "Разовая покупка не оформляет подписку и не продлевается автоматически.",
        one_time_offer_buttons(feature),
    )


def claim_sales_prompt(user_id, milestone):
    try:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO sales_prompts(user_id,milestone,shown_at) VALUES (?,?,?)",
                (user_id, milestone, datetime.now().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def build_result_offer(user_id, feature, used_one_time=False):
    """Return (note, buttons). Each milestone is shown only once per user."""
    plan, _ = get_subscription(user_id)
    if plan:
        return "", back_button()
    lim = get_limits(user_id)
    used = int(lim.get("requests", 0))
    milestone = None
    note = ""
    if used_one_time:
        milestone = f"once_{feature}"
        note = "\n\n✨ Разбор завершён. В Аура Про за 390 ₽ доступны все глубокие функции на 30 дней — без покупки каждого разбора отдельно."
    elif used == 1:
        milestone = "first_result"
        note = "\n\n✨ Это твой первый личный результат в Ауре. В Про можно открыть Матрицу судьбы, прогнозы, денежный код, натальную карту и остальные глубокие функции."
    elif used == 3:
        milestone = "third_result"
        note = "\n\nТы уже попробовала 3 персональных разбора. В Аура Про все возможности открываются на 30 дней, без оплаты каждого результата отдельно."
    elif used >= max(0, FREE_REQUESTS - 1):
        milestone = "last_free_results"
        remaining = max(0, FREE_REQUESTS - used)
        note = f"\n\nОсталось бесплатных разборов: {remaining}. Можно продолжить бесплатно или открыть все возможности Ауры на месяц."
    if not milestone or not claim_sales_prompt(user_id, milestone):
        return "", back_button()
    rows = []
    related_products = {
        "money_scenario": "once_money_code",
        "numerology": "once_matrix",
        "forecast_period": "once_forecast",
    }
    product_code = FEATURE_TO_PRODUCT.get(feature) or related_products.get(feature)
    if product_code and not used_one_time:
        product = ONE_TIME_PRODUCTS[product_code]
        rows.append([{"type":"callback", "text":f"✨ Один разбор — {product['amount']} ₽", "payload":f"pay_{product_code}"}])
    rows.append([{"type":"callback", "text":"🔥 Открыть все возможности — 390 ₽", "payload":"pay_pro"}])
    rows.append([{"type":"callback", "text":"🔙 В меню", "payload":"back_menu"}])
    return note, rows

# ========== ПРОВЕРКА ДОСТУПА ==========
async def check_access(user_id, feature="general"):
    plan, sub_end = get_subscription(user_id)
    lim = get_limits(user_id)

    if plan == "aura_pro":
        return "pro"

    if feature == "psycho":
        if plan == "aura_start":
            return "ok" if lim["psycho"] < START_PSYCHO else "limit_psycho_start"
        return "ok" if lim["psycho"] < FREE_PSYCHO else "limit_psycho_free"

    if feature == "diary":
        if plan in ("aura_start", "aura_pro"):
            return "ok"
        return "diary_blocked"

    if feature in ("compat_photo", "taro_photo", "aura_photo"):
        return "start_block"

    # Эти четыре продукта продаются отдельно или входят в Про.
    # Они никогда не расходуют пять бесплатных базовых разборов.
    if feature in FEATURE_TO_PRODUCT:
        return "start_block"

    photo_map = {"chiromancy": "chiromancy", "physio": "physio", "grapho": "grapho"}
    if feature in photo_map:
        if plan == "aura_start":
            return "ok" if lim[feature] < START_PHOTO else "limit_photo"
        return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

    if plan == "aura_start":
        return "ok"

    return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

# ========== ПРОМПТЫ ==========
PSYCHO_SYSTEM = """Ты мудрый психолог и коуч с 20-летним опытом. Помогаешь людям разобраться в себе.
Говоришь тепло, человечно, как близкий друг. Пишешь только на русском.
Никогда не начинай с Конечно, Отлично, Вот, Готово. Обращайся на ты.
Задаёшь уточняющие вопросы. Даёшь конкретные техники и советы. Помнишь всё что человек рассказывал."""

DIARY_SYSTEM = """Ты тихий хранитель дневника. Человек записывает мысли.
Никаких советов. Никакого анализа. Просто скажи одним-двумя предложениями что услышал.
Потом задай один простой тёплый вопрос. Максимум 3 предложения. Пишешь только на русском."""

MONEY_SCENARIO_SYSTEM = """Ты финансовый коуч по поведенческим привычкам. Не обещай богатство и не давай инвестиционных рекомендаций. По описанию человека выдели: текущий денежный сценарий, одно ограничивающее убеждение, один ресурс, один конкретный шаг на 7 дней и один уточняющий вопрос. Только русский, без Markdown."""

REFLECTION_SYSTEM = """Ты бережный ведущий короткой вечерней рефлексии. Отрази главную эмоцию, назови то, что человек уже сделал хорошо, предложи одно маленькое действие на завтра и задай один точный вопрос. Не ставь диагнозы и не перегружай советами. 5–7 предложений, русский язык, без Markdown."""

NUMEROLOGY_SYSTEM = "Ты интерпретатор нумерологических расчётов. Используй только переданные числа, не пересчитывай их и не выдумывай новые. Пиши по-русски, практично, без фатальных обещаний и без Markdown."
TARO_SYSTEM = "Ты мудрый таролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
DREAMS_SYSTEM = "Ты мудрый толкователь снов с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
AURA_SYSTEM = "Ты мудрый энергетик с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
MATRIX_SYSTEM = "Ты интерпретатор Матрицы судьбы по системе 22 арканов. Используй только рассчитанные значения, объясняй их как инструмент саморефлексии, не как неизбежную судьбу. Без Markdown."
FORECAST_SYSTEM = "Ты мудрый прорицатель с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
COMPATIBILITY_SYSTEM = "Ты мудрый астропсихолог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
NATAL_SYSTEM = "Ты профессиональный астропсихолог. Интерпретируй только переданные рассчитанные положения планет, дома и аспекты. Не придумывай координаты и не обещай неизбежных событий. Пиши конкретно, понятно и без Markdown."
HOROSCOPE_SYSTEM = "Ты мудрый астролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
MONEY_CODE_SYSTEM = "Ты интерпретатор авторской нумерологической модели денежного кода. Используй только переданные расчёты, объясняй практические денежные привычки и риски без гарантий дохода. Без Markdown."
CHIROMANCY_SYSTEM = """Ты опытный хиромант. Внимательно изучи фотографию ладони и сделай детальный персональный разбор.

Структура разбора:
1. 🖐 Тип руки и общая энергетика — форма, размер, упругость кожи по впечатлению
2. 💫 Линия жизни — длина, глубина, изломы, разветвления — что это говорит об энергии и жизненных циклах
3. 🧠 Линия ума — наклон, длина, изгиб — подход к решениям, стиль мышления
4. ❤️ Линия сердца — начало, конец, прерывания — эмоциональность, отношения
5. 🌟 Дополнительные знаки — бугры, звёзды, острова, кресты, если видны
6. 💡 Главный вывод и совет — что эта ладонь говорит о пути человека прямо сейчас

Пиши конкретно о том, что видишь на ЭТОЙ ладони. Избегай общих фраз. Это символический и рефлексивный анализ — не медицинский и не предсказание судьбы.
Только на русском. Без Markdown. Объём: 250-350 слов."""
PHYSIO_SYSTEM = """Ты делаешь развлекательный и рефлексивный разбор визуального впечатления от фотографии лица.

Структура разбора:
1. ✨ Первое впечатление — какую энергию и настроение передаёт лицо
2. 👁 Взгляд и глаза — что выражают, какую внутреннюю силу показывают
3. 💫 Черты лица — общая гармония, выразительность, характерные особенности
4. 🌟 Энергетика и харизма — как человек вероятно проявляет себя в общении
5. 💡 Главное послание — ключевая сила этого человека по визуальному впечатлению

Используй формулировки "создаёт впечатление", "может говорить о", "визуально ощущается". Не определяй характер как факт, не делай выводов о здоровье, интеллекте, этничности, религии или ориентации.
Только на русском. Без Markdown. Объём: 200-280 слов."""
GRAPHO_SYSTEM = """Ты делаешь развлекательный рефлексивный разбор особенностей почерка.

Структура разбора:
1. ✍️ Общий характер почерка — наклон, размер, нажим, ритм
2. 💫 Что говорит наклон — правый, левый или прямой — об отношении к миру
3. 🔤 Особенности букв — округлость или угловатость, связность или разрывы
4. 🌟 Поля и строки — как человек организует пространство
5. 💡 Главный вывод — что этот почерк символически говорит о стиле человека

Формулируй как "может говорить о", "создаёт впечатление". Не выдавай выводы за факты. Не делай медицинских или психиатрических выводов.
Только на русском. Без Markdown. Объём: 200-260 слов."""
TARO_PHOTO_SYSTEM = "Ты опытный таролог. Смотришь на фото карт Таро и читаешь расклад. Пишешь только на русском. Никаких звёздочек и решёток."
COMPAT_PHOTO_SYSTEM = "Ты делаешь развлекательную рефлексию о визуальной динамике пары по фото. Не утверждай совместимость как факт и не определяй чувствительные черты. Дай вопросы для разговора и подчеркни, что отношения определяются поведением и общением. Только на русском."
LUNAR_SYSTEM = "Ты мудрый астролог и знаток лунного календаря. Пишешь тепло, конкретно, практично. Только на русском. Никаких звёздочек и решёток."

# ========== AI ФУНКЦИИ ==========
async def generate_text(system, prompt, model="gpt-4o-mini"):
    response = await openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=1500
    )
    return clean_display_text(response.choices[0].message.content)

async def generate_with_history(system, history, new_message):
    messages = [{"role": "system", "content": system}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": new_message})
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, max_tokens=1500
    )
    return clean_display_text(response.choices[0].message.content)

def _detect_image_media_type(image_bytes):
    """Определить MIME-тип изображения по сигнатуре файла."""
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


async def _openai_photo_analysis(system_prompt, image_bytes):
    """Основной фото-анализ через уже работающий OpenAI-клиент."""
    import base64

    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    media_type = _detect_image_media_type(image_bytes)
    last_error = None

    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                openai_client.chat.completions.create(
                    model=PHOTO_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Проанализируй прикреплённое изображение строго по системной инструкции. Ответь только на русском языке.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_base64}",
                                        "detail": "high",
                                    },
                                },
                            ],
                        },
                    ],
                    max_tokens=1500,
                ),
                timeout=120,
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("OpenAI вернул пустой результат фото-анализа")
            return clean_display_text(content)
        except Exception as exc:
            last_error = exc
            logging.warning("OpenAI photo attempt %s/2 failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(2)

    raise RuntimeError(f"OpenAI photo analysis failed: {last_error}")


async def _claude_photo_analysis(system_prompt, image_bytes):
    """Дополнительный провайдер; используется только при настроенном CLAUDE_KEY."""
    import base64

    if claude_client is None:
        raise RuntimeError("Claude photo provider is not configured")

    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    media_type = _detect_image_media_type(image_bytes)
    response = await asyncio.wait_for(
        asyncio.to_thread(
            claude_client.messages.create,
            model=CLAUDE_PHOTO_MODEL,
            max_tokens=1500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": system_prompt},
                    ],
                }
            ],
        ),
        timeout=120,
    )
    return clean_display_text(response.content[0].text)


async def generate_photo_analysis(system_prompt, image_bytes):
    """Фото-анализ с автоматическим резервным провайдером."""
    preferred = PHOTO_AI_PROVIDER if PHOTO_AI_PROVIDER in {"openai", "claude"} else "openai"
    providers = [preferred, "claude" if preferred == "openai" else "openai"]
    errors = []

    for provider in providers:
        if provider == "claude" and claude_client is None:
            continue
        try:
            if provider == "claude":
                return await _claude_photo_analysis(system_prompt, image_bytes)
            return await _openai_photo_analysis(system_prompt, image_bytes)
        except Exception as exc:
            errors.append(f"{provider}: {type(exc).__name__}: {exc}")
            logging.warning("Фото-анализ через %s не выполнен: %s", provider, exc)

    raise RuntimeError("; ".join(errors) or "Нет доступного провайдера фото-анализа")

# ========== ОПЛАТА ==========
async def create_payment(user_id, plan):
    subscriptions = {
        "aura_start": ("190.00", "Старт", 30),
        "aura_pro": ("390.00", "Про", 30),
        "aura_pro_year": ("2990.00", "Про на год", 365),
    }
    if plan in ONE_TIME_PRODUCTS:
        product = ONE_TIME_PRODUCTS[plan]
        amount = f"{product['amount']:.2f}"
        product_name = product["title"]
        days = 0
        purchase_type = "one_time"
        description = f"Aura Telegram Разовый разбор {product_name} — {user_id}"
        receipt_description = f"Разовый персональный разбор: {product_name}"
    else:
        amount, product_name, days = subscriptions.get(plan, subscriptions["aura_pro"])
        purchase_type = "subscription"
        description = f"AuraBot Telegram Тариф {product_name} — {user_id}"
        receipt_description = f"AuraBot Тариф {product_name} {days} дней"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": f"https://t.me/{BOT_USERNAME}"},
                "capture": True,
                "description": description,
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": receipt_description,
                    "quantity": "1.00",
                    "amount": {"value": amount, "currency": "RUB"},
                    "vat_code": 1, "payment_subject": "service", "payment_mode": "full_payment"
                }]},
                "metadata": {"user_id": user_id, "plan": plan, "purchase_type": purchase_type, "platform": "Telegram"}
            },
            headers={"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET)
        )
        r.raise_for_status()
        return r.json()

# ========== ФОНОВАЯ ПРОВЕРКА ОПЛАТЫ ==========
async def check_payments_loop():
    """Confirm YooKassa payments idempotently and activate access once."""
    while True:
        await asyncio.sleep(15)
        try:
            for payment_id, user_id, plan in get_pending_payments():
                try:
                    with db_connect() as conn:
                        already = conn.execute(
                            "SELECT 1 FROM payment_history WHERE payment_id=? AND status='succeeded'",
                            (payment_id,),
                        ).fetchone()
                    if already:
                        delete_pending_payment(payment_id)
                        continue

                    async with httpx.AsyncClient(timeout=30) as client:
                        r = await client.get(
                            f"https://api.yookassa.ru/v3/payments/{payment_id}",
                            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET),
                        )
                        r.raise_for_status()
                        payment = r.json()

                    status = payment.get("status")
                    if status == "succeeded":
                        if plan in ONE_TIME_PRODUCTS:
                            product = ONE_TIME_PRODUCTS[plan]
                            add_one_time_credit(user_id, product["feature"], 1)
                            with db_connect() as conn:
                                conn.execute(
                                    "INSERT OR IGNORE INTO payment_history "
                                    "(payment_id,user_id,plan,platform,status,processed_at) VALUES (?,?,?,?,?,?)",
                                    (payment_id, user_id, plan, "Telegram", "succeeded", datetime.now().isoformat()),
                                )
                            delete_pending_payment(payment_id)
                            user_name, user_username, _ = get_user_identity(user_id)
                            amount = product["amount"]
                            asyncio.create_task(asyncio.to_thread(
                                sheets_log_sale, user_id, user_name, product["title"], amount, None, PLATFORM_NAME
                            ))
                            await notify_owner(
                                "💰 Новая разовая продажа Aura", user_id, product["title"],
                                f"Сумма: {amount} ₽\nФункция: {product['feature']}\nPayment ID: {payment_id}"
                            )
                            await send_message(
                                user_id,
                                f"✅ Оплата прошла!\n\nТебе доступен один полный разбор «{product['title']}». "
                                "Нажми кнопку ниже — право спишется только после успешного результата.",
                                [[{"type":"callback", "text":f"✨ Начать: {product['title']}", "payload":product["feature"]}],
                                 [{"type":"callback", "text":"🔙 В меню", "payload":"back_menu"}]],
                            )
                            continue

                        activation_plan = "aura_pro" if plan == "aura_pro_year" else plan
                        activation_days = 365 if plan == "aura_pro_year" else 30

                        with db_connect() as conn:
                            previous_paid = conn.execute(
                                "SELECT COUNT(*) FROM payment_history WHERE user_id=? AND status='succeeded'",
                                (user_id,),
                            ).fetchone()[0]

                        set_subscription(user_id, activation_plan, activation_days)

                        with db_connect() as conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO payment_history "
                                "(payment_id,user_id,plan,platform,status,processed_at) VALUES (?,?,?,?,?,?)",
                                (payment_id, user_id, plan, "Telegram", "succeeded", datetime.now().isoformat()),
                            )

                        try:
                            log_event(user_id, "payment_succeeded", feature=plan, source="Telegram", value=payment_id)
                        except Exception:
                            pass

                        if previous_paid == 0:
                            with db_connect() as conn:
                                ref = conn.execute(
                                    "SELECT referrer_id FROM user_profiles WHERE user_id=?",
                                    (user_id,),
                                ).fetchone()
                            if ref and ref[0] and int(ref[0]) != int(user_id):
                                with db_connect() as conn:
                                    inserted = conn.execute(
                                        "INSERT OR IGNORE INTO referral_rewards "
                                        "(payment_id,referred_user_id,referrer_id,rewarded_at) VALUES (?,?,?,?)",
                                        (payment_id, user_id, int(ref[0]), datetime.now().isoformat()),
                                    ).rowcount
                                if inserted:
                                    set_subscription(int(ref[0]), "aura_pro", 30)
                                    try:
                                        await send_message(
                                            int(ref[0]),
                                            "🎁 Твой приглашённый друг оформил первую подписку. Тебе начислено 30 дней Аура Про!",
                                            main_menu_buttons(),
                                        )
                                    except Exception:
                                        pass

                        delete_pending_payment(payment_id)
                        plan_name = (
                            "🟢 Старт" if plan == "aura_start"
                            else "💜 Про на год" if plan == "aura_pro_year"
                            else "🔥 Про"
                        )
                        _, sub_end = get_subscription(user_id)
                        user_name, user_username, _ = get_user_identity(user_id)
                        amount = {"aura_start": 190, "aura_pro": 390, "aura_pro_year": 2990}.get(plan, 0)
                        asyncio.create_task(asyncio.to_thread(sheets_log_sale, user_id, user_name, plan, amount, sub_end, PLATFORM_NAME))
                        await notify_owner(
                            "💰 Новая продажа Aura", user_id, plan,
                            f"Сумма: {amount} ₽\nПодписка до: {sub_end.strftime('%d.%m.%Y') if sub_end else '—'}\nPayment ID: {payment_id}"
                        )

                        await send_message(
                            user_id,
                            f"✅ Оплата прошла!\n\nТариф {plan_name} активирован на {activation_days} дней.\n\nПользуйся на здоровье! 🔮",
                            main_menu_buttons(),
                        )
                    elif status == "canceled":
                        delete_pending_payment(payment_id)
                        await send_message(user_id, "❌ Платёж отменён. Попробуй снова.", main_menu_buttons())
                except Exception as e:
                    logging.error(f"Ошибка проверки платежа {payment_id}: {e}")
                    await notify_owner("⚠️ Ошибка проверки платежа", user_id, plan, f"{payment_id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка check_payments_loop: {e}")

# ========== УТРЕННИЕ РАССЫЛКИ ==========
async def daily_loop():
    while True:
        now = datetime.now(MOSCOW)
        next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))

        now = datetime.now(MOSCOW)
        today = now.strftime("%d.%m.%Y")
        phase = moon_phase_snapshot(now.astimezone(timezone.utc))
        if phase.get("angle") is None:
            phase_context = "Точная астрономическая фаза недоступна. Не называй конкретную фазу."
            heading = f"🌙 Мягкий настрой на {today}"
        else:
            phase_context = f"Фактическая фаза: {phase['name']}, угол лунной фазы {phase['angle']}°. Используй только эти данные и не выдумывай другие астрономические показатели."
            heading = f"🌙 Лунный настрой на {today}"
        try:
            lunar_text = await generate_text(
                LUNAR_SYSTEM,
                f"Сегодня {today}. {phase_context} Дай спокойный практический настрой: что полезно поддержать, чего избегать и одно действие дня. Без гарантий событий.",
            )
            with db_connect() as conn:
                users = conn.execute(
                    "SELECT u.user_id FROM users u LEFT JOIN user_profiles p ON p.user_id=u.user_id WHERE COALESCE(p.stopped,0)=0"
                ).fetchall()
            for (uid,) in users:
                try:
                    await send_message(uid, f"{heading}\n\n{clean_display_text(lunar_text)}")
                    await asyncio.sleep(0.04)
                except Exception as exc:
                    logging.warning("daily lunar Telegram %s: %s", uid, exc)
        except Exception as exc:
            logging.exception("Ошибка общего утреннего сообщения Telegram: %s", exc)
            await notify_owner("⚠️ Ошибка утренней рассылки", 0, "daily_lunar", str(exc))

        with db_connect() as conn:
            pro_users = conn.execute(
                """SELECT u.user_id, COALESCE(p.birth_date,u.birth_date)
                   FROM users u JOIN subscriptions s ON u.user_id=s.user_id
                   LEFT JOIN user_profiles p ON p.user_id=u.user_id
                   WHERE s.plan='aura_pro' AND s.sub_end>?
                     AND COALESCE(p.birth_date,u.birth_date,'')<>''
                     AND COALESCE(p.stopped,0)=0""",
                (datetime.now().isoformat(),),
            ).fetchall()
        for uid, birth in pro_users:
            try:
                values = calculate_numerology_data(parse_birth_date(birth), now) if parse_birth_date(birth) else {}
                text = await generate_text(
                    HOROSCOPE_SYSTEM,
                    f"Дата рождения: {birth}. Сегодня {today}. Расчёт личного дня: {values.get('personal_day','—')}. "
                    "Дай персональную подсказку: энергия, отношения, деньги, главное действие. Без фатальных обещаний.",
                )
                await send_message(uid, f"⭐️ Твоя личная подсказка на {today}\n\n{clean_display_text(text)}")
                await asyncio.sleep(0.04)
            except Exception as exc:
                logging.warning("personal daily Telegram %s: %s", uid, exc)

# ========== АНАЛИТИКА И ПРОФИЛЬ ==========
BOT_LINK = f"https://t.me/{BOT_USERNAME}"
MOSCOW = ZoneInfo("Europe/Moscow")
UPDATE_QUEUE = asyncio.Queue(maxsize=1000)

def db_connect():
    return sqlite3.connect(DB, timeout=10)

def log_event(user_id, event, feature="", source="", value=""):
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO analytics_events (user_id,event,feature,source,value,created_at) VALUES (?,?,?,?,?,?)",
                         (user_id,event,feature,source,str(value)[:500],datetime.now().isoformat()))
    except Exception as e:
        logging.error(f"analytics: {e}")

def mark_update(update_key):
    if not update_key:
        return True
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO processed_updates (update_key,processed_at) VALUES (?,?)", (update_key,datetime.now().isoformat()))
        return True
    except sqlite3.IntegrityError:
        return False

def save_profile(user_id, birth_date=None, source=None, referrer_id=None, stopped=None):
    with db_connect() as conn:
        conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
        if birth_date: conn.execute("UPDATE user_profiles SET birth_date=? WHERE user_id=?", (birth_date,user_id))
        if source: conn.execute("UPDATE user_profiles SET source=? WHERE user_id=?", (source,user_id))
        if referrer_id and referrer_id != user_id: conn.execute("UPDATE user_profiles SET referrer_id=COALESCE(referrer_id,?) WHERE user_id=?", (referrer_id,user_id))
        if stopped is not None: conn.execute("UPDATE user_profiles SET stopped=? WHERE user_id=?", (int(stopped),user_id))

def extract_birth_date(text):
    m=re.search(r"\b(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/]((?:19|20)\d{2})\b", text or "")
    if not m: return None
    try:
        d=datetime(int(m.group(3)),int(m.group(2)),int(m.group(1)))
        return d.strftime("%d.%m.%Y")
    except ValueError: return None

def deep_link(payload):
    return f"{BOT_LINK}?start={payload[:128]}"

def get_profile_text(user_id):
    plan,end=get_subscription(user_id); lim=get_limits(user_id)
    with db_connect() as conn:
        row=conn.execute("SELECT birth_date,source FROM user_profiles WHERE user_id=?",(user_id,)).fetchone()
    birth=row[0] if row else ""
    plan_name={"aura_start":"🟢 Аура Старт","aura_pro":"🔥 Аура Про"}.get(plan,"Бесплатный")
    until=f" до {end.strftime('%d.%m.%Y')}" if end else ""
    return (f"👤 Твой профиль\n\nТариф: {plan_name}{until}\nДата рождения: {birth or 'не указана'}\n"
            f"Бесплатных разборов использовано: {lim['requests']} из {FREE_REQUESTS}\n"
            f"Сообщений психологу: {lim['psycho']}\n\nДата рождения сохраняется после первого персонального разбора.")

def referral_buttons(user_id):
    link=deep_link(f"ref_{user_id}")
    share=f"https://t.me/share/url?url={quote(link)}&text={quote('Попробуй AuraBot — личные разборы, Таро и AI-психолог')}"
    return [[{"type":"link","text":"📨 Поделиться","url":share}], [{"type":"callback","text":"🔙 В меню","payload":"back_menu"}]]



def get_user_identity(user_id):
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT first_name, username, step FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return row[0] or "—", row[1] or "—", row[2] or "idle"
    except Exception:
        pass
    return "—", "—", "idle"

def plan_label(user_id):
    plan, end = get_subscription(user_id)
    label = {"aura_start": "Старт", "aura_pro": "Про"}.get(plan, "Бесплатный")
    return label + (f" до {end.strftime('%d.%m.%Y')}" if end else "")

async def notify_owner(title, user_id=0, feature="", details=""):
    try:
        name, username, step = get_user_identity(user_id) if user_id else ("—", "—", "—")
        text = (f"{title}\n\nПлатформа: {PLATFORM_NAME}\nПользователь: {name}\nUsername: {username}\n"
                f"ID: {user_id or '—'}\nТариф: {plan_label(user_id) if user_id else '—'}\n"
                f"Функция/шаг: {feature or step}\nВремя: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        if details:
            text += f"\n\nДетали:\n{details[:2500]}"
        await send_message(OWNER_ID, text)
    except Exception as e:
        logging.error(f"Не удалось уведомить владельца: {e}")

async def open_support(chat_id, user_id):
    log_event(user_id, "support_opened")
    set_step(user_id, "support")
    await send_message(chat_id,
        "💬 Поддержка Aura\n\nОпиши, что произошло: какая функция не сработала, что ты нажимала и что увидела. "
        "Сообщение сразу придёт владельцу проекта.",
        back_button())

async def process_support_message(chat_id, user_id, text):
    log_event(user_id, "support_sent")
    set_step(user_id, "idle")
    await notify_owner("🆘 Новое обращение в поддержку Aura", user_id, "support", text)
    await send_message(chat_id,
        "✅ Сообщение отправлено. Мы проверим проблему и постараемся отреагировать как можно быстрее.",
        main_menu_buttons())




FEATURE_STEPS = {
    "numerology", "matrix", "taro", "dreams", "aura", "forecast_period",
    "annual_forecast", "compatibility", "natal", "horoscope", "money_code",
    "money_scenario", "evening_reflection",
}


async def build_feature_prompt(step, text):
    birth_dt = parse_birth_date(text)
    if step in {"numerology", "matrix", "forecast_period", "annual_forecast", "money_code"} and not birth_dt:
        raise ValueError("Не смогла распознать дату рождения. Напиши её в формате ДД.ММ.ГГГГ")
    if step == "numerology":
        data = calculate_numerology_data(birth_dt)
        return NUMEROLOGY_SYSTEM, (
            f"Дата рождения: {birth_dt.strftime('%d.%m.%Y')}\n"
            f"Рассчитанные значения: жизненный путь {data['life_path']}; день рождения {data['birthday']}; "
            f"отношение к миру {data['attitude']}; личный год {data['personal_year']}; личный месяц {data['personal_month']}.\n\n"
            "Дай структурированный разбор: сильные стороны, сложности, отношения, реализация и 3 практических шага."
        ), birth_dt
    if step == "matrix":
        data = calculate_matrix22_data(birth_dt)
        values = ", ".join(f"{key}={value}" for key, value in data.items())
        return MATRIX_SYSTEM, (
            f"Дата рождения: {birth_dt.strftime('%d.%m.%Y')}\nРассчитанная Матрица 22 арканов: {values}.\n\n"
            "Интерпретируй предназначение, таланты, денежную линию, отношения, центральную задачу и дай практический план."
        ), birth_dt
    if step == "money_code":
        match = re.search(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/](?:19|20)\d{2}\b", text or "")
        full_name = (text[:match.start()] if match else "").strip(" ,;:-")
        if len(normalize_name(full_name)) < 2:
            raise ValueError("Напиши полное имя и дату рождения, например: Мария Иванова 15.03.1990")
        data = calculate_money_code_data(full_name, birth_dt)
        return MONEY_CODE_SYSTEM, (
            f"Имя: {full_name}\nДата рождения: {birth_dt.strftime('%d.%m.%Y')}\n"
            f"Рассчитанный денежный код: {data['money_code']}; число имени {data['name_number']}; "
            f"жизненный путь {data['life_path']}; личный год {data['personal_year']}.\n\n"
            "Объясни элементы, денежные привычки, риски и составь практику на 14 дней. Не обещай доход."
        ), birth_dt
    if step in {"forecast_period", "annual_forecast"}:
        data = calculate_numerology_data(birth_dt)
        if step == "forecast_period":
            period = "месяц" if "месяц" in (text or "").lower() else "неделя"
            return FORECAST_SYSTEM, (
                f"Дата рождения: {birth_dt.strftime('%d.%m.%Y')}; период: {period}. "
                f"Личный год {data['personal_year']}, личный месяц {data['personal_month']}, личный день {data['personal_day']}.\n\n"
                "Составь краткий практичный прогноз: работа, отношения, эмоциональный фон, 3 действия и одно предостережение."
            ), birth_dt
        return FORECAST_SYSTEM, (
            f"Дата рождения: {birth_dt.strftime('%d.%m.%Y')}. Личный год {data['personal_year']}; текущий личный месяц {data['personal_month']}.\n\n"
            "Составь полный прогноз на 12 месяцев: тема года, деньги и работа, отношения, внутреннее состояние, "
            "рекомендации по кварталам, сильные периоды, периоды осторожности и итоговый план."
        ), birth_dt
    if step == "natal":
        chart = await calculate_natal_chart(text)
        parsed = parse_natal_input(text)
        return NATAL_SYSTEM, (
            f"Исходные данные: {text}\n\nРассчитанная карта:\n{format_natal_chart_data(chart)}\n\n"
            "Сделай интерпретацию: ядро личности, эмоции, мышление, отношения, энергия действий, реализация, "
            "ключевые аспекты и 5 практических рекомендаций. Укажи равнодомную систему домов."
        ), parsed[0] if parsed else None
    simple = {
        "taro": (TARO_SYSTEM, f"Вопрос: {text}\n\nСделай символический расклад из 3 карт: контекст, что важно увидеть, возможный следующий шаг. Не выдавай будущее за факт."),
        "dreams": (DREAMS_SYSTEM, f"Сон: {text}\n\nДай психологическое и символическое толкование, выдели эмоции, ассоциации и вопросы для саморефлексии."),
        "aura": (AURA_SYSTEM, f"Дата рождения или описание состояния: {text}\n\nДай символический рефлексивный разбор энергии, сильных сторон, уязвимостей и практику восстановления."),
        "compatibility": (COMPATIBILITY_SYSTEM, f"Данные: {text}\n\nРазбери вероятные сценарии общения: сильные стороны, конфликтные зоны и 5 вопросов для пары."),
        "horoscope": (HOROSCOPE_SYSTEM, f"Данные: {text}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. Дай практичную подсказку без обещаний событий."),
        "money_scenario": (MONEY_SCENARIO_SYSTEM, f"Описание денежной ситуации: {text}"),
        "evening_reflection": (REFLECTION_SYSTEM, f"Ответ человека для вечерней рефлексии: {text}"),
    }
    if step not in simple:
        raise ValueError("Не удалось определить выбранный разбор")
    system, prompt = simple[step]
    return system, prompt, birth_dt


# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
WELCOME_TEXT = """✨ {name}, добро пожаловать в Aura.

Здесь можно спокойно разобраться в том, что сейчас действительно важно: отношениях, деньгах, внутреннем состоянии или направлении жизни.

Что доступно:
• личные разборы под твою ситуацию
• Таро, нумерология, сны и совместимость
• AI-психолог с памятью диалога
• дневник и вечерняя рефлексия
• фото-разборы и глубокие персональные продукты

🎁 Бесплатно: 5 личных разборов и 15 сообщений психологу
🌙 Каждый день — мягкие персональные подсказки

Выбери раздел ниже. Я проведу тебя дальше шаг за шагом."""

async def handle_limit_msg(chat_id, access):
    if access == "limit_free":
        await send_message(chat_id, "🚫 Бесплатные разборы закончились (5 из 5).\n\nОформи подписку:", upgrade_buttons())
    elif access == "limit_psycho_free":
        await send_message(chat_id, "🚫 Бесплатные сообщения психологу закончились.\n\nОформи подписку:", upgrade_buttons())
    elif access == "limit_psycho_start":
        await send_message(chat_id, "🚫 Лимит психолога на Старте (100 сообщений).\n\nПерейди на Про:", upgrade_buttons("start"))
    elif access == "limit_photo":
        await send_message(chat_id, "🚫 Лимит фото-анализов на Старте (5 раз).\n\nПерейди на Про:", upgrade_buttons("start"))
    elif access == "diary_blocked":
        await send_message(chat_id, "📔 Постоянный личный дневник доступен с тарифа Старт.\n\nМожно сначала бесплатно пройти короткую вечернюю рефлексию.", [
            [{"type":"callback","text":"📝 Бесплатная рефлексия","payload":"evening_reflection"}],
            [{"type":"callback","text":"🟢 Старт · 190 ₽","payload":"pay_start"}],
            [{"type":"callback","text":"🔥 Про · 390 ₽","payload":"pay_pro"}],
            [{"type":"callback","text":"🔙 В меню","payload":"back_menu"}],
        ])
    elif access == "start_block":
        await send_message(chat_id, "🔒 Эта функция доступна только на тарифе 🔥 Про.\n\n390 руб/мес:", upgrade_buttons("start"))


def looks_like_crisis(text):
    t=(text or "").lower()
    markers=("не хочу жить","покончить с собой","суицид","убить себя","причинить себе вред","навредить себе","убить его","убить её")
    return any(x in t for x in markers)

async def process_command(chat_id, user_id, text, username="", first_name=""):
    get_user(user_id, username, first_name)
    name = first_name or "друг"
    log_event(user_id, "message", value=text[:100])

    if text in ("/start", "start"):
        set_step(user_id, "idle")
        plan, _ = get_subscription(user_id)
        asyncio.create_task(asyncio.to_thread(sheets_log_visit, user_id, first_name, username, plan))
        await send_message(chat_id, WELCOME_TEXT.format(name=name), main_menu_buttons())
        return

    user = get_user(user_id)
    step = user.get("step", "")

    # Обработка шагов
    if step == "support":
        await process_support_message(chat_id, user_id, text)
        return

    if step == "my_day_birth":
        access = await check_access(user_id, "general")
        if access not in ("ok", "pro"):
            set_step(user_id, "idle")
            await handle_limit_msg(chat_id, access)
            return
        birth = extract_birth_date(text)
        if not birth:
            await send_message(chat_id, "Не смогла распознать дату. Напиши, например: 15.03.1990", back_button())
            return
        save_profile(user_id, birth_date=birth)
        with db_connect() as conn:
            conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth, user_id))
        set_step(user_id, "idle")
        birth_dt = parse_birth_date(birth)
        try:
            values = calculate_numerology_data(birth_dt, datetime.now(MOSCOW)) if birth_dt else {}
            result = await generate_text(
                HOROSCOPE_SYSTEM,
                f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. "
                f"Расчёт личного дня: {values.get('personal_day', '—')}. Дай персональную подсказку: "
                "энергия дня, отношения, деньги, главное действие и вечерняя практика. Не обещай неизбежных событий.",
            )
        except Exception as exc:
            await notify_owner("⚠️ Ошибка функции Мой день", user_id, "my_day", str(exc))
            await send_message(chat_id, "⚠️ Не удалось подготовить подсказку. Бесплатный запрос не списан. Попробуй ещё раз.", back_button())
            return
        if access == "ok":
            increment_limit(user_id, "requests")
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, first_name, username))
        offer_note, offer_buttons = build_result_offer(user_id, "my_day")
        await send_message(chat_id, "🌟 Твой день\n\n" + clean_display_text(result) + offer_note, offer_buttons)
        return

    if step == "review":
        set_step(user_id, "idle")
        save_review(user_id, username, first_name, text)
        asyncio.create_task(asyncio.to_thread(sheets_log_review, user_id, first_name, username, text))
        await send_message(chat_id, "⭐️ Спасибо за отзыв. Это помогает нам делать Aura ещё лучше. 💜", main_menu_buttons())
        return

    if step == "diary":
        access = await check_access(user_id, "diary")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        history = get_diary_history(user_id)
        history_ctx = ""
        if history:
            history_ctx = "\n\nПредыдущие записи:"
            for entry, resp, date in history[-3:]:
                history_ctx += f"\n[{date[:10]}] {entry[:80]}"
        response = await generate_text(DIARY_SYSTEM, f"Запись в дневник: {text}{history_ctx}")
        add_diary_entry(user_id, text, response)
        await send_message(chat_id, response, back_button())
        return

    if step == "psycho":
        if looks_like_crisis(text):
            await send_message(chat_id, "Мне очень жаль, что тебе сейчас настолько тяжело. Я не заменяю экстренную помощь. Если есть риск, что ты можешь навредить себе или другому человеку, прямо сейчас позвони 112 или обратись к человеку рядом, которому доверяешь. Постарайся не оставаться в одиночестве и убери подальше всё, чем можно причинить вред. Напиши одним словом: ты сейчас в непосредственной опасности — да или нет?", psycho_buttons())
            log_event(user_id,"crisis_message"); return
        access = await check_access(user_id, "psycho")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        history = get_psycho_history(user_id)
        response = await generate_with_history(PSYCHO_SYSTEM, history, text)
        add_psycho_message(user_id, "user", text)
        add_psycho_message(user_id, "assistant", response)
        if access == "ok":
            increment_limit(user_id, "psycho_messages")
        asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, first_name, username))
        await send_message(chat_id, response, psycho_buttons())
        return

    if step in FEATURE_STEPS:
        feature = step if step in FEATURE_TO_PRODUCT else "general"
        plan_now, _ = get_subscription(user_id)
        use_one_time = feature in FEATURE_TO_PRODUCT and plan_now != "aura_pro" and get_one_time_credit(user_id, feature) > 0
        access = "one_time" if use_one_time else await check_access(user_id, feature)
        if access not in ("ok", "pro", "one_time"):
            if feature in FEATURE_TO_PRODUCT:
                await send_one_time_offer(chat_id, feature)
                return
            await handle_limit_msg(chat_id, access)
            return
        await send_message(chat_id, "⏳ Собираю твой личный разбор...")
        try:
            system, prompt, birth_dt = await build_feature_prompt(step, text)
            result = await generate_text(system, prompt)
        except ValueError as exc:
            await send_message(chat_id, f"Не получилось начать разбор. {exc}", back_button())
            return
        except Exception as exc:
            logging.exception("Ошибка персонального разбора %s: %s", step, exc)
            await notify_owner("⚠️ Ошибка персонального разбора", user_id, step, str(exc))
            await send_message(chat_id, "⚠️ Не удалось завершить разбор. Оплаченное право и бесплатный запрос не списаны. Попробуй ещё раз или напиши в поддержку.", back_button())
            return
        set_step(user_id, "idle")
        if birth_dt:
            birth_value = birth_dt.strftime("%d.%m.%Y")
            try:
                with db_connect() as conn:
                    conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
                    conn.execute("UPDATE user_profiles SET birth_date=? WHERE user_id=?", (birth_value, user_id))
                    conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth_value, user_id))
            except Exception:
                pass
        if access == "ok":
            increment_limit(user_id, "requests")
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, first_name, username))
        elif access == "one_time":
            if not consume_one_time_credit(user_id, feature):
                await notify_owner("⚠️ Ошибка списания разового разбора", user_id, feature, "Результат создан, но кредит не найден")
        if step == "evening_reflection":
            offer_note = "\n\n📔 Хочешь сохранять записи и возвращаться к ним? Постоянный дневник доступен на тарифе Старт."
            offer_buttons = [
                [{"type":"callback","text":"✅ Открыть дневник — 190 ₽","payload":"pay_start"}],
                [{"type":"callback","text":"🔥 Все возможности — 390 ₽","payload":"pay_pro"}],
                [{"type":"callback","text":"🔙 В меню","payload":"back_menu"}],
            ]
        else:
            offer_note, offer_buttons = build_result_offer(user_id, step if step == "money_scenario" else feature, used_one_time=use_one_time)
        await send_message(chat_id, result + offer_note, offer_buttons)
        return

    await send_message(chat_id, "Выбери раздел ниже — я проведу тебя дальше ✨", main_menu_buttons())

async def process_callback(chat_id, user_id, payload, first_name=""):
    get_user(user_id, "", first_name)

    log_event(user_id, "callback", feature=payload)
    if payload.startswith("cat_"):
        cat=payload.split("_",1)[1]
        titles={"situation":"🔮 Разобрать ситуацию","love":"❤️ Отношения","money":"💰 Деньги и предназначение","self":"✨ Узнать себя"}
        await send_message(chat_id, titles.get(cat,"Выбери направление"), category_buttons(cat)); return
    if payload == "profile":
        await send_message(chat_id, get_profile_text(user_id), back_button()); return
    if payload == "referral":
        await send_message(chat_id, "🎁 Пригласи близкого человека\n\nОн получит 3 дополнительных бесплатных разбора, а после его первой оплаты тебе начислят 30 дней Аура Про.\n\nНажми кнопку и отправь приглашение.", referral_buttons(user_id)); return
    if payload == "my_day":
        access = await check_access(user_id, "general")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        with db_connect() as conn:
            row = conn.execute("SELECT birth_date FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        birth = row[0] if row and row[0] else ""
        if not birth:
            set_step(user_id, "my_day_birth")
            await send_message(chat_id, "🌟 Мой день\n\nВведи дату рождения в формате ДД.ММ.ГГГГ — я сохраню её и подготовлю личную подсказку.", back_button())
            return
        birth_dt = parse_birth_date(birth)
        await send_message(chat_id, "⏳ Собираю личную подсказку...")
        try:
            values = calculate_numerology_data(birth_dt, datetime.now(MOSCOW)) if birth_dt else {}
            result = await generate_text(
                HOROSCOPE_SYSTEM,
                f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. "
                f"Расчёт личного дня: {values.get('personal_day', '—')}. Дай персональную подсказку: "
                "энергия дня, отношения, деньги, главное действие и короткая вечерняя практика. Не обещай неизбежных событий.",
            )
        except Exception as exc:
            await notify_owner("⚠️ Ошибка функции Мой день", user_id, "my_day", str(exc))
            await send_message(chat_id, "⚠️ Не удалось подготовить подсказку. Бесплатный запрос не списан.", back_button())
            return
        if access == "ok":
            increment_limit(user_id, "requests")
            name, uname, _ = get_user_identity(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, name, uname if uname != "—" else ""))
        offer_note, offer_buttons = build_result_offer(user_id, "my_day")
        await send_message(chat_id, "🌟 Твой день\n\n" + clean_display_text(result) + offer_note, offer_buttons)
        return

    if payload == "noop":
        return

    if payload == "back_menu":
        set_step(user_id, "idle")
        name = first_name or "друг"
        await send_message(chat_id, WELCOME_TEXT.format(name=name), main_menu_buttons())
        return

    if payload == "psycho_new":
        clear_psycho_history(user_id)
        set_step(user_id, "psycho")
        await send_message(chat_id, "🧠 Новый разговор\n\nНачинаем заново. Напиши, с чем хочешь разобраться сейчас.", psycho_buttons())
        return

    if payload == "tariffs":
        plan, sub_end = get_subscription(user_id)
        current = ""
        if plan == "aura_start":
            current = f"\n\n✅ Твой тариф: 🟢 Старт (до {sub_end.strftime('%d.%m.%Y')})"
        elif plan == "aura_pro":
            current = f"\n\n✅ Твой тариф: 🔥 Про (до {sub_end.strftime('%d.%m.%Y')})"
        await send_message(chat_id,
            f"💎 Тарифы Aura\n\n"
            f"🟢 Старт — 190 ₽ / 30 дней\n"
            f"• базовые разборы без ограничений\n"
            f"• дневник\n"
            f"• хиромантия, графология и фото-разборы — по условиям тарифа\n"
            f"• психолог — до 100 сообщений\n\n"
            f"🔥 Про — 390 ₽ / 30 дней\n"
            f"• полный доступ ко всем функциям\n"
            f"• Матрица судьбы, Прогноз и Натальная карта\n"
            f"• Денежный код и глубокие фото-разборы\n"
            f"• персональная ежедневная подсказка\n"
            f"• психолог без ограничений\n\n"
            f"💜 Про на год — 2 990 ₽ / 365 дней\n\n"
            f"✨ Разовые разборы без подписки\n"
            f"Денежный код — 199 ₽\n"
            f"Матрица судьбы — 249 ₽\n"
            f"Прогноз на год — 299 ₽\n"
            f"Натальная карта — 349 ₽\n\n"
            f"🎁 Бесплатно: 5 разборов и 15 сообщений психологу{current}",
            [
                [{"type": "callback", "text": "🟢 Старт · 190 ₽", "payload": "pay_start"}],
                [{"type": "callback", "text": "🔥 Про · 390 ₽", "payload": "pay_pro"}],
                [{"type": "callback", "text": "💜 Про на год · 2 990 ₽", "payload": "pay_year"}],
                [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
            ]
        )
        return

    if payload in ("pay_start", "pay_pro", "pay_year") or payload.startswith("pay_once_"):
        plan = {"pay_start":"aura_start", "pay_pro":"aura_pro", "pay_year":"aura_pro_year"}.get(payload)
        if not plan:
            plan = payload[4:]
        if plan not in ("aura_start", "aura_pro", "aura_pro_year") and plan not in ONE_TIME_PRODUCTS:
            await send_message(chat_id, "Не удалось определить покупку. Открой меню и попробуй снова.", back_button())
            return
        try:
            payment = await create_payment(user_id, plan)
            pay_url = payment.get("confirmation", {}).get("confirmation_url", "")
            payment_id = payment.get("id", "")
            if pay_url and payment_id:
                save_pending_payment(payment_id, user_id, plan)
                log_event(user_id, "payment_created", feature=plan, value=payment_id)
                if plan in ONE_TIME_PRODUCTS:
                    product = ONE_TIME_PRODUCTS[plan]
                    plan_name = f"{product['title']} — {product['amount']} ₽"
                    payment_note = "Это разовая покупка без автопродления. После оплаты будет доступен один полный разбор."
                else:
                    plan_name = {"aura_start":"Старт 190 ₽", "aura_pro":"Про 390 ₽", "aura_pro_year":"Про на год 2 990 ₽"}[plan]
                    payment_note = "Подписка активируется автоматически после подтверждения оплаты."
                await send_message(chat_id,
                    f"💳 {plan_name}\n\nНажми кнопку для оплаты.\n{payment_note}",
                    [[{"type": "link", "text": f"💳 Оплатить", "url": pay_url}],
                     [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]]
                )
            else:
                await send_message(chat_id, f"❌ Ошибка при создании платежа. Обратись в поддержку: {SUPPORT_URL}", back_button())
        except Exception as e:
            logging.error(f"Ошибка платежа: {e}")
            await notify_owner("⚠️ Ошибка создания платежа", user_id, plan, str(e))
            await send_message(chat_id, f"❌ Ошибка платежа. Обратись в поддержку: {SUPPORT_URL}", back_button())
        return

    if payload == "support":
        await open_support(chat_id, user_id)
        return

    if payload == "review":
        set_step(user_id, "review")
        await send_message(chat_id, "⭐️ Оставить отзыв\n\nНапиши что думаешь о боте — что понравилось, что улучшить:", back_button())
        return

    # Кнопки меню
    step_buttons = {
        "numerology": "Введи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        "matrix": "Введи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        "taro": "Напиши свой вопрос или опиши ситуацию:",
        "dreams": "Опиши свой сон подробно — что происходило, кто был, какие образы:",
        "aura": "Введи дату рождения в формате ДД.ММ.ГГГГ:",
        "forecast_period": "Введи дату рождения и период:\nНапример: 15.03.1990, месяц",
        "annual_forecast": "Введи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        "compatibility": "Введи данные обоих людей:\nМария 15.03.1990 Александр 22.07.1988",
        "natal": "Введи дату, время и место рождения:\n15.03.1990 14:30 Москва",
        "horoscope": "Напиши свой знак зодиака:\nНапример: Телец, Скорпион, Водолей",
        "money_code": "Введи своё полное имя и дату рождения:\nМария Иванова 15.03.1990",
        "money_scenario": "Ответь одним сообщением:\n1. Что сейчас происходит с деньгами?\n2. Какой результат ты хочешь?\n3. Что, по твоему ощущению, мешает?",
        "evening_reflection": "Напиши одним сообщением:\n• что сегодня забрало силы;\n• что получилось;\n• что ты не хочешь нести в завтра.",
    }

    pro_features = ("matrix", "annual_forecast", "natal", "money_code", "taro_photo", "compat_photo")
    photo_features = ("aura_photo", "chiromancy", "physio", "grapho")

    if payload == "psycho":
        access = await check_access(user_id, "psycho")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        set_step(user_id, "psycho")
        history = get_psycho_history(user_id)
        if history:
            await send_message(chat_id, "🧠 AI-Психолог\n\nПродолжим с того места, где остановились. Напиши всё как есть — я помогу разложить ситуацию по шагам.", psycho_buttons())
        else:
            await send_message(chat_id, "🧠 AI-Психолог\n\nНапиши, что сейчас больше всего тревожит, ранит или не даёт покоя. Начнём с этого.", psycho_buttons())
        return

    if payload == "diary":
        access = await check_access(user_id, "diary")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        set_step(user_id, "diary")
        await send_message(chat_id,
            "📔 Личный дневник\n\n"
            "Тихое личное пространство для мыслей, переживаний и наблюдений.\n\n"
            "Напиши, как прошёл день, что осталось в голове или на сердце.\n"
            "Я бережно откликнусь и задам один мягкий вопрос.\n\n"
            "Если нужен совет и диалог — открой 🧠 Психолог.\n\n"
            "📔 Доступно на тарифах Старт и Про.",
            back_button()
        )
        return

    if payload in photo_features:
        access = await check_access(user_id, payload)
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        set_step(user_id, payload)
        photo_msgs = {
            "aura_photo": "🔮 Символический анализ ауры\n\nПришли портретное фото при хорошем естественном освещении, без фильтров и сильной обработки.\n\nЯ подготовлю мягкую рефлексивную интерпретацию.",
            "chiromancy": "🖐 Хиромантия\n\nПришли чёткое фото ладони:\n• хороший свет\n• ладонь раскрыта вверх\n• пальцы расслаблены\n• лучше правая рука\n\nПосле этого я подготовлю личный символический разбор линий и формы руки.",
            "physio": "😊 Впечатление по фото\n\nПришли фото лица:\n• анфас, взгляд прямо в камеру\n• хороший свет\n• без фильтров\n\nЯ подготовлю аккуратную рефлексию о визуальном впечатлении.",
            "grapho": "✍️ Графология\n\nНапиши от руки 5–7 предложений и пришли фото листа:\n• пиши как обычно\n• хороший свет\n• текст должен быть читаемым\n\nПосле этого я разберу особенности почерка.",
        }
        await send_message(chat_id, photo_msgs[payload], back_button())
        return

    if payload == "taro_photo":
        plan, _ = get_subscription(user_id)
        if plan != "aura_pro":
            await send_message(chat_id, "🔒 Таро по фото доступно только на тарифе Про.\n\n390 руб/мес:", upgrade_buttons("start"))
            return
        set_step(user_id, "taro_photo")
        await send_message(chat_id, "🃏 Таро по фото карт\n\nРазложи карты так, чтобы каждая была хорошо видна, и отправь фото.\n\nЯ определю карты и соберу связный разбор расклада.", back_button())
        return

    if payload == "compat_photo":
        plan, _ = get_subscription(user_id)
        if plan != "aura_pro":
            await send_message(chat_id, "🔒 Совместимость по фото только на тарифе Про.\n\n390 руб/мес:", upgrade_buttons("start"))
            return
        set_step(user_id, "compat_photo")
        await send_message(chat_id, "👫 Совместимость по фото\n\nПришли фото, на котором хорошо видны оба человека.\n\nЯ подготовлю мягкую рефлексию о динамике и сильных сторонах пары.", back_button())
        return

    if payload in pro_features and payload not in ("taro_photo", "compat_photo"):
        access = await check_access(user_id, payload)
        if access not in ("ok", "pro"):
            if payload in FEATURE_TO_PRODUCT and get_one_time_credit(user_id, payload) > 0:
                access = "one_time"
            elif payload in FEATURE_TO_PRODUCT:
                await send_one_time_offer(chat_id, payload)
                return
            else:
                await handle_limit_msg(chat_id, access)
                return

    if payload in step_buttons:
        set_step(user_id, payload)
        feature_names = {
            "numerology": "🔢 Нумерология", "matrix": "🌌 Матрица судьбы",
            "taro": "🃏 Таро", "dreams": "💤 Толкование снов",
            "aura": "🌈 Аура", "forecast_period": "📅 Прогноз на период",
            "annual_forecast": "📊 Прогноз на год",
            "compatibility": "❤️ Совместимость", "natal": "♈ Натальная карта",
            "horoscope": "🌟 Гороскоп", "money_code": "💰 Денежный код",
            "money_scenario": "💬 Денежный сценарий", "evening_reflection": "📝 Вечерняя рефлексия",
        }
        name = feature_names.get(payload, payload)
        await send_message(chat_id, f"{name}\n\n{step_buttons[payload]}", back_button())
        return

    await send_message(chat_id, "Выбери раздел ниже — я проведу тебя дальше ✨", main_menu_buttons())

async def process_photo(chat_id, user_id, photo_url):
    user = get_user(user_id)
    step = user.get("step", "")

    photo_steps = {
        "aura_photo": (AURA_SYSTEM + " Анализ символический и рефлексивный; не утверждай, что измеряешь реальную энергию.", "aura_photo", "requests"),
        "chiromancy": (CHIROMANCY_SYSTEM, "chiromancy", "photo_chiromancy"),
        "physio": (PHYSIO_SYSTEM, "physio", "photo_physio"),
        "grapho": (GRAPHO_SYSTEM, "grapho", "photo_grapho"),
        "taro_photo": (TARO_PHOTO_SYSTEM, "taro_photo", "requests"),
        "compat_photo": (COMPAT_PHOTO_SYSTEM, "taro_photo", "requests"),
    }

    if step not in photo_steps:
        await send_message(chat_id, "Сначала выбери нужный фото-разбор, затем отправь фото ✨", main_menu_buttons())
        return

    system, feature, limit_field = photo_steps[step]
    access = await check_access(user_id, feature)
    if access not in ("ok", "pro"):
        await handle_limit_msg(chat_id, access)
        return

    await send_message(chat_id, "⏳ Внимательно изучаю фото и собираю разбор...")
    try:
        image_bytes = await get_photo(photo_url)
        if not image_bytes:
            await send_message(chat_id, "❌ Не получилось загрузить фото. Попробуй ещё раз — я проверю заново.", back_button())
            return
        result = await generate_photo_analysis(system, image_bytes)
        set_step(user_id, "idle")
        if access == "ok":
            increment_limit(user_id, "requests" if limit_field == "requests" else limit_field)
            name, uname, _ = get_user_identity(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, name, uname if uname != "—" else ""))
        await send_message(chat_id, result, back_button())
    except Exception as e:
        logging.error(f"Ошибка фото-анализа: {e}")
        await notify_owner("⚠️ Ошибка фото-анализа", user_id, step, str(e))
        await send_message(chat_id, "⚠️ Фото сейчас не удалось обработать. Лимит не списан — отправь снимок ещё раз через минуту или напиши в поддержку.", back_button())

# ========== TELEGRAM HANDLERS ==========
async def process_start_payload(message: Message):
    payload = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        payload = parts[1].strip()

    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "друг"

    get_user(user_id, username, first_name)
    set_step(user_id, "idle")
    plan, _ = get_subscription(user_id)
    asyncio.create_task(asyncio.to_thread(
        sheets_log_visit, user_id, first_name, username, plan
    ))

    if payload:
        log_event(user_id, "bot_started", source=payload)
        save_profile(user_id, source=payload, stopped=False)

        if payload.startswith("ref_"):
            try:
                referrer_id = int(payload.split("_", 1)[1])
                if referrer_id != user_id:
                    with db_connect() as conn:
                        row = conn.execute(
                            "SELECT referrer_id FROM user_profiles WHERE user_id=?",
                            (user_id,)
                        ).fetchone()
                        if row and not row[0]:
                            conn.execute(
                                "UPDATE user_profiles SET referrer_id=? WHERE user_id=?",
                                (referrer_id, user_id)
                            )
            except Exception:
                logging.exception("Ошибка обработки реферала")

        if payload == "channel_intro":
            log_event(user_id, "channel_entry", feature="intro_choice", source=payload)
            await send_message(
                message.chat.id,
                "🎁 Начнём с бесплатного личного разбора.\n\nВыбери, что сейчас волнует тебя сильнее всего:",
                [
                    [{"type":"callback","text":"🔮 Разобрать ситуацию","payload":"cat_situation"}],
                    [{"type":"callback","text":"❤️ Отношения","payload":"cat_love"},
                     {"type":"callback","text":"💰 Деньги","payload":"cat_money"}],
                    [{"type":"callback","text":"🧠 Психолог","payload":"psycho"},
                     {"type":"callback","text":"✨ Узнать себя","payload":"cat_self"}],
                    [{"type":"callback","text":"🏠 Главное меню","payload":"back_menu"}],
                ],
            )
            return

        source_map = {
            "channel_taro": "taro",
            "channel_money": "money_scenario",
            "channel_psycho": "psycho",
            "channel_horoscope": "my_day",
            "channel_day": "my_day",
            "channel_dreams": "dreams",
            "channel_matrix": "matrix",
            "channel_forecast": "forecast_period",
            "channel_love": "compatibility",
            "channel_self": "numerology",
            "channel_diary": "evening_reflection",
        }
        target = source_map.get(payload)
        if target:
            log_event(user_id, "channel_entry", feature=target, source=payload)
            await process_callback(message.chat.id, user_id, target, first_name)
            return

    await send_message(
        message.chat.id,
        WELCOME_TEXT.format(name=first_name),
        main_menu_buttons()
    )


PLAN_PRICES = {
    "aura_start": 190,
    "aura_pro": 390,
    "aura_pro_year": 2990,
    "once_money_code": 199,
    "once_matrix": 249,
    "once_forecast": 299,
    "once_natal": 349,
}


def _revenue_for_rows(rows):
    return sum(PLAN_PRICES.get((row[0] or '').strip(), 0) for row in rows)


def build_admin_stats_report():
    now = datetime.now()
    since7 = (now - timedelta(days=7)).isoformat()
    with db_connect() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_start = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE plan='aura_start' AND sub_end>?", (now.isoformat(),)).fetchone()[0]
        active_pro = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE plan='aura_pro' AND sub_end>?", (now.isoformat(),)).fetchone()[0]
        total_reviews = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        reviews_7d = conn.execute("SELECT COUNT(*) FROM reviews WHERE created_at>=?", (since7,)).fetchone()[0]
        support_7d = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event='support_sent' AND created_at>=?", (since7,)).fetchone()[0]
        sales_all = conn.execute("SELECT plan FROM payment_history WHERE status='succeeded'").fetchall()
        sales_7d = conn.execute("SELECT plan FROM payment_history WHERE status='succeeded' AND processed_at>=?", (since7,)).fetchall()
    total_sales = len(sales_all)
    sales7 = len(sales_7d)
    revenue_all = _revenue_for_rows(sales_all)
    revenue_7d = _revenue_for_rows(sales_7d)
    return (
        "📊 Aura Telegram — сводка\n\n"
        f"Пользователи: {total_users}\n"
        f"Активные подписки: {active_start + active_pro} (Старт {active_start} • Про {active_pro})\n"
        f"Продажи всего: {total_sales} • {revenue_all} ₽\n"
        f"Продажи за 7 дней: {sales7} • {revenue_7d} ₽\n"
        f"Отзывы: {total_reviews} (за 7 дней: {reviews_7d})\n"
        f"Обращения в поддержку за 7 дней: {support_7d}"
    )


def build_admin_funnel_report():
    now = datetime.now()
    since7 = (now - timedelta(days=7)).isoformat()
    since30 = (now - timedelta(days=30)).isoformat()
    with db_connect() as conn:
        channel_entries_7d = conn.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE event='channel_entry' AND created_at>=?",
            (since7,),
        ).fetchone()[0]
        top_sources = conn.execute(
            "SELECT source, COUNT(*) FROM analytics_events WHERE event='channel_entry' AND created_at>=? GROUP BY source ORDER BY COUNT(*) DESC LIMIT 6",
            (since7,),
        ).fetchall()
        channel_users_total = conn.execute(
            "SELECT COUNT(*) FROM user_profiles WHERE source='channel_intro' OR source LIKE 'channel_%'",
        ).fetchone()[0]
        channel_sales_30d_rows = conn.execute(
            "SELECT ph.plan FROM payment_history ph JOIN user_profiles up ON up.user_id=ph.user_id WHERE ph.status='succeeded' AND ph.processed_at>=? AND (up.source='channel_intro' OR up.source LIKE 'channel_%')",
            (since30,),
        ).fetchall()
    top_lines = [f"• {src or 'unknown'} — {cnt}" for src, cnt in top_sources] or ["• пока нет переходов"]
    channel_sales_30d = len(channel_sales_30d_rows)
    channel_revenue_30d = _revenue_for_rows(channel_sales_30d_rows)
    return (
        "🚀 Aura Telegram — воронка\n\n"
        f"Переходы из канала за 7 дней: {channel_entries_7d}\n"
        f"Пользователи, пришедшие из канала: {channel_users_total}\n"
        f"Продажи от канала за 30 дней: {channel_sales_30d} • {channel_revenue_30d} ₽\n\n"
        "Топ входов за 7 дней:\n" + "\n".join(top_lines) +
        "\n\nРеклама: веди в канал. В закрепе — intro-пост, в ленте — 1 CTA-пост в день и 2–3 визуальных поста в неделю."
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await process_start_payload(message)

@dp.message(Command("reset_me"))
async def cmd_reset_me(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    user_id = message.from_user.id
    with db_connect() as conn:
        conn.execute("INSERT OR IGNORE INTO limits (user_id) VALUES (?)", (user_id,))
        conn.execute(
            """UPDATE limits SET requests=0, psycho_messages=0,
               photo_chiromancy=0, photo_physio=0, photo_grapho=0
               WHERE user_id=?""",
            (user_id,),
        )
        conn.execute("DELETE FROM sales_prompts WHERE user_id=?", (user_id,))
    set_step(user_id, "idle")
    await message.answer(
        "✅ Тестовые лимиты сброшены только для вашего аккаунта.\n\n"
        "Снова доступны 5 бесплатных разборов и 15 сообщений психологу. "
        "Подписки и разовые покупки не изменены."
    )

@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    with db_connect() as conn:
        conn.execute("UPDATE limits SET requests=0, psycho_messages=0, photo_chiromancy=0, photo_physio=0, photo_grapho=0")
    await message.answer("✅ Лимиты сброшены.")

@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) not in (3, 4):
        await message.answer("Формат: /activate user_id aura_pro [days]")
        return
    try:
        target = int(parts[1])
        plan = parts[2]
        days = int(parts[3]) if len(parts) == 4 else 30
        set_subscription(target, plan, days)
        await message.answer(f"✅ {plan} активирован для {target} на {days} дней.")
    except Exception as exc:
        await message.answer(f"Ошибка: {exc}")

@dp.message(Command("publish_channel_intro"))
async def cmd_publish_channel_intro(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    ok = await publish_channel_intro()
    await message.answer(
        "✅ Продающий пост опубликован. Теперь закрепи его в канале."
        if ok else
        "❌ Не удалось опубликовать пост. Проверь журнал сервиса."
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer(build_admin_stats_report())


@dp.message(Command("funnel"))
async def cmd_funnel(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer(build_admin_funnel_report())

@dp.message(Command("reviews"))
async def cmd_reviews(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    rows = get_recent_reviews(10)
    if not rows:
        await message.answer("Пока нет сохранённых отзывов.")
        return
    lines = ["⭐️ Последние отзывы", "", "Публикуй только после разрешения автора: /publish_review ID"]
    for review_id, first_name, review, created in rows:
        lines.append(
            f"\nID {review_id} • {first_name or 'Анонимно'} • {created[:10]}\n"
            f"{truncate_at_sentence(review, 260)}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("publish_review"))
async def cmd_publish_review(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /publish_review ID\nПубликуй только после разрешения автора.")
        return
    ok = await publish_saved_review(int(parts[1]))
    await message.answer("✅ Анонимный отзыв опубликован." if ok else "❌ Отзыв не найден или публикация не удалась.")


@dp.callback_query()
async def callback_router(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    await process_callback(
        callback.message.chat.id,
        callback.from_user.id,
        callback.data or "",
        callback.from_user.first_name or "друг"
    )

@dp.message(F.photo)
async def photo_router(message: Message):
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    await process_photo(message.chat.id, message.from_user.id, photo_url)

async def transcribe_voice(file_path):
    with open(file_path, "rb") as audio_file:
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru"
        )
    return response.text

@dp.message(F.voice)
async def voice_router(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id, message.from_user.username or "", message.from_user.first_name or "")
    step = user.get("step", "")
    if not step or step == "idle":
        await send_message(message.chat.id, "Сначала выбери функцию, затем отправь голосовое.", main_menu_buttons())
        return

    file_path = f"/tmp/aura_voice_{user_id}_{message.message_id}.ogg"
    try:
        await send_message(message.chat.id, "🎤 Распознаю голосовое...")
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, file_path)
        text = await transcribe_voice(file_path)
        await process_command(
            message.chat.id,
            user_id,
            text,
            message.from_user.username or "",
            message.from_user.first_name or ""
        )
    except Exception:
        logging.exception("Ошибка обработки голосового")
        await send_message(message.chat.id, "Не удалось обработать голосовое. Попробуй ещё раз.", back_button())
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

@dp.message(F.text)
async def text_router(message: Message):
    await process_command(
        message.chat.id,
        message.from_user.id,
        message.text or "",
        message.from_user.username or "",
        message.from_user.first_name or ""
    )


# ========== ФИРМЕННЫЕ ВИЗУАЛЫ КАНАЛА ==========
VISUAL_DIR = "/tmp/aura_channel_visuals"
VISUAL_DAYS = {0: "Планы недели", 2: "Отношения", 4: "Выбор и Таро", 6: "Итоги недели"}


def _load_visual_font(size, bold=False, serif=False):
    from PIL import ImageFont
    if serif:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        ]
    elif bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap_visual_text(draw, text, font, max_width, max_lines=4):
    words = clean_display_text(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines)) < len(" ".join(words)):
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def create_channel_visual(dt, rubric, title):
    """Create four distinct premium editorial cards per week without external APIs."""
    if rubric != "value" or dt.weekday() not in VISUAL_DAYS:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFilter
        import random
        os.makedirs(VISUAL_DIR, exist_ok=True)
        path = os.path.join(VISUAL_DIR, f"{dt.strftime('%Y%m%d')}_{rubric}_premium.png")
        if os.path.exists(path):
            return path
        size = 1200
        img = Image.new("RGB", (size, size), (15, 6, 32))
        pixels = img.load()
        for y in range(size):
            for x in range(size):
                dx, dy = x - 720, y - 500
                radial = max(0.0, 1.0 - ((dx * dx + dy * dy) ** 0.5) / 900)
                vertical = y / size
                pixels[x, y] = (
                    int(15 + 35 * radial + 10 * vertical),
                    int(6 + 12 * radial),
                    int(32 + 62 * radial + 18 * vertical),
                )
        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((610, 150, 1180, 720), fill=(156, 88, 255, 70))
        glow_draw.ellipse((-240, 700, 400, 1340), fill=(87, 39, 160, 55))
        glow = glow.filter(ImageFilter.GaussianBlur(85))
        img = Image.alpha_composite(img.convert("RGBA"), glow)
        draw = ImageDraw.Draw(img, "RGBA")
        rnd = random.Random(int(dt.strftime("%Y%m%d")) + dt.weekday() * 91)
        for _ in range(105):
            x, y = rnd.randint(30, 1170), rnd.randint(30, 1170)
            radius = rnd.choice((1, 1, 1, 2, 2, 3))
            alpha = rnd.randint(65, 180)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(239, 220, 255, alpha))
        day = dt.weekday()
        gold = (222, 187, 116, 205)
        violet = (199, 161, 255, 180)
        if day == 0:
            for radius in (150, 205, 260):
                draw.arc((850 - radius, 125 - radius, 850 + radius, 125 + radius), 5, 175, fill=gold, width=3)
        elif day == 2:
            draw.ellipse((790, 115, 1050, 375), outline=violet, width=4)
            draw.ellipse((900, 115, 1160, 375), outline=gold, width=4)
        elif day == 4:
            draw.rounded_rectangle((860, 95, 1080, 410), radius=24, outline=gold, width=4, fill=(23, 10, 47, 125))
            draw.ellipse((925, 160, 1015, 250), outline=gold, width=3)
            draw.line((970, 135, 970, 330), fill=gold, width=2)
        else:
            points = [(780, 150), (920, 90), (1085, 205), (1015, 350), (850, 320)]
            for first, second in zip(points, points[1:]):
                draw.line((*first, *second), fill=gold, width=3)
            for x, y in points:
                draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(255, 237, 189, 230))
        brand = _load_visual_font(31, bold=True)
        title_font = _load_visual_font(70, bold=True)
        serif = _load_visual_font(31, serif=True)
        small = _load_visual_font(27)
        draw.text((78, 72), "АУРА — ПСИХОЛОГИЯ", font=brand, fill=(232, 211, 255, 240))
        draw.line((78, 125, 510, 125), fill=(221, 187, 116, 185), width=2)
        panel = (65, 360, 1135, 1010)
        draw.rounded_rectangle(panel, radius=42, fill=(18, 8, 39, 174), outline=(218, 187, 244, 90), width=2)
        draw.text((105, 410), VISUAL_DAYS[day].upper(), font=small, fill=(222, 187, 116, 235))
        y_pos = 500
        for line in _wrap_visual_text(draw, title, title_font, 910, 4):
            draw.text((105, y_pos), line, font=title_font, fill=(255, 255, 255, 245))
            y_pos += 92
        draw.text((105, 930), "Пойми себя • найди опору • сделай следующий шаг", font=serif, fill=(222, 208, 236, 235))
        draw.text((78, 1100), "Практика • психология • самопознание", font=small, fill=(203, 178, 224, 220))
        img.convert("RGB").save(path, quality=95)
        return path
    except Exception as exc:
        logging.exception("Визуал канала: %s", exc)
        return None


def get_recent_reviews(limit=10):
    try:
        with db_connect() as conn:
            return conn.execute(
                "SELECT id,first_name,review,created_at FROM reviews ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    except Exception:
        return []


# ========== КАНАЛ TELEGRAM — PREMIUM FUNNEL ==========
CHANNEL_EDITOR_SYSTEM = """Ты главный редактор премиального канала «Аура — Психология».
Канал сочетает практическую психологию, бережное самопознание и символические практики.
Пиши живо, конкретно и современно. Не используй дешёвую мистику, запугивание, фатальные обещания, выдуманные отзывы и гарантии результата.
Пост должен дать узнавание, одну практическую пользу и естественное персональное продолжение.
Не добавляй ссылки, хэштеги и Markdown: нативную кнопку добавит программа. Короткие абзацы, русский язык."""

CHANNEL_SLOTS = {(9, 0): "morning", (13, 0): "value", (20, 0): "evening"}

WEEKLY_FUNNEL = {
    0: {"theme": "внутреннее состояние и планы недели", "morning": ("🌟 Получить личную подсказку", "channel_day"), "value": ("🧠 Разобраться в своём состоянии", "channel_psycho"), "evening": ("📝 Подвести итоги дня", "channel_diary")},
    1: {"theme": "деньги, самоценность и реализация", "morning": ("💰 Разобрать денежный сценарий", "channel_money"), "value": ("💰 Получить бесплатный разбор", "channel_money"), "evening": ("🌟 Получить подсказку на день", "channel_day")},
    2: {"theme": "отношения, границы и близость", "morning": ("❤️ Посмотреть совместимость", "channel_love"), "value": ("❤️ Разобраться в отношениях", "channel_love"), "evening": ("🃏 Задать вопрос картам", "channel_taro")},
    3: {"theme": "тревога, усталость и опора на себя", "morning": ("🧠 Поговорить с психологом", "channel_psycho"), "value": ("🧠 Разложить ситуацию по полочкам", "channel_psycho"), "evening": ("📝 Сделать вечернюю рефлексию", "channel_diary")},
    4: {"theme": "Таро, выбор и неопределённость", "morning": ("🃏 Получить личный расклад", "channel_taro"), "value": ("🔮 Задать свой вопрос", "channel_taro"), "evening": ("🌙 Разобрать сон", "channel_dreams")},
    5: {"theme": "самопознание, сильные стороны и предназначение", "morning": ("🔢 Получить личный разбор", "channel_self"), "value": ("✨ Узнать свои сильные стороны", "channel_self"), "evening": ("🔢 Узнать больше о себе", "channel_self")},
    6: {"theme": "итоги недели, восстановление и новый цикл", "morning": ("📅 Получить прогноз недели", "channel_forecast"), "value": ("📝 Подвести итоги недели", "channel_diary"), "evening": ("🌟 Получить подсказку на завтра", "channel_day")},
}

FALLBACK_POSTS = {
    "morning": """🌅 Один вопрос на сегодня

Какое состояние ты хочешь сохранить — спокойствие, ясность или уверенность?

Перед первым важным делом остановись на десять секунд, сделай медленный вдох и назови своё намерение. Маленькая пауза помогает действовать не из тревоги, а из выбранной опоры.""",
    "value": """🧠 Практика, которая возвращает ясность

Раздели лист на три части: «что я знаю точно», «что я предполагаю» и «что я чувствую».

Тревога часто смешивает эти слои. Когда они разделены, проще увидеть, где нужны действия, а где — поддержка и время.""",
    "evening": """🌙 Вечерняя перезагрузка

Назови три вещи: что сегодня получилось, что забрало силы и что можно не нести с собой в завтра.

Один сложный момент не отменяет весь день.""",
}


def channel_deep_link(payload):
    return deep_link(payload)


def native_channel_keyboard(button_text, start_payload):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=button_text[:64], url=channel_deep_link(start_payload)
    )]])


async def send_to_channel(text, button_text, start_payload, image_path=None):
    keyboard = native_channel_keyboard(button_text, start_payload)
    clean = clean_display_text(text)
    if image_path and os.path.exists(str(image_path)):
        from aiogram.types import FSInputFile
        return await bot.send_photo(
            CHANNEL_ID,
            FSInputFile(image_path),
            caption=truncate_at_sentence(clean, 950),
            reply_markup=keyboard,
        )
    return await bot.send_message(
        CHANNEL_ID,
        truncate_at_sentence(clean, 4000),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


def channel_slot_key(dt, rubric):
    return f"{dt.strftime('%Y-%m-%d')}_{rubric}"


def channel_was_sent(key):
    with db_connect() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM channel_posts WHERE slot_key=? AND status='sent'", (key,)
        ).fetchone())


def save_channel_post(key, rubric, topic, content, status):
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO channel_posts(slot_key,rubric,topic,content,status,published_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(slot_key) DO UPDATE SET
               rubric=excluded.rubric,topic=excluded.topic,content=excluded.content,
               status=excluded.status,published_at=excluded.published_at""",
            (key, rubric, topic[:250], content[:4000], status, datetime.now(MOSCOW).isoformat()),
        )


def recent_channel_topics(limit=30):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT rubric,topic FROM channel_posts WHERE status='sent' ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return "\n".join(f"- {rubric}: {topic}" for rubric, topic in rows if topic)


def extract_topic(text):
    return " ".join(clean_display_text(text).replace("\n", " ").split())[:220]


def _channel_content_mode(dt, rubric):
    if rubric != "value":
        return rubric
    return {
        0: "practical_guide",
        1: "free_money_demo",
        2: "relationship_checklist",
        3: "micro_exercise",
        4: "interactive_choice",
        5: "bot_demo",
        6: "weekly_digest",
    }[dt.weekday()]


def build_channel_prompt(dt, rubric):
    theme = WEEKLY_FUNNEL[dt.weekday()]["theme"]
    recent = recent_channel_topics()
    avoid = f"\nНедавние темы, которые нельзя повторять:\n{recent}" if recent else ""
    mode = _channel_content_mode(dt, rubric)
    if rubric == "morning":
        task = f"Тема: {theme}. Утренний пост 350–600 знаков: сильная первая строка, узнаваемая мысль, практика на минуту и один вопрос."
    elif rubric == "evening":
        task = f"Тема: {theme}. Вечерний пост 350–600 знаков: мягкий итог, практика на 2 минуты и один точный вопрос."
    else:
        formats = {
            "practical_guide": "Практический мини-гайд: проблема, простое объяснение и три шага.",
            "free_money_demo": "Покажи ценность бесплатного разбора денежного сценария: три вопроса для самодиагностики и один небольшой вывод. Не продавай платный код в самом тексте.",
            "relationship_checklist": "Чек-лист по отношениям: четыре признака здоровой опоры и один вопрос читателю.",
            "micro_exercise": "Психологическое упражнение на тревогу или усталость, которое можно выполнить за три минуты.",
            "interactive_choice": "Интерактив: три символических варианта и короткая расшифровка. Скажи, что это рефлексия, а не предсказание.",
            "bot_demo": "Покажи ИЛЛЮСТРАТИВНЫЙ пример структуры персонального ответа бота. Не выдавай его за отзыв или реальную историю. Объясни отличие личного разбора от общего поста.",
            "weekly_digest": "Сохраняемый итог недели: три вопроса, одна практика и намерение на следующую неделю.",
        }
        limit = "до 850 знаков" if dt.weekday() in VISUAL_DAYS else "до 1200 знаков"
        task = f"Тема: {theme}. Главный пост {limit}. {formats[mode]} Короткие абзацы и конкретика."
    return task + avoid


async def generate_channel_post(dt, rubric):
    limit = 580 if rubric in ("morning", "evening") else (880 if dt.weekday() in VISUAL_DAYS else 1250)
    try:
        text = clean_display_text(await generate_text(CHANNEL_EDITOR_SYSTEM, build_channel_prompt(dt, rubric)))
        if len(text) < 100:
            raise RuntimeError("слишком короткий пост")
        return truncate_at_sentence(text, limit)
    except Exception as exc:
        logging.warning("Канал: генерация %s не удалась: %s", rubric, exc)
        return FALLBACK_POSTS[rubric]


async def publish_channel_slot(dt, rubric):
    key = channel_slot_key(dt, rubric)
    if channel_was_sent(key):
        return False
    text = await generate_channel_post(dt, rubric)
    button_text, start_payload = WEEKLY_FUNNEL[dt.weekday()][rubric]
    try:
        visual = create_channel_visual(dt, rubric, WEEKLY_FUNNEL[dt.weekday()]["theme"])
        await send_to_channel(text, button_text, start_payload, visual)
        save_channel_post(key, rubric, extract_topic(text), text, "sent")
        return True
    except Exception as exc:
        save_channel_post(key, rubric, extract_topic(text), text, "failed")
        await notify_owner("⚠️ Не вышел пост в Telegram-канале", 0, rubric, f"{key}: {exc}")
        return False


async def publish_saved_review(review_id):
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT first_name,review FROM reviews WHERE id=?", (int(review_id),)
            ).fetchone()
        if not row:
            return False
        first_name = (row[0] or "Пользователь").split()[0]
        text = (
            "⭐️ Реальный отзыв об Ауре\n\n"
            f"«{truncate_at_sentence(row[1], 900)}»\n\n"
            f"— {first_name}, имя опубликовано с разрешения автора."
        )
        await send_to_channel(text, "🎁 Попробовать Ауру бесплатно", "channel_intro")
        return True
    except Exception as exc:
        logging.exception("Публикация отзыва: %s", exc)
        return False


async def publish_channel_intro():
    text = """🔮 Добро пожаловать в «Аура — Психология»

Иногда нужен не общий совет, а спокойное место, где можно понять, что происходит именно с тобой.

Здесь каждый день:
🧠 простая психология без сложных терминов
❤️ отношения, границы и самоценность
💰 деньги, реализация и внутренние опоры
🃏 Таро и символические практики как способ посмотреть на ситуацию иначе
🌙 короткие упражнения для возвращения к себе

Когда общего поста недостаточно, AuraBot поможет разобрать именно твою ситуацию.

🎁 Бесплатно: 5 персональных разборов и 15 сообщений AI-психологу.

Нажми кнопку и выбери то, что волнует тебя сейчас."""
    try:
        image_path = str(INTRO_IMAGE_PATH) if INTRO_IMAGE_PATH.exists() else None
        await send_to_channel(text, "🎁 Начать бесплатный личный разбор", "channel_intro", image_path)
        return True
    except Exception as exc:
        logging.exception("Не удалось опубликовать intro: %s", exc)
        return False


async def channel_posting_loop():
    await asyncio.sleep(5)
    while True:
        now = datetime.now(MOSCOW)
        try:
            for (hour, minute), rubric in CHANNEL_SLOTS.items():
                slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if 0 <= (now - slot).total_seconds() <= 21600:
                    await publish_channel_slot(slot, rubric)
                    await asyncio.sleep(2)
            candidates = []
            for (hour, minute), rubric in CHANNEL_SLOTS.items():
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                candidates.append((candidate, rubric))
            next_dt, next_rubric = min(candidates, key=lambda item: item[0])
            await asyncio.sleep(max(1, (next_dt - datetime.now(MOSCOW)).total_seconds()))
            await publish_channel_slot(next_dt, next_rubric)
        except Exception as exc:
            logging.exception("Channel loop Telegram: %s", exc)
            await asyncio.sleep(60)


# ========== MAIN ==========
async def main():
    init_db()
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(daily_loop())
    asyncio.create_task(channel_posting_loop())
    logging.info("Aura Telegram Bot Premium 10/10 запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
