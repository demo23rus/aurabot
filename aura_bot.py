import asyncio
import sqlite3
import logging
import uuid
import os
import json
import re
import hashlib
import random
from urllib.parse import quote
from zoneinfo import ZoneInfo
import httpx
from datetime import datetime, timedelta
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
import anthropic
import gspread
from google.oauth2.service_account import Credentials

# ========== КОНФИГ ==========
BOT_TOKEN = "8887660316:AAHoVJ90RWIE6jz-pFbv8y3WVAI9WEsOXno"
BOT_USERNAME = "myaura_mystic_bot"
CHANNEL_ID = "@aurabot_mystic"
OPENAI_KEY = "sk-mfvVI3QN2uQvXPlhMkAeUUzmbjK5aQzj"
CLAUDE_KEY = "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"
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

# Лимиты
FREE_REQUESTS = 5
FREE_PSYCHO = 15
START_PSYCHO = 100
START_PHOTO = 5

# ЮКасса
YOOKASSA_SHOP_ID = "1363324"
YOOKASSA_SECRET = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"

# ========== GOOGLE SHEETS — КОМПАКТНАЯ КОММЕРЧЕСКАЯ АНАЛИТИКА ==========
GOOGLE_CREDS_PATH = "/root/google_credentials.json"
SPREADSHEET_NAME = "PostGenius Users"
USERS_SHEET_NAME = "Aura Telegram"
SALES_SHEET_NAME = "Продажи Aura"

USERS_HEADERS = ["Последнее посещение", "ID", "Имя", "Username", "Запросы", "Подписка", "До", "Отзыв"]
SALES_HEADERS = ["Дата", "Платформа", "ID", "Имя", "Тариф", "Сумма", "Подписка до"]

def _open_spreadsheet():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
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

def sheets_log_sale(user_id, first_name, plan, amount, sub_end, platform):
    try:
        ws = _get_or_create_sheet(SALES_SHEET_NAME, SALES_HEADERS)
        plan_name = {"aura_start": "Старт", "aura_pro": "Про", "aura_pro_year": "Про на год"}.get(plan, plan)
        ws.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"), platform, str(user_id), first_name or "—",
            plan_name, f"{amount} ₽", sub_end.strftime("%d.%m.%Y") if sub_end else "—"
        ])
        sheets_sync_user(user_id, first_name, "")
    except Exception as e:
        logging.error(f"Google Sheets sale log: {e}")


# ========== ЛОГИ ==========
logging.basicConfig(level=logging.INFO)

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY, base_url="https://api.proxyapi.ru/openai/v1")
claude_client = anthropic.Anthropic(api_key=CLAUDE_KEY)

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
        ],
        "love": [
            [{"type":"callback","text":"❤️ Совместимость по датам","payload":"compatibility"}],
            [{"type":"callback","text":"👫 Совместимость по фото","payload":"compat_photo"}],
            [{"type":"callback","text":"🃏 Таро на отношения","payload":"taro"}],
        ],
        "money": [
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
            [{"type": "callback", "text": "🔥 Купить Про — 390 руб", "payload": "pay_pro"}],
            [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
        ]
    return [
        [{"type": "callback", "text": "🟢 Старт — 190 руб", "payload": "pay_start"}],
        [{"type": "callback", "text": "🔥 Про — 390 руб", "payload": "pay_pro"}],
        [{"type": "callback", "text": "💜 Про на год — 2 990 руб", "payload": "pay_year"}],
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
    product_code = FEATURE_TO_PRODUCT.get(feature)
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

NUMEROLOGY_SYSTEM = "Ты мудрый нумеролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
TARO_SYSTEM = "Ты мудрый таролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
DREAMS_SYSTEM = "Ты мудрый толкователь снов с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
AURA_SYSTEM = "Ты мудрый энергетик с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
MATRIX_SYSTEM = "Ты мудрый мастер Матрицы Судьбы с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
FORECAST_SYSTEM = "Ты мудрый прорицатель с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
COMPATIBILITY_SYSTEM = "Ты мудрый астропсихолог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
NATAL_SYSTEM = "Ты мудрый астролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
HOROSCOPE_SYSTEM = "Ты мудрый астролог с 20-летним опытом. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
MONEY_CODE_SYSTEM = "Ты мудрый нумеролог специализирующийся на денежном коде. Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."
CHIROMANCY_SYSTEM = "Ты опытный хиромант. Смотришь на фото ладони и рассказываешь конкретно и лично. Пишешь только на русском. Никаких звёздочек и решёток."
PHYSIO_SYSTEM = "Ты делаешь только развлекательный и рефлексивный разбор визуального впечатления от фото. Не определяй характер как факт, здоровье, интеллект, этничность, религию, ориентацию, надёжность или диагнозы. Используй формулировки 'может создавать впечатление'. Пишешь только на русском."
GRAPHO_SYSTEM = "Ты опытный графолог. Смотришь на фото почерка и рассказываешь о характере конкретно. Пишешь только на русском. Никаких звёздочек и решёток."
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

async def generate_with_claude_photo(system_prompt, image_bytes):
    import base64
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    # Определяем формат по magic bytes
    if image_bytes[:4] == b'RIFF' or image_bytes[8:12] == b'WEBP':
        media_type = "image/webp"
    elif image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        media_type = "image/png"
    elif image_bytes[:3] == b'GIF':
        media_type = "image/gif"
    else:
        media_type = "image/jpeg"
    response = await asyncio.to_thread(
        claude_client.messages.create,
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}},
            {"type": "text", "text": system_prompt}
        ]}]
    )
    return clean_display_text(response.content[0].text)

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
        today = datetime.now(MOSCOW).strftime("%d.%m.%Y")
        try:
            lunar_text = await generate_text(LUNAR_SYSTEM,
                f"Сегодня {today}. Дай мягкий общий лунный настрой без выдумывания точной астрономической фазы, если она не передана: что полезно делать, чего избегать, совет дня.")
            with db_connect() as conn:
                users = conn.execute("SELECT u.user_id FROM users u LEFT JOIN user_profiles p ON p.user_id=u.user_id WHERE COALESCE(p.stopped,0)=0").fetchall()
            for (uid,) in users:
                try:
                    await send_message(uid, f"🌙 Настрой на {today}\n\n{lunar_text}")
                    await asyncio.sleep(0.04)
                except Exception as e:
                    logging.warning(f"daily lunar {uid}: {e}")
        except Exception as e:
            logging.exception(f"Ошибка общего утреннего сообщения: {e}")
        with db_connect() as conn:
            pro_users = conn.execute("""SELECT u.user_id, COALESCE(p.birth_date,u.birth_date) FROM users u
                JOIN subscriptions s ON u.user_id=s.user_id
                LEFT JOIN user_profiles p ON p.user_id=u.user_id
                WHERE s.plan='aura_pro' AND s.sub_end>? AND COALESCE(p.birth_date,u.birth_date,'')!='' AND COALESCE(p.stopped,0)=0""",
                (datetime.now().isoformat(),)).fetchall()
        for uid,birth in pro_users:
            try:
                text=await generate_text(HOROSCOPE_SYSTEM,f"Дата рождения: {birth}. Сегодня {today}. Дай персональную подсказку: энергия, отношения, деньги, главное действие. Без фатальных обещаний.")
                await send_message(uid,f"⭐️ Твоя личная подсказка на {today}\n\n{text}")
                await asyncio.sleep(0.04)
            except Exception as e: logging.warning(f"personal daily {uid}: {e}")

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
    set_step(user_id, "support")
    await send_message(chat_id,
        "💬 Поддержка Aura\n\nОпиши, что произошло: какая функция не сработала, что ты нажимала и что увидела. "
        "Сообщение сразу придёт владельцу проекта.",
        back_button())

async def process_support_message(chat_id, user_id, text):
    set_step(user_id, "idle")
    await notify_owner("🆘 Новое обращение в поддержку Aura", user_id, "support", text)
    await send_message(chat_id,
        "✅ Сообщение отправлено. Мы проверим проблему и постараемся отреагировать как можно быстрее.",
        main_menu_buttons())


# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
WELCOME_TEXT = """🔮 {name}, добро пожаловать в AuraBot.

Я помогу получить не общий текст, а личную подсказку под твою ситуацию: отношения, деньги, предназначение или внутреннее состояние.

🎁 Начни с бесплатного разбора — выбери, что волнует тебя сейчас."""

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
        await send_message(chat_id, "📔 Личный дневник доступен с тарифа 🟢 Старт.\n\n190 руб/мес:", upgrade_buttons())
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
        birth=extract_birth_date(text)
        if not birth:
            await send_message(chat_id,"Не смогла распознать дату. Напиши, например: 15.03.1990",back_button()); return
        save_profile(user_id,birth_date=birth)
        with db_connect() as conn: conn.execute("UPDATE users SET birth_date=? WHERE user_id=?",(birth,user_id))
        set_step(user_id,"idle")
        result=await generate_text(HOROSCOPE_SYSTEM,f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. Дай персональную подсказку: энергия дня, отношения, деньги, главное действие и вечерняя практика. Не обещай неизбежных событий.")
        await send_message(chat_id,"🌟 Твой день\n\n"+result,back_button()); return
    if step == "review":
        set_step(user_id, "idle")
        save_review(user_id, username, first_name, text)
        asyncio.create_task(asyncio.to_thread(sheets_log_review, user_id, first_name, username, text))
        await send_message(chat_id, "⭐️ Спасибо за отзыв! Обязательно учтём.", main_menu_buttons())
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

    step_map = {
        "numerology": (NUMEROLOGY_SYSTEM, f"Дата рождения: {{text}}\n\nРассчитай числа судьбы, личности, души. Объясни что означает для этого человека."),
        "matrix": (MATRIX_SYSTEM, "Дата рождения: {text}\n\nРассчитай Матрицу Судьбы. Расскажи о кармических задачах, предназначении, талантах."),
        "taro": (TARO_SYSTEM, "Вопрос: {text}\n\nВытащи 3 карты Таро. Расклад: прошлое, настоящее, будущее. Расскажи что означают."),
        "dreams": (DREAMS_SYSTEM, "Сон: {text}\n\nДай толкование психологическое и эзотерическое. Говори конкретно."),
        "aura": (AURA_SYSTEM, "Дата рождения: {text}\n\nРасскажи об ауре: цвет, энергетика, сильные стороны, уязвимости."),
        "forecast_period": (FORECAST_SYSTEM, "Данные: {text}\n\nСоставь краткий практичный нумерологический прогноз только на указанный период — неделю или месяц. Не превращай его в полный годовой разбор."),
        "annual_forecast": (FORECAST_SYSTEM, "Дата рождения: {text}\n\nСоставь полный персональный прогноз на 12 месяцев: главная тема года, деньги и работа, отношения, внутреннее состояние, сильные периоды, периоды осторожности, рекомендации по каждому кварталу и итоговый план действий. Не давай фатальных гарантий."),
        "compatibility": (COMPATIBILITY_SYSTEM, "Данные: {text}\n\nПроанализируй совместимость двух людей."),
        "natal": (NATAL_SYSTEM, "Данные (дата, время, место): {text}\n\nПрочитай натальную карту."),
        "horoscope": (HOROSCOPE_SYSTEM, f"Знак зодиака: {{text}}\n\nГороскоп на сегодня {datetime.now().strftime('%d.%m.%Y')}."),
        "money_code": (MONEY_CODE_SYSTEM, "Имя и дата: {text}\n\nРассчитай денежный код. Расскажи что означает и как активировать."),
    }

    if step in step_map:
        birth = extract_birth_date(text)
        if birth:
            save_profile(user_id, birth_date=birth)
            with db_connect() as conn:
                conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth,user_id))
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
        system, prompt_tpl = step_map[step]
        prompt = prompt_tpl.replace("{text}", text)
        set_step(user_id, "idle")
        await send_message(chat_id, "⏳ Анализирую...")
        result = await generate_text(system, prompt)
        if access == "ok":
            increment_limit(user_id, "requests")
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, first_name, username))
        elif access == "one_time":
            if not consume_one_time_credit(user_id, feature):
                await notify_owner("⚠️ Ошибка списания разового разбора", user_id, feature, "Результат создан, но кредит не найден")
        offer_note, offer_buttons = build_result_offer(user_id, feature, used_one_time=use_one_time)
        await send_message(chat_id, result + offer_note, offer_buttons)
        return

    await send_message(chat_id, "Выбери действие из меню 👇", main_menu_buttons())

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
        with db_connect() as conn:
            row=conn.execute("SELECT birth_date FROM user_profiles WHERE user_id=?",(user_id,)).fetchone()
        birth=row[0] if row else ""
        if not birth:
            set_step(user_id,"my_day_birth")
            await send_message(chat_id,"🌟 Мой день\n\nВведи дату рождения в формате ДД.ММ.ГГГГ — я сохраню её и подготовлю личную подсказку.",back_button()); return
        await send_message(chat_id,"⏳ Собираю личную подсказку...")
        result=await generate_text(HOROSCOPE_SYSTEM, f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. Дай персональную подсказку: энергия дня, отношения, деньги, главное действие и короткая вечерняя практика. Не обещай неизбежных событий.")
        await send_message(chat_id,"🌟 Твой день\n\n"+result,back_button()); return
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
        await send_message(chat_id, "🧠 Новый разговор.\n\nРасскажи что тебя беспокоит.", psycho_buttons())
        return

    if payload == "tariffs":
        plan, sub_end = get_subscription(user_id)
        current = ""
        if plan == "aura_start":
            current = f"\n\n✅ Твой тариф: 🟢 Старт (до {sub_end.strftime('%d.%m.%Y')})"
        elif plan == "aura_pro":
            current = f"\n\n✅ Твой тариф: 🔥 Про (до {sub_end.strftime('%d.%m.%Y')})"
        await send_message(chat_id,
            f"💎 Тарифы AuraBot\n\n"
            f"🟢 Старт — 190 руб / 1 месяц\n"
            f"Все базовые функции безлимит\n"
            f"Хиромантия, Физиогномика, Графология — по 5 раз\n"
            f"Психолог — 100 сообщений\n"
            f"Личный дневник\n\n"
            f"🔥 Про — 390 руб / 1 месяц\n"
            f"Всё без ограничений\n"
            f"Матрица, Прогноз, Натальная карта\n"
            f"Денежный код, все фото-анализы\n"
            f"Персональный гороскоп каждое утро\n\n"
            f"✨ Разовые разборы без подписки\n"
            f"Денежный код — 199 ₽\n"
            f"Матрица судьбы — 249 ₽\n"
            f"Прогноз на год — 299 ₽\n"
            f"Натальная карта — 349 ₽\n\n"
            f"🌙 Всем бесплатно: лунный календарь каждое утро\n\n"
            f"🎁 Бесплатно: 5 разборов + 15 сообщений психологу{current}",
            [
                [{"type": "callback", "text": "🟢 Старт — 190 руб", "payload": "pay_start"}],
                [{"type": "callback", "text": "🔥 Про — 390 руб", "payload": "pay_pro"}],
                [{"type": "callback", "text": "💜 Про на год — 2 990 руб", "payload": "pay_year"}],
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
            await send_message(chat_id, "🧠 AI-Психолог\n\nПродолжаем разговор. Что тебя беспокоит?", psycho_buttons())
        else:
            await send_message(chat_id, "🧠 AI-Психолог\n\nРасскажи что тебя беспокоит прямо сейчас.", psycho_buttons())
        return

    if payload == "diary":
        access = await check_access(user_id, "diary")
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        set_step(user_id, "diary")
        await send_message(chat_id,
            "📔 Личный дневник\n\n"
            "Это твоё личное пространство — только ты и твои мысли.\n\n"
            "Запиши как прошёл день, что на душе.\n"
            "Я тихо выслушаю и задам один вопрос.\n\n"
            "Для советов — используй 🧠 AI-Психолог\n\n"
            "📔 Доступен на тарифе Старт и Про",
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
            "aura_photo": "🔮 Символический анализ ауры по фото\n\nПришли своё фото при хорошем естественном освещении, без фильтров. Разбор носит развлекательный и рефлексивный характер.",
            "chiromancy": "🖐 Хиромантия\n\nПришли фото ладони:\n— Хорошее освещение\n— Ладонь вверх, пальцы расслаблены\n— Лучше правая рука",
            "physio": "😊 Физиогномика\n\nПришли фото лица:\n— Анфас, прямо в камеру\n— Хорошее освещение\n— Без фильтров",
            "grapho": "✍️ Графология\n\nНапиши от руки 5-7 предложений и пришли фото:\n— Пиши как обычно\n— Хорошее освещение",
        }
        await send_message(chat_id, photo_msgs[payload], back_button())
        return

    if payload == "taro_photo":
        plan, _ = get_subscription(user_id)
        if plan != "aura_pro":
            await send_message(chat_id, "🔒 Таро по фото доступно только на тарифе Про.\n\n390 руб/мес:", upgrade_buttons("start"))
            return
        set_step(user_id, "taro_photo")
        await send_message(chat_id, "🃏 Таро по фото карт\n\nВытащи карты и сфотографируй их.\nЯ прочитаю расклад!", back_button())
        return

    if payload == "compat_photo":
        plan, _ = get_subscription(user_id)
        if plan != "aura_pro":
            await send_message(chat_id, "🔒 Совместимость по фото только на тарифе Про.\n\n390 руб/мес:", upgrade_buttons("start"))
            return
        set_step(user_id, "compat_photo")
        await send_message(chat_id, "👫 Совместимость по фото\n\nПришли фото где видны оба человека.", back_button())
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
        }
        name = feature_names.get(payload, payload)
        await send_message(chat_id, f"{name}\n\n{step_buttons[payload]}", back_button())
        return

    await send_message(chat_id, "Выбери действие из меню 👇", main_menu_buttons())

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
        await send_message(chat_id, "Выбери функцию из меню чтобы отправить фото 👇", main_menu_buttons())
        return

    system, feature, limit_field = photo_steps[step]
    access = await check_access(user_id, feature)
    if access not in ("ok", "pro"):
        await handle_limit_msg(chat_id, access)
        return

    await send_message(chat_id, "⏳ Анализирую фото...")
    try:
        image_bytes = await get_photo(photo_url)
        if not image_bytes:
            await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй ещё раз.", back_button())
            return
        result = await generate_with_claude_photo(system, image_bytes)
        set_step(user_id, "idle")
        if access == "ok":
            increment_limit(user_id, "requests" if limit_field == "requests" else limit_field)
            name, uname, _ = get_user_identity(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, name, uname if uname != "—" else ""))
        await send_message(chat_id, result, back_button())
    except Exception as e:
        logging.error(f"Ошибка фото-анализа: {e}")
        await notify_owner("⚠️ Ошибка фото-анализа", user_id, step, str(e))
        await send_message(chat_id, "Ошибка анализа фото. Попробуй ещё раз.", back_button())

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
                            with db_connect() as bonus_conn:
                                bonus_conn.execute(
                                    "UPDATE limits SET requests=MAX(0, requests-3) WHERE user_id=?",
                                    (user_id,)
                                )
            except Exception:
                logging.exception("Ошибка обработки реферала")

        source_map = {
            "channel_taro": "taro",
            "channel_money": "money_code",
            "channel_psycho": "psycho",
            "channel_horoscope": "my_day",
            "channel_day": "my_day",
            "channel_dreams": "dreams",
            "channel_matrix": "matrix",
            "channel_forecast": "forecast_period",
            "channel_love": "compatibility",
            "channel_self": "numerology",
            "channel_diary": "diary",
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

def create_channel_visual(dt, rubric, title):
    if rubric != "value" or dt.weekday() not in VISUAL_DAYS:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
        os.makedirs(VISUAL_DIR, exist_ok=True)
        path = os.path.join(VISUAL_DIR, f"{dt.strftime('%Y%m%d')}_{rubric}.png")
        if os.path.exists(path):
            return path
        img = Image.new("RGB", (1200, 1200), (24, 10, 48))
        draw = ImageDraw.Draw(img)
        for y in range(1200):
            p = y / 1200
            draw.line((0, y, 1200, y), fill=(int(24+30*p), int(10+15*p), int(48+55*p)))
        draw.ellipse((760, -120, 1320, 440), fill=(92, 48, 145))
        draw.ellipse((-180, 760, 380, 1320), fill=(63, 32, 110))
        try:
            font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 76)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
            font_brand = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        except Exception:
            font_big = font_small = font_brand = ImageFont.load_default()
        draw.text((86, 86), "АУРА — ПСИХОЛОГИЯ", font=font_brand, fill=(220, 195, 255))
        words = title.split()
        lines=[]; line=""
        for word in words:
            test=(line+" "+word).strip()
            if draw.textlength(test, font=font_big) > 980 and line:
                lines.append(line); line=word
            else: line=test
        if line: lines.append(line)
        y=390
        for line in lines[:4]:
            draw.text((86,y), line, font=font_big, fill=(255,255,255)); y+=98
        draw.text((86, 1035), "Практика • самопознание • личный разбор", font=font_small, fill=(220, 205, 235))
        img.save(path, quality=94)
        return path
    except Exception as e:
        logging.error(f"Визуал канала: {e}")
        return None


# ========== КАНАЛ TELEGRAM — PREMIUM FUNNEL ==========

CHANNEL_EDITOR_SYSTEM = """Ты главный редактор премиального канала «Аура — Психология».
Канал сочетает практическую психологию, бережное самопознание и символические практики.
Пиши живо, конкретно и современно. Не используй дешёвую мистику, запугивание, фатальные обещания, выдуманные отзывы и гарантии результата.
Каждый пост должен дать узнавание, одну практическую пользу и естественно подвести к персональному продолжению в боте.
Не добавляй ссылки, хэштеги и фразы «перейди по ссылке» — нативную кнопку добавит программа.
Короткие абзацы, только русский язык. Не используй Markdown-разметку: звёздочки, решётки, подчёркивания и обратные кавычки."""

CHANNEL_SLOTS = {(9, 0): "morning", (13, 0): "value", (20, 0): "evening"}

WEEKLY_FUNNEL = {
    0: {"theme": "внутреннее состояние и планы недели", "morning": ("🌟 Получить личный прогноз", "channel_day"), "value": ("🧠 Разобраться в своём состоянии", "channel_psycho"), "evening": ("📔 Подвести итоги дня", "channel_diary")},
    1: {"theme": "деньги, самоценность и реализация", "morning": ("💰 Узнать денежный сценарий", "channel_money"), "value": ("💰 Рассчитать денежный код", "channel_money"), "evening": ("🌟 Получить подсказку на день", "channel_day")},
    2: {"theme": "отношения, границы и близость", "morning": ("❤️ Посмотреть совместимость", "channel_love"), "value": ("❤️ Разобраться в отношениях", "channel_love"), "evening": ("🃏 Задать вопрос картам", "channel_taro")},
    3: {"theme": "тревога, усталость и опора на себя", "morning": ("🧠 Поговорить с психологом", "channel_psycho"), "value": ("🧠 Разложить ситуацию по полочкам", "channel_psycho"), "evening": ("📔 Записать мысли", "channel_diary")},
    4: {"theme": "Таро, выбор и неопределённость", "morning": ("🃏 Получить личный расклад", "channel_taro"), "value": ("🔮 Задать свой вопрос", "channel_taro"), "evening": ("🌙 Разобрать сон", "channel_dreams")},
    5: {"theme": "самопознание, сильные стороны и предназначение", "morning": ("🔢 Получить личный разбор", "channel_self"), "value": ("🌌 Открыть Матрицу судьбы", "channel_matrix"), "evening": ("🔢 Узнать больше о себе", "channel_self")},
    6: {"theme": "итоги недели, восстановление и новый цикл", "morning": ("📅 Получить прогноз недели", "channel_forecast"), "value": ("📔 Подвести итоги недели", "channel_diary"), "evening": ("🌟 Получить подсказку на завтра", "channel_day")},
}

FALLBACK_POSTS = {
    "morning": """🌅 Вопрос на сегодня

Какое одно состояние ты хочешь сохранить в течение дня — спокойствие, ясность или уверенность?

Перед первым важным делом остановись на десять секунд, сделай медленный вдох и назови про себя своё намерение. Это не решит всё сразу, но поможет действовать не из тревоги, а из выбранной опоры.""",
    "value": """🧠 Практика, которая возвращает ясность

Когда мысли ходят по кругу, раздели лист на три части: «что я знаю точно», «что я предполагаю» и «что я чувствую».

Тревога часто смешивает эти три слоя. Когда они разделены, проще увидеть, где нужны действия, а где — поддержка и время.""",
    "evening": """🌙 Вечерняя перезагрузка

Перед сном назови три вещи: что сегодня получилось, что забрало силы и что можно не нести с собой в завтра.

Не оценивай день целиком как хороший или плохой. Один сложный момент не отменяет всего остального.""",
}

def channel_deep_link(payload):
    return deep_link(payload)

def native_channel_keyboard(button_text, start_payload):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=button_text[:64], url=channel_deep_link(start_payload))
    ]])

async def send_to_channel(text, button_text, start_payload, image_path=None):
    keyboard = native_channel_keyboard(button_text, start_payload)
    if image_path and os.path.exists(image_path):
        from aiogram.types import FSInputFile
        return await bot.send_photo(
            CHANNEL_ID, FSInputFile(image_path), caption=clean_display_text(text)[:1024],
            reply_markup=keyboard
        )
    return await bot.send_message(
        CHANNEL_ID, clean_display_text(text)[:4096], reply_markup=keyboard, disable_web_page_preview=True
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
               rubric=excluded.rubric,
               topic=excluded.topic,
               content=excluded.content,
               status=excluded.status,
               published_at=excluded.published_at""",
            (key, rubric, topic[:250], content[:4096], status, datetime.now().isoformat()),
        )

def recent_channel_topics(limit=24):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT rubric,topic FROM channel_posts WHERE status='sent' ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return "\n".join(f"- {rubric}: {topic}" for rubric, topic in rows if topic)

def extract_topic(text):
    return " ".join((text or "").replace("\n", " ").split())[:220]

def build_channel_prompt(dt, rubric):
    theme = WEEKLY_FUNNEL[dt.weekday()]["theme"]
    recent = recent_channel_topics()
    avoid = f"\nНедавние темы, которые нельзя повторять:\n{recent}" if recent else ""
    if rubric == "morning":
        task = f"""Тема дня: {theme}. Напиши утренний пост до 900 знаков.
Структура: сильная первая строка, узнаваемая мысль, одна практика на 30–60 секунд и вопрос читателю.
Не составляй общий гороскоп на 12 знаков и не обещай конкретных событий."""
    elif rubric == "value":
        task = f"""Тема дня: {theme}. Напиши главный полезный пост до 1700 знаков.
Начни с жизненной ситуации, в которой читатель узнает себя. Объясни смысл простыми словами.
Дай 2–3 конкретных шага или вопроса для саморефлексии. Заверши персональным вопросом, который естественно продолжить в боте.
Не имитируй отзыв пользователя и не обещай магический результат."""
    else:
        task = f"""Тема дня: {theme}. Напиши вечерний пост до 1000 знаков: мягкое завершение дня, практика на 2–4 минуты и один точный вопрос для дневника.
Избегай абстрактных фраз про свет, потоки энергии и вселенские знаки."""
    return task + avoid

async def generate_channel_post(dt, rubric):
    try:
        text = await generate_text(CHANNEL_EDITOR_SYSTEM, build_channel_prompt(dt, rubric))
        text = clean_display_text(text)
        if len(text) < 120:
            raise RuntimeError("слишком короткий пост")
        return text
    except Exception as e:
        logging.error(f"Канал: генерация {rubric} не удалась: {e}")
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
    except Exception as e:
        save_channel_post(key, rubric, extract_topic(text), text, "failed")
        logging.exception(f"Ошибка публикации {key}: {e}")
        await notify_owner("⚠️ Не вышел пост в Telegram-канале", 0, rubric, f"{key}: {e}")
        return False

async def publish_channel_intro():
    text = """🔮 Добро пожаловать в «Аура — Психология»

Здесь не обещают предсказать жизнь одной фразой. Канал помогает лучше слышать себя, замечать повторяющиеся сценарии и принимать решения спокойнее.

Что будет в канале:

🧠 практическая психология без сложных терминов;
❤️ отношения, границы и самоценность;
💰 деньги, реализация и внутренние опоры;
🃏 Таро и символические практики как способ посмотреть на ситуацию с другой стороны;
🌙 вечерние вопросы и упражнения для возвращения к себе.

В канале ты получаешь полезную мысль. В AuraBot — персональное продолжение именно под твою ситуацию.

Первый личный разбор можно начать бесплатно."""
    try:
        await send_to_channel(text, "🎁 Получить первый личный разбор", "channel_self")
        return True
    except Exception as e:
        logging.exception(f"Не удалось опубликовать intro: {e}")
        return False

async def channel_posting_loop():
    await asyncio.sleep(5)
    while True:
        now = datetime.now(MOSCOW)
        try:
            for (hour, minute), rubric in CHANNEL_SLOTS.items():
                slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                lateness = (now - slot).total_seconds()
                if 0 <= lateness <= 21600:
                    await publish_channel_slot(slot, rubric)
                    await asyncio.sleep(2)

            candidates = []
            for (hour, minute), rubric in CHANNEL_SLOTS.items():
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                candidates.append((candidate, rubric))

            next_dt, next_rubric = min(candidates, key=lambda item: item[0])
            await asyncio.sleep(max(1, (next_dt - now).total_seconds()))
            await publish_channel_slot(next_dt, next_rubric)
        except Exception as e:
            logging.exception(f"Channel loop: {e}")
            await asyncio.sleep(60)

# ========== MAIN ==========
async def main():
    init_db()
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(daily_loop())
    asyncio.create_task(channel_posting_loop())
    logging.info("Aura Telegram Bot запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
