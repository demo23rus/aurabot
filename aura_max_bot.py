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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
import anthropic
import gspread
from google.oauth2.service_account import Credentials

# ========== КОНФИГ ==========
MAX_TOKEN = "f9LHodD0cOKQMa6aUXu2uNfUQu8nnfZcgZ7c0X8aUUwrz1XCbBY18pNaP0FDdHV7s89tHIIpuN78bVpdyzjQ"
MAX_API = "https://platform-api.max.ru"
OPENAI_KEY = "sk-mfvVI3QN2uQvXPlhMkAeUUzmbjK5aQzj"
CLAUDE_KEY = "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"
OWNER_ID = 549639607
SUPPORT_URL = "https://t.me/Boss023rus"

# Лимиты
FREE_REQUESTS = 5
FREE_PSYCHO = 15
START_PSYCHO = 100
START_PHOTO = 5

# ЮКасса
YOOKASSA_SHOP_ID = "1363324"
YOOKASSA_SECRET = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"

# ========== GOOGLE SHEETS ==========
GOOGLE_CREDS_PATH = "/root/google_credentials.json"
SPREADSHEET_NAME = "PostGenius Users"
SHEET_NAME = "АураМакс"

def get_gsheet():
    try:
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open(SPREADSHEET_NAME)
        try:
            return spreadsheet.worksheet(SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=10)
            ws.append_row(["Дата", "user_id", "Имя", "Username", "Тариф", "Отзыв"])
            return ws
    except Exception as e:
        logging.error(f"Ошибка Google Sheets: {e}")
        return None

def sheets_log_visit(user_id, first_name, username, plan):
    try:
        ws = get_gsheet()
        if ws:
            ws.append_row([
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                str(user_id),
                first_name or "",
                username or "",
                plan or "бесплатный",
                ""
            ])
    except Exception as e:
        logging.error(f"Ошибка записи посещения в Sheets: {e}")

def sheets_log_review(user_id, first_name, username, review_text):
    try:
        ws = get_gsheet()
        if not ws:
            return
        col_user = ws.col_values(2)
        uid_str = str(user_id)
        last_row = None
        for i, val in enumerate(col_user):
            if val == uid_str:
                last_row = i + 1
        if last_row:
            ws.update_cell(last_row, 6, review_text)
        else:
            ws.append_row([
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                uid_str,
                first_name or "",
                username or "",
                "",
                review_text
            ])
    except Exception as e:
        logging.error(f"Ошибка записи отзыва в Sheets: {e}")

# ========== ЛОГИ ==========
logging.basicConfig(level=logging.INFO)

# ========== КЛИЕНТЫ AI ==========
openai_client = AsyncOpenAI(api_key=OPENAI_KEY, base_url="https://api.proxyapi.ru/openai/v1")
claude_client = anthropic.Anthropic(api_key=CLAUDE_KEY)

# ========== MAX API ==========
async def send_message(chat_id, text, buttons=None):
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    payload = {"text": text[:4000]}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{MAX_API}/messages?chat_id={chat_id}", json=payload, headers=headers)
        logging.info(f"send_message chat_id={chat_id}: {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"MAX send error {r.status_code}: {r.text[:500]}")
        try: return r.json()
        except Exception: return {"ok": True}

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
        [{"type": "link", "text": "💬 Поддержка", "url": SUPPORT_URL}],
    ]

def category_buttons(category):
    menus = {
        "situation": [
            [{"type":"callback","text":"🃏 Таро на ситуацию","payload":"taro"}],
            [{"type":"callback","text":"💤 Толкование сна","payload":"dreams"}],
            [{"type":"callback","text":"📅 Прогноз на период","payload":"forecast"}],
        ],
        "love": [
            [{"type":"callback","text":"❤️ Совместимость по датам","payload":"compatibility"}],
            [{"type":"callback","text":"👫 Совместимость по фото","payload":"compat_photo"}],
            [{"type":"callback","text":"🃏 Таро на отношения","payload":"taro"}],
        ],
        "money": [
            [{"type":"callback","text":"💰 Денежный код","payload":"money_code"}],
            [{"type":"callback","text":"🌌 Матрица судьбы","payload":"matrix"}],
            [{"type":"callback","text":"📊 Прогноз на год","payload":"forecast"}],
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
DB = "/root/aura_max.db"

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
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT plan, sub_end FROM subscriptions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[1]:
        return None, None
    sub_end = datetime.fromisoformat(row[1])
    if sub_end > datetime.now():
        return row[0], sub_end
    return None, None

def set_subscription(user_id, plan, days):
    conn = sqlite3.connect(DB, timeout=10)
    now = datetime.now()
    current = conn.execute("SELECT plan, sub_end FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    base = now
    if current and current[1]:
        try:
            old_end = datetime.fromisoformat(current[1])
            if old_end > now and current[0] == plan:
                base = old_end
        except Exception:
            pass
    end = (base + timedelta(days=days)).isoformat()
    conn.execute("INSERT OR REPLACE INTO subscriptions (user_id, plan, sub_end) VALUES (?,?,?)", (user_id, plan, end))
    conn.execute("UPDATE limits SET requests=0, psycho_messages=0, photo_chiromancy=0, photo_physio=0, photo_grapho=0 WHERE user_id=?", (user_id,))
    conn.execute("INSERT OR REPLACE INTO usage_periods (user_id, period_start, period_end, requests, psycho, photo) VALUES (?,?,?,?,?,?)",
                 (user_id, now.isoformat(), end, 0, 0, 0))
    conn.commit(); conn.close()

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

    if feature in ("compat_photo", "taro_photo", "money_code", "aura_photo"):
        return "start_block"

    if feature in ("matrix", "forecast", "natal"):
        if plan == "aura_start":
            return "start_block"
        return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

    photo_map = {"chiromancy": "chiromancy", "physio": "physio", "grapho": "grapho", "aura_photo": "aura_photo"}
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
    return response.choices[0].message.content

async def generate_with_history(system, history, new_message):
    messages = [{"role": "system", "content": system}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": new_message})
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, max_tokens=1500
    )
    return response.choices[0].message.content

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
    return response.content[0].text

# ========== ОПЛАТА ==========
async def create_payment(user_id, plan):
    prices = {"aura_start": ("190.00", "Старт", 30), "aura_pro": ("390.00", "Про", 30), "aura_pro_year": ("2990.00", "Про на год", 365)}
    amount, plan_name, days = prices.get(plan, prices["aura_pro"])
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://aurahelper.ru/payment/success"},
                "capture": True,
                "description": f"AuraBot MAX Тариф {plan_name} — {user_id}",
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": f"AuraBot Тариф {plan_name} {days} дней",
                    "quantity": "1.00",
                    "amount": {"value": amount, "currency": "RUB"},
                    "vat_code": 1, "payment_subject": "service", "payment_mode": "full_payment"
                }]},
                "metadata": {"user_id": user_id, "plan": plan}
            },
            headers={"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET)
        )
        return r.json()

# ========== ФОНОВАЯ ПРОВЕРКА ОПЛАТЫ ==========
async def check_payments_loop():
    while True:
        await asyncio.sleep(15)
        try:
            for payment_id, user_id, plan in get_pending_payments():
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(
                            f"https://api.yookassa.ru/v3/payments/{payment_id}",
                            auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET)
                        )
                        payment = r.json()
                    if payment.get("status") == "succeeded":
                        activation_plan = "aura_pro" if plan == "aura_pro_year" else plan
                        activation_days = 365 if plan == "aura_pro_year" else 30
                        set_subscription(user_id, activation_plan, activation_days)
                        log_event(user_id, "payment_succeeded", feature=plan, value=payment_id)
                        with db_connect() as conn:
                            ref=conn.execute("SELECT referrer_id FROM user_profiles WHERE user_id=?",(user_id,)).fetchone()
                        if ref and ref[0]:
                            set_subscription(int(ref[0]), "aura_pro", 30)
                            try: await send_message(int(ref[0]), "🎁 Твой приглашённый друг оформил подписку. Тебе начислено 30 дней Аура Про!", main_menu_buttons())
                            except Exception: pass
                        delete_pending_payment(payment_id)
                        plan_name = "🟢 Старт" if plan == "aura_start" else ("💜 Про на год" if plan == "aura_pro_year" else "🔥 Про")
                        await send_message(user_id,
                            f"✅ Оплата прошла!\n\nТариф {plan_name} активирован на 30 дней.\n\nПользуйся на здоровье! 🔮",
                            main_menu_buttons()
                        )
                    elif payment.get("status") == "canceled":
                        delete_pending_payment(payment_id)
                        await send_message(user_id, "❌ Платёж отменён. Попробуй снова.", main_menu_buttons())
                except Exception as e:
                    logging.error(f"Ошибка проверки платежа {payment_id}: {e}")
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
BOT_LINK = "https://max.ru/id232007136009_bot"
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
    share=f"https://max.ru/:share?text={quote('Попробуй AuraMAX — личные разборы, Таро и AI-психолог: '+link)}"
    return [[{"type":"link","text":"📨 Поделиться","url":share}], [{"type":"callback","text":"🔙 В меню","payload":"back_menu"}]]

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
WELCOME_TEXT = """🔮 {name}, добро пожаловать в AuraMAX.

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
        await send_message(chat_id, response, psycho_buttons())
        return

    step_map = {
        "numerology": (NUMEROLOGY_SYSTEM, f"Дата рождения: {{text}}\n\nРассчитай числа судьбы, личности, души. Объясни что означает для этого человека."),
        "matrix": (MATRIX_SYSTEM, "Дата рождения: {text}\n\nРассчитай Матрицу Судьбы. Расскажи о кармических задачах, предназначении, талантах."),
        "taro": (TARO_SYSTEM, "Вопрос: {text}\n\nВытащи 3 карты Таро. Расклад: прошлое, настоящее, будущее. Расскажи что означают."),
        "dreams": (DREAMS_SYSTEM, "Сон: {text}\n\nДай толкование психологическое и эзотерическое. Говори конкретно."),
        "aura": (AURA_SYSTEM, "Дата рождения: {text}\n\nРасскажи об ауре: цвет, энергетика, сильные стороны, уязвимости."),
        "forecast": (FORECAST_SYSTEM, "Данные: {text}\n\nСоставь нумерологический прогноз на период."),
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
        feature = step if step in ("matrix", "forecast", "natal", "money_code", "taro_photo", "compat_photo") else "general"
        access = await check_access(user_id, feature)
        if access not in ("ok", "pro"):
            await handle_limit_msg(chat_id, access)
            return
        system, prompt_tpl = step_map[step]
        prompt = prompt_tpl.replace("{text}", text)
        set_step(user_id, "idle")
        await send_message(chat_id, "⏳ Анализирую...")
        result = await generate_text(system, prompt)
        if access == "ok":
            increment_limit(user_id, "requests")
        await send_message(chat_id, result, back_button())
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

    if payload in ("pay_start", "pay_pro", "pay_year"):
        plan = {"pay_start":"aura_start", "pay_pro":"aura_pro", "pay_year":"aura_pro_year"}[payload]
        try:
            payment = await create_payment(user_id, plan)
            pay_url = payment.get("confirmation", {}).get("confirmation_url", "")
            payment_id = payment.get("id", "")
            if pay_url and payment_id:
                save_pending_payment(payment_id, user_id, plan)
                log_event(user_id, "payment_created", feature=plan, value=payment_id)
                plan_name = {"aura_start":"Старт 190 руб", "aura_pro":"Про 390 руб", "aura_pro_year":"Про на год 2 990 руб"}[plan]
                await send_message(chat_id,
                    f"💳 Оплата тарифа {plan_name}\n\nНажми кнопку для оплаты.\nПодписка активируется автоматически! ✅",
                    [[{"type": "link", "text": f"💳 Оплатить", "url": pay_url}],
                     [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]]
                )
            else:
                await send_message(chat_id, f"❌ Ошибка при создании платежа. Обратись в поддержку: {SUPPORT_URL}", back_button())
        except Exception as e:
            logging.error(f"Ошибка платежа: {e}")
            await send_message(chat_id, f"❌ Ошибка платежа. Обратись в поддержку: {SUPPORT_URL}", back_button())
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
        "forecast": "Введи дату рождения и период:\nНапример: 15.03.1990, месяц",
        "compatibility": "Введи данные обоих людей:\nМария 15.03.1990 Александр 22.07.1988",
        "natal": "Введи дату, время и место рождения:\n15.03.1990 14:30 Москва",
        "horoscope": "Напиши свой знак зодиака:\nНапример: Телец, Скорпион, Водолей",
        "money_code": "Введи своё полное имя и дату рождения:\nМария Иванова 15.03.1990",
    }

    pro_features = ("matrix", "forecast", "natal", "money_code", "taro_photo", "compat_photo")
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
            await handle_limit_msg(chat_id, access)
            return

    if payload in step_buttons:
        set_step(user_id, payload)
        feature_names = {
            "numerology": "🔢 Нумерология", "matrix": "🌌 Матрица судьбы",
            "taro": "🃏 Таро", "dreams": "💤 Толкование снов",
            "aura": "🌈 Аура", "forecast": "📅 Прогноз",
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
        await send_message(chat_id, result, back_button())
    except Exception as e:
        logging.error(f"Ошибка фото-анализа: {e}")
        await send_message(chat_id, "Ошибка анализа фото. Попробуй ещё раз.", back_button())

# ========== FASTAPI WEBHOOK ==========
WEBHOOK_URL = "https://aurahelper.ru/webhook"

app = FastAPI()

@app.on_event("startup")
async def startup():
    init_db()
    headers={"Authorization":MAX_TOKEN,"Content-Type":"application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r=await client.post(f"{MAX_API}/subscriptions", json={"url":WEBHOOK_URL,"update_types":["message_created","message_callback","bot_started","bot_stopped"]}, headers=headers)
            logging.info(f"Webhook регистрация: {r.status_code} {r.text[:300]}")
    except Exception as e: logging.error(f"Webhook registration: {e}")
    for _ in range(3): asyncio.create_task(update_worker())
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(channel_posting_loop())
    asyncio.create_task(daily_loop())
    logging.info("Aura MAX Bot запущен")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data=await request.json()
        key=str(data.get("timestamp", ""))+":"+data.get("update_type","")+":"+str(data.get("message",{}).get("body",{}).get("mid",data.get("callback",{}).get("callback_id","")))
        if not mark_update(key): return JSONResponse({"ok":True,"duplicate":True})
        try: UPDATE_QUEUE.put_nowait(data)
        except asyncio.QueueFull:
            logging.error("Update queue full")
            return JSONResponse({"ok":False},status_code=503)
    except Exception as e:
        logging.error(f"Webhook parse error: {e}")
    return JSONResponse({"ok":True})

async def update_worker():
    while True:
        data=await UPDATE_QUEUE.get()
        try: await handle_update(data)
        except Exception as e: logging.exception(f"Worker error: {e}")
        finally: UPDATE_QUEUE.task_done()

async def handle_update(data):
    update_type=data.get("update_type",""); message=data.get("message",{}); callback=data.get("callback",{})
    if update_type=="bot_started":
        user=data.get("user",{}); chat_id=data.get("chat_id") or user.get("user_id"); user_id=user.get("user_id") or chat_id
        first_name=user.get("name","друг"); username=user.get("username",""); payload=data.get("payload") or "direct"
        get_user(user_id,username,first_name); set_step(user_id,"idle"); save_profile(user_id,source=payload,stopped=False)
        if payload.startswith("ref_"):
            try: save_profile(user_id,referrer_id=int(payload.split("_",1)[1]))
            except Exception: pass
        log_event(user_id,"bot_started",source=payload)
        asyncio.create_task(asyncio.to_thread(sheets_log_visit,user_id,first_name,username,get_subscription(user_id)[0]))
        routes={"channel_taro":"taro","channel_money":"money_code","channel_psycho":"psycho","channel_love":"compatibility","channel_self":"numerology","channel_day":"my_day"}
        target=routes.get(payload)
        if target:
            await send_message(chat_id,WELCOME_TEXT.format(name=first_name),main_menu_buttons())
            await process_callback(chat_id,user_id,target,first_name)
        else: await send_message(chat_id,WELCOME_TEXT.format(name=first_name),main_menu_buttons())
    elif update_type=="bot_stopped":
        user=data.get("user",{}); uid=user.get("user_id") or data.get("chat_id")
        if uid: save_profile(uid,stopped=True); log_event(uid,"bot_stopped")
    elif update_type=="message_created":
        sender=message.get("sender",{}); chat_id=message.get("recipient",{}).get("chat_id") or data.get("chat_id")
        uid=sender.get("user_id"); body=message.get("body",{}); text=body.get("text",""); attachments=body.get("attachments",[])
        for att in attachments:
            if att.get("type")=="image":
                pd=att.get("payload",{}); url=pd.get("url") or pd.get("photo_url") or ((pd.get("photos") or [{}])[0].get("url"))
                if url: await process_photo(chat_id,uid,url); return
        if text: await process_command(chat_id,uid,text,sender.get("username",""),sender.get("name","друг"))
    elif update_type=="message_callback":
        user=callback.get("user",{}); uid=user.get("user_id"); chat_id=message.get("recipient",{}).get("chat_id") or callback.get("chat_id") or data.get("chat_id")
        payload=callback.get("payload","")
        if chat_id and payload: await process_callback(chat_id,uid,payload,user.get("name","друг"))

@app.get("/payment/success")
async def payment_success():
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Оплата прошла — AuraBot</title>
<style>
  body { font-family: Arial, sans-serif; text-align: center; padding: 60px 20px; background: #1a0533; color: #e8d5ff; }
  .icon { font-size: 64px; margin-bottom: 20px; }
  h1 { font-size: 28px; margin-bottom: 12px; color: #c084fc; }
  p { font-size: 16px; color: #d8b4fe; line-height: 1.6; }
</style>
</head>
<body>
  <div class="icon">🔮</div>
  <h1>Оплата прошла!</h1>
  <p>Твоя подписка активирована.<br>Возвращайся в бот и пользуйся!</p>
  <p>Бот → <strong>AuraBot</strong></p>
</body>
</html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)

@app.get("/health")
async def health():
    return {"status": "ok"}

# ========== КАНАЛ MAX ==========
MAX_CHANNEL_ID = -75554451158515

CHANNEL_SYSTEM_MORNING = """Ты — мудрый эзотерик и духовный наставник канала Аура — Психология.
Пишешь вдохновляющий утренний пост. Каждый раз выбирай НОВУЮ тему — не повторяй предыдущие.
Темы для ротации: энергия дня, лунный день, цвет дня, число дня, архетип дня, стихия дня, послание вселенной.
Пост: начинается с красивого эмодзи, содержит мудрость или цитату, даёт энергию на день.
Стиль: тёплый, вдохновляющий. 3-4 предложения. Только на русском. Без хэштегов."""

CHANNEL_SYSTEM_HOROSCOPE = """Ты — профессиональный астролог канала Аура — Психология.
Пишешь краткий гороскоп на сегодня для всех 12 знаков зодиака.
Для каждого знака: 1-2 предложения — конкретный совет или прогноз на день.
Каждый день РАЗНЫЕ темы: работа, отношения, деньги, здоровье, интуиция, творчество — чередуй.
Формат строго такой:
♈ Овен — [текст]
♉ Телец — [текст]
♊ Близнецы — [текст]
♋ Рак — [текст]
♌ Лев — [текст]
♍ Дева — [текст]
♎ Весы — [текст]
♏ Скорпион — [текст]
♐ Стрелец — [текст]
♑ Козерог — [текст]
♒ Водолей — [текст]
♓ Рыбы — [текст]
Только на русском. Без хэштегов."""

CHANNEL_SYSTEM_PSYCHO = """Ты — опытный психолог и коуч канала Аура — Психология.
Пишешь развёрнутый дневной совет. Каждый раз НОВАЯ тема — не повторяй предыдущие.
Темы для ротации: границы в общении, работа со страхами, самооценка, токсичные отношения, 
выгорание, принятие себя, детские травмы, тревожность, одиночество, прокрастинация, обиды, ревность.
Совет: практичный, жизненный, с конкретным упражнением на сегодня.
Стиль: профессиональный но тёплый. 6-8 предложений. Без хэштегов."""

CHANNEL_SYSTEM_EVENING = """Ты — мудрый астролог и эзотерик канала Аура — Психология.
Пишешь вечерний пост. Каждый раз НОВАЯ тема — не повторяй предыдущие.
Темы для ротации: медитация на ночь, аффирмация, практика благодарности, отпускание дня, 
лунная энергия, подведение итогов, намерение на завтра, очищение энергии.
Пост: помогает отпустить день, настраивает на сон, даёт практику.
Стиль: мягкий, успокаивающий. 4-5 предложений. Без хэштегов."""

CHANNEL_SYSTEM_TARO = """Ты — профессиональный таролог канала Аура — Психология.
Каждый день вытягиваешь одну карту Таро и объясняешь её значение на сегодня.
Каждый день РАЗНАЯ карта — не повторяй карты которые уже были.
Структура поста:
— Название карты и её аркан
— Что эта карта означает сегодня для всех
— Совет от карты на день
— Одна короткая аффирмация
Стиль: мистический, вдохновляющий, конкретный. Без хэштегов. Только на русском."""

CHANNEL_SYSTEM_LUNAR = """Ты — астролог и эзотерик канала Аура — Психология.
Пишешь пост о лунном календаре на текущую неделю.
Структура:
— Текущая фаза луны и её влияние
— Благоприятные дни недели для разных дел (финансы, отношения, начинания, отдых)
— Главный совет недели от луны
Стиль: практичный, конкретный, с эмодзи. Без хэштегов. Только на русском.
В конце добавь: "Сохрани чтобы не потерять 🔖" """

CHANNEL_SYSTEM_MONEY = """Ты — эзотерик и нумеролог канала Аура — Психология.
Пишешь пост о деньгах и энергетике для разных знаков зодиака или типов людей.
Каждый раз НОВАЯ тема: денежные блоки по знакам, денежные аффирмации, практики привлечения изобилия,
что мешает деньгам приходить, ритуалы на деньги по лунному календарю.
Стиль: практичный, вдохновляющий. В конце мягкий призыв узнать личный разбор в боте.
Только на русском. Без хэштегов."""

CHANNEL_SYSTEM_AFFIRMATION = """Ты — коуч и эзотерик канала Аура — Психология.
Пишешь пост с аффирмациями на каждый день недели (7 аффирмаций).
Каждый раз НОВАЯ тема аффирмаций: любовь к себе, изобилие, здоровье, отношения, уверенность, защита, успех.
Формат: один эмодзи + день недели + аффирмация.
В конце: "Сохрани и начинай каждое утро с аффирмации своего дня 🌅"
Только на русском. Без хэштегов."""

CHANNEL_SYSTEM_DREAMS = """Ты — эзотерик и толкователь снов канала Аура — Психология.
Пишешь пост о значении символов во снах или знаках которые посылает вселенная.
Каждый раз НОВАЯ тема: символы во снах, знаки от вселенной в жизни, совпадения не случайны,
ангельские числа, знаки что ты на правильном пути.
Структура: 7-10 символов с кратким объяснением.
В конце: "Сохрани себе 🔖"
Только на русском. Без хэштегов."""

CHANNEL_SYSTEM_REVIEW = """Ты — администратор канала Аура — Психология.
Пишешь пост о возможностях личного эзотерического наставника в боте.
Каждый раз выбирай ОДНУ функцию и раскрой её подробно:
анализ ауры по фото, карты Таро, персональный гороскоп, нумерология, матрица судьбы, психологическая поддержка.
Пиши от лица пользователя — как будто делишься опытом ("я попробовала...").
В конце мягкий призыв попробовать бесплатно.
Упоминай https://max.ru/id232007136009_bot.
Стиль: живой, искренний. Без хэштегов. Только на русском."""

CHANNEL_SYSTEM_POLL = """Ты — администратор канала Аура — Психология.
Пишешь вовлекающий пост с мини-тестом где расшифровка дана СРАЗУ в том же посте.
Каждый раз НОВАЯ тема: выбери карту и узнай послание, выбери цвет и узнай свою энергию,
выбери символ и узнай что тебя ждёт, выбери стихию и узнай свой тип, выбери камень и узнай свою силу.
Формат строго такой:
— Вступление с вопросом (1-2 предложения)
— 3 варианта на выбор с эмодзи
— Разделитель (например: ✨ Расшифровка ✨)
— Расшифровка каждого варианта (2-3 предложения на каждый)
— Финальная фраза с призывом попробовать бота: "Хочешь личный разбор именно для тебя? → https://max.ru/id232007136009_bot"
Стиль: лёгкий, игривый, мистический. Только на русском. Без хэштегов."""

async def send_to_channel(text):
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    payload = {"text": text[:4000]}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{MAX_API}/messages?chat_id={MAX_CHANNEL_ID}", json=payload, headers=headers)
        logging.info(f"Канал MAX: {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"MAX channel error {r.status_code}: {r.text[:500]}")

CHANNEL_SLOTS = {
    (9,0): "morning", (13,0): "value", (20,0): "evening"
}

def channel_slot_key(dt, rubric): return f"{dt.strftime('%Y-%m-%d')}_{rubric}"

def channel_was_sent(key):
    with db_connect() as conn: return bool(conn.execute("SELECT 1 FROM channel_posts WHERE slot_key=? AND status='sent'",(key,)).fetchone())

def save_channel_post(key,rubric,content,status):
    with db_connect() as conn:
        conn.execute("INSERT INTO channel_posts(slot_key,rubric,content,status,published_at) VALUES(?,?,?,?,?) ON CONFLICT(slot_key) DO UPDATE SET content=excluded.content,status=excluded.status,published_at=excluded.published_at",
                     (key,rubric,content,status,datetime.now().isoformat()))

def recent_channel_topics(rubric,limit=10):
    with db_connect() as conn: rows=conn.execute("SELECT content FROM channel_posts WHERE rubric=? AND status='sent' ORDER BY id DESC LIMIT ?",(rubric,limit)).fetchall()
    return "\n---\n".join(r[0][:250] for r in rows)

async def publish_channel_slot(dt,rubric):
    key=channel_slot_key(dt,rubric)
    if channel_was_sent(key): return
    today=dt.strftime('%d.%m.%Y'); recent=recent_channel_topics(rubric)
    weekday=dt.weekday()
    if rubric=="morning":
        theme=["отношения","деньги","уверенность","интуиция","энергия","границы","новые решения"][weekday]
        text=await generate_text(CHANNEL_SYSTEM_MORNING,f"Дата {today}. Тема дня: {theme}. Напиши короткий цепляющий пост: узнаваемая мысль, одна конкретная практика и вопрос подписчику. Не повторяй недавнее: {recent}")
        cta=f"\n\n✨ Получить личную подсказку на сегодня → {deep_link('channel_day')}"
        title="🌅 Настрой на день"
    elif rubric=="value":
        systems=[CHANNEL_SYSTEM_PSYCHO,CHANNEL_SYSTEM_MONEY,CHANNEL_SYSTEM_PSYCHO,CHANNEL_SYSTEM_REVIEW,CHANNEL_SYSTEM_DREAMS,CHANNEL_SYSTEM_PSYCHO,CHANNEL_SYSTEM_POLL]
        prompts=["границы и самоценность","денежные привычки без магических обещаний","тревога и опора на себя","покажи одну функцию бота честно, без выдуманного отзыва","символы снов как повод для рефлексии","отношения и уважение к себе","мини-тест с расшифровкой"]
        text=await generate_text(systems[weekday],f"Сегодня {today}. Тема: {prompts[weekday]}. Дай практическую ценность, без ложных гарантий. Не повторяй недавнее: {recent}")
        payload=["channel_self","channel_money","channel_psycho","channel_self","channel_taro","channel_love","channel_taro"][weekday]
        cta=f"\n\n🔮 Продолжить с личным разбором → {deep_link(payload)}"
        title=["🧠 Практика недели","💰 Деньги и внутренние опоры","🧠 Психология без сложных слов","✨ Как работает AuraMAX","🌙 Язык снов","❤️ Отношения с собой","🃏 Мини-тест"][weekday]
    else:
        text=await generate_text(CHANNEL_SYSTEM_EVENING,f"Сегодня {today}. Сделай мягкую вечернюю практику на 2-4 минуты и один вопрос для дневника. Не повторяй недавнее: {recent}")
        cta=f"\n\n📔 Сохранить мысли в личном дневнике → {deep_link('channel_psycho')}"
        title="🌙 Вечерняя перезагрузка"
    content=f"{title}\n\n{text}{cta}"
    try:
        await send_to_channel(content); save_channel_post(key,rubric,content,"sent")
    except Exception:
        save_channel_post(key,rubric,content,"failed"); raise

async def channel_posting_loop():
    await asyncio.sleep(5)
    while True:
        now=datetime.now(MOSCOW)
        try:
            # catch up only today's missed slots, no more than 6 hours late
            for (h,m),rubric in CHANNEL_SLOTS.items():
                slot=now.replace(hour=h,minute=m,second=0,microsecond=0)
                if slot<=now and (now-slot).total_seconds()<=21600:
                    await publish_channel_slot(slot,rubric)
            # calculate next slot
            candidates=[]
            for (h,m),rubric in CHANNEL_SLOTS.items():
                d=now.replace(hour=h,minute=m,second=0,microsecond=0)
                if d<=now: d+=timedelta(days=1)
                candidates.append((d,rubric))
            nxt,rubric=min(candidates,key=lambda x:x[0])
            await asyncio.sleep(max(1,(nxt-now).total_seconds()))
            await publish_channel_slot(nxt,rubric)
        except Exception as e:
            logging.exception(f"Channel loop: {e}"); await asyncio.sleep(60)

# ========== MAIN ==========
async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
