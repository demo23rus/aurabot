import asyncio
import sqlite3
import logging
import uuid
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
FREE_REQUESTS = 15
FREE_PSYCHO = 30
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
        return r.json()

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
        [{"type": "callback", "text": "🔢 Нумерология", "payload": "numerology"},
         {"type": "callback", "text": "🃏 Таро", "payload": "taro"}],
        [{"type": "callback", "text": "💤 Сны", "payload": "dreams"},
         {"type": "callback", "text": "🌈 Аура", "payload": "aura"}],
        [{"type": "callback", "text": "🌟 Гороскоп", "payload": "horoscope"},
         {"type": "callback", "text": "❤️ Совместимость", "payload": "compatibility"}],
        [{"type": "callback", "text": "🧠 AI-Психолог", "payload": "psycho"},
         {"type": "callback", "text": "📔 Личный дневник", "payload": "diary"}],
        [{"type": "callback", "text": "🖐 Хиромантия", "payload": "chiromancy"},
         {"type": "callback", "text": "😊 Физиогномика", "payload": "physio"}],
        [{"type": "callback", "text": "✍️ Графология", "payload": "grapho"}],
        [{"type": "callback", "text": "🔥 ПРО ФУНКЦИИ 🔥", "payload": "noop"}],
        [{"type": "callback", "text": "🌌 Матрица судьбы", "payload": "matrix"},
         {"type": "callback", "text": "📅 Прогноз", "payload": "forecast"}],
        [{"type": "callback", "text": "♈ Натальная карта", "payload": "natal"},
         {"type": "callback", "text": "💰 Денежный код", "payload": "money_code"}],
        [{"type": "callback", "text": "🃏 Таро по фото", "payload": "taro_photo"},
         {"type": "callback", "text": "👫 Совместимость фото", "payload": "compat_photo"}],
        [{"type": "callback", "text": "💎 Тарифы и оплата", "payload": "tariffs"}],
        [{"type": "callback", "text": "⭐️ Оставить отзыв", "payload": "review"}],
        [{"type": "link", "text": "💬 Поддержка", "url": SUPPORT_URL}],
    ]

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
    c.execute("""CREATE TABLE IF NOT EXISTS channel_posts (
        slot_key TEXT PRIMARY KEY,
        rubric TEXT NOT NULL,
        topic TEXT DEFAULT '',
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        published_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        clicked_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_clicks_source ON channel_clicks(source)")
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
    conn = sqlite3.connect(DB)
    end = (datetime.now() + timedelta(days=days)).isoformat()
    conn.execute("INSERT OR REPLACE INTO subscriptions (user_id, plan, sub_end) VALUES (?,?,?)",
                 (user_id, plan, end))
    conn.commit()
    conn.close()

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

    if feature in ("compat_photo", "taro_photo", "money_code"):
        return "start_block"

    if feature in ("matrix", "forecast", "natal"):
        if plan == "aura_start":
            return "start_block"
        return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

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
PHYSIO_SYSTEM = "Ты опытный физиогномист. Смотришь на фото лица и рассказываешь о характере конкретно. Пишешь только на русском. Никаких звёздочек и решёток."
GRAPHO_SYSTEM = "Ты опытный графолог. Смотришь на фото почерка и рассказываешь о характере конкретно. Пишешь только на русском. Никаких звёздочек и решёток."
TARO_PHOTO_SYSTEM = "Ты опытный таролог. Смотришь на фото карт Таро и читаешь расклад. Пишешь только на русском. Никаких звёздочек и решёток."
COMPAT_PHOTO_SYSTEM = "Ты опытный физиогномист и психолог. Анализируешь совместимость двух людей по фото. Пишешь только на русском. Никаких звёздочек и решёток."
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
    amount = "190.00" if plan == "aura_start" else "390.00"
    plan_name = "Старт" if plan == "aura_start" else "Про"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://aurahelper.ru/payment/success"},
                "capture": True,
                "description": f"AuraBot MAX Тариф {plan_name} — {user_id}",
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": f"AuraBot Тариф {plan_name} 30 дней",
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
                        set_subscription(user_id, plan, 30)
                        delete_pending_payment(payment_id)
                        plan_name = "🟢 Старт" if plan == "aura_start" else "🔥 Про"
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
        now = datetime.now()
        next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= next_8am:
            next_8am += timedelta(days=1)
        await asyncio.sleep((next_8am - now).total_seconds())

        today = datetime.now().strftime("%d.%m.%Y")
        try:
            lunar_text = await generate_text(LUNAR_SYSTEM,
                f"Сегодня {today}. Составь лунный прогноз: фаза луны, что благоприятно делать, чего избегать, совет дня.")
            conn = sqlite3.connect(DB)
            users = conn.execute("SELECT user_id FROM users").fetchall()
            conn.close()
            for (uid,) in users:
                try:
                    await send_message(uid, f"🌙 Лунный календарь на {today}\n\n{lunar_text}")
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Ошибка лунного календаря: {e}")

        conn = sqlite3.connect(DB)
        pro_users = conn.execute("""SELECT u.user_id, u.birth_date FROM users u
            JOIN subscriptions s ON u.user_id = s.user_id
            WHERE s.plan = 'aura_pro' AND s.sub_end > ? AND u.birth_date != ''""",
            (datetime.now().isoformat(),)).fetchall()
        conn.close()
        for user_id, birth_date in pro_users:
            try:
                text = await generate_text(HOROSCOPE_SYSTEM,
                    f"Дата рождения: {birth_date}\n\nПерсональный гороскоп на {today} по дате рождения.")
                await send_message(user_id, f"⭐️ Твой персональный гороскоп на {today}\n\n{text}")
            except Exception as e:
                logging.error(f"Ошибка гороскопа {user_id}: {e}")

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
WELCOME_TEXT = """🔮 Привет, {name}!

Я AuraBot — эзотерик и психолог в одном. Уже чувствую твою энергию 👀

Что умею:
🔢 Нумерология, 🃏 Таро, 💤 Сны, 🌈 Аура
🌟 Гороскоп, ❤️ Совместимость
🧠 AI-Психолог с памятью истории
📔 Личный дневник голосом

🔥 На тарифе Про:
🌌 Матрица судьбы, ♈ Натальная карта
📅 Прогноз, 💰 Денежный код
🖐 Хиромантия, 😊 Физиогномика, ✍️ Графология
👫 Совместимость по фото, 🃏 Таро по фото карт
⭐️ Персональный гороскоп по дате рождения каждое утро

🌙 Всем каждое утро: лунный календарь

🎁 Бесплатно: 15 запросов + 20 сообщений психологу"""

async def handle_limit_msg(chat_id, access):
    if access == "limit_free":
        await send_message(chat_id, "🚫 Бесплатные запросы закончились (15 из 15).\n\nОформи подписку:", upgrade_buttons())
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

async def process_command(chat_id, user_id, text, username="", first_name=""):
    get_user(user_id, username, first_name)
    name = first_name or "друг"

    if text == "/publish_channel_intro" and user_id == OWNER_ID:
        ok = await publish_channel_intro()
        await send_message(chat_id, "✅ Продающий пост опубликован. Теперь его можно закрепить в канале." if ok else "❌ Не удалось опубликовать пост. Проверь журнал сервиса.", main_menu_buttons())
        return

    if text in ("/start", "start"):
        set_step(user_id, "idle")
        plan, _ = get_subscription(user_id)
        asyncio.create_task(asyncio.to_thread(sheets_log_visit, user_id, first_name, username, plan))
        await send_message(chat_id, WELCOME_TEXT.format(name=name), main_menu_buttons())
        return

    user = get_user(user_id)
    step = user.get("step", "")

    # Обработка шагов
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
            f"🎁 Бесплатно: 15 запросов + 30 сообщений психологу{current}",
            [
                [{"type": "callback", "text": "🟢 Старт — 190 руб", "payload": "pay_start"}],
                [{"type": "callback", "text": "🔥 Про — 390 руб", "payload": "pay_pro"}],
                [{"type": "callback", "text": "🔙 В меню", "payload": "back_menu"}]
            ]
        )
        return

    if payload in ("pay_start", "pay_pro"):
        plan = "aura_start" if payload == "pay_start" else "aura_pro"
        try:
            payment = await create_payment(user_id, plan)
            pay_url = payment.get("confirmation", {}).get("confirmation_url", "")
            payment_id = payment.get("id", "")
            if pay_url and payment_id:
                save_pending_payment(payment_id, user_id, plan)
                plan_name = "Старт 190 руб" if plan == "aura_start" else "Про 390 руб"
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
    photo_features = ("chiromancy", "physio", "grapho")

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
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{MAX_API}/subscriptions",
                json={"url": WEBHOOK_URL}, headers=headers)
            logging.info(f"Webhook регистрация: {r.json()}")
    except Exception as e:
        logging.error(f"Ошибка регистрации webhook: {e}")
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(channel_posting_loop())
    logging.info("Aura MAX Bot запущен!")
    asyncio.create_task(daily_loop())
    logging.info("Aura MAX Bot запущен!")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        logging.info(f"MAX webhook: {data}")

        update_type = data.get("update_type", "")
        message = data.get("message", {})
        callback = data.get("callback", {})

        if update_type == "bot_started":
            user = data.get("user", {})
            chat_id = data.get("chat_id") or user.get("user_id")
            user_id = user.get("user_id") or chat_id
            first_name = user.get("name", "друг")
            username = user.get("username", "")
            start_payload = str(
                data.get("payload")
                or data.get("start_payload")
                or data.get("message", {}).get("body", {}).get("payload")
                or ""
            ).strip()
            get_user(user_id, username, first_name)
            set_step(user_id, "idle")
            plan, _ = get_subscription(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_log_visit, user_id, first_name, username, plan))

            routes = {
                "channel_taro": "taro",
                "channel_money": "money_code",
                "channel_psycho": "psycho",
                "channel_love": "compatibility",
                "channel_self": "numerology",
                "channel_day": "horoscope",
                "channel_diary": "diary",
                "channel_dreams": "dreams",
                "channel_matrix": "matrix",
                "channel_forecast": "forecast",
            }
            target = routes.get(start_payload)
            if target:
                try:
                    conn = sqlite3.connect(DB)
                    conn.execute(
                        "INSERT INTO channel_clicks (user_id,source,target,clicked_at) VALUES (?,?,?,?)",
                        (user_id, start_payload, target, datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logging.error(f"Ошибка записи перехода из канала: {e}")
                entry_copy = {
                    "channel_taro": "🃏 Ты пришла за личным ответом. Напиши вопрос или опиши ситуацию — начнём расклад.",
                    "channel_money": "💰 Введи полное имя и дату рождения — посмотрим твой денежный сценарий.",
                    "channel_psycho": "🧠 Опиши ситуацию одним сообщением. Я помогу отделить факты, чувства и тревожные мысли.",
                    "channel_love": "❤️ Введи имена и даты рождения двух людей — посмотрим сильные стороны и зоны роста отношений.",
                    "channel_self": "🔢 Введи дату рождения — начнём личный нумерологический разбор.",
                    "channel_day": "🌟 Напиши свой знак зодиака — подготовлю подсказку на сегодня.",
                    "channel_diary": "📔 Запиши, что сейчас на душе. Дневник сохранит мысль и задаст один бережный вопрос.",
                    "channel_dreams": "🌙 Опиши сон подробно: образы, людей и свои чувства.",
                    "channel_matrix": "🌌 Введи дату рождения — откроем разбор предназначения и жизненных задач.",
                    "channel_forecast": "📅 Введи дату рождения и период, например: 15.03.1990, месяц.",
                }
                await send_message(chat_id, entry_copy.get(start_payload, WELCOME_TEXT.format(name=first_name)))
                await process_callback(chat_id, user_id, target, first_name)
            else:
                await send_message(chat_id, WELCOME_TEXT.format(name=first_name), main_menu_buttons())

        elif update_type == "message_created":
            sender = message.get("sender", {})
            chat_id = message.get("recipient", {}).get("chat_id")
            user_id = sender.get("user_id")
            first_name = sender.get("name", "друг")
            username = sender.get("username", "")
            body = message.get("body", {})
            text = body.get("text", "")
            attachments = body.get("attachments", [])

            if attachments:
                for att in attachments:
                    if att.get("type") == "image":
                        payload_data = att.get("payload", {})
                        photo_url = (
                            payload_data.get("url") or
                            payload_data.get("photo_url") or
                            (payload_data.get("photos", [{}])[0].get("url") if payload_data.get("photos") else None)
                        )
                        logging.info(f"Фото payload: {payload_data}")
                        if photo_url:
                            await process_photo(chat_id, user_id, photo_url)
                            return JSONResponse({"ok": True})
                        else:
                            logging.error(f"Не найден URL фото: {payload_data}")

            if text:
                await process_command(chat_id, user_id, text, username, first_name)

        elif update_type == "message_callback":
            user = callback.get("user", {})
            recipient = message.get("recipient", {})
            chat_id = (
                recipient.get("chat_id") or
                callback.get("chat_id") or
                message.get("sender", {}).get("chat_id")
            )
            user_id = user.get("user_id")
            first_name = user.get("name", "друг")
            payload = callback.get("payload", "")
            logging.info(f"CALLBACK: chat_id={chat_id} user_id={user_id} payload={payload}")
            if chat_id and payload:
                await process_callback(chat_id, user_id, payload, first_name)
            else:
                logging.error(f"Нет chat_id в callback: {data}")

    except Exception as e:
        logging.error(f"Webhook error: {e}")

    return JSONResponse({"ok": True})

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

# ========== КАНАЛ MAX — PREMIUM FUNNEL ==========
MAX_CHANNEL_ID = -75554451158515
MAX_BOT_LINK = "https://max.ru/id232007136009_bot"

CHANNEL_EDITOR_SYSTEM = """Ты главный редактор премиального канала «Аура — Психология».
Канал сочетает практическую психологию, бережное самопознание и символические практики.
Пиши живо, конкретно и современно. Не используй дешёвую мистику, запугивание, фатальные обещания, выдуманные отзывы и гарантии результата.
Каждый пост должен дать узнавание, одну практическую пользу и естественно подвести к персональному продолжению в боте.
Не добавляй ссылки, хэштеги и призывы «перейти по ссылке» — нативную кнопку добавит программа.
Короткие абзацы, только русский язык."""

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
    return f"{MAX_BOT_LINK}?start={payload}"

def native_channel_button(text, payload):
    return [[{"type": "link", "text": text[:40], "url": channel_deep_link(payload)}]]

async def send_to_channel(text, button_text, start_payload):
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    payload = {
        "text": text[:4000],
        "attachments": [{
            "type": "inline_keyboard",
            "payload": {"buttons": native_channel_button(button_text, start_payload)}
        }]
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{MAX_API}/messages", params={"chat_id": MAX_CHANNEL_ID, "disable_link_preview": "true"}, json=payload, headers=headers)
        logging.info(f"Канал MAX: {r.status_code}")
        if r.status_code >= 400:
            raise RuntimeError(f"MAX channel error {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            return {"ok": True}

def channel_slot_key(dt, rubric):
    return f"{dt.strftime('%Y-%m-%d')}_{rubric}"

def channel_was_sent(key):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT status FROM channel_posts WHERE slot_key=?", (key,)).fetchone()
    conn.close()
    return bool(row and row[0] == "sent")

def save_channel_post(key, rubric, topic, content, status):
    conn = sqlite3.connect(DB)
    conn.execute(
        """INSERT OR REPLACE INTO channel_posts (slot_key,rubric,topic,content,status,published_at) VALUES (?,?,?,?,?,?)""",
        (key, rubric, topic[:250], content[:4000], status, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def recent_channel_topics(limit=24):
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT rubric,topic FROM channel_posts WHERE status='sent' ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
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
        text = (text or "").strip()
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
        await send_to_channel(text, button_text, start_payload)
        save_channel_post(key, rubric, extract_topic(text), text, "sent")
        return True
    except Exception as e:
        save_channel_post(key, rubric, extract_topic(text), text, "failed")
        logging.exception(f"Ошибка публикации {key}: {e}")
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

В канале ты получаешь полезную мысль. В AuraMAX — персональное продолжение именно под твою ситуацию.

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
        now = datetime.utcnow() + timedelta(hours=3)
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
    config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
