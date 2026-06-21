import asyncio
import sqlite3
import logging
import uuid
import os
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
TELEGRAM_OWNER_ID = 549639607  # Не используется для авторизации в MAX
MAX_OWNER_ID = int(os.getenv("MAX_OWNER_ID", "214128371") or 214128371)
MAX_OWNER_USERNAME = os.getenv("MAX_OWNER_USERNAME", "").lstrip("@").lower()

def is_owner(user_id, username=""):
    if MAX_OWNER_ID and int(user_id or 0) == MAX_OWNER_ID:
        return True
    return bool(MAX_OWNER_USERNAME and (username or "").lstrip("@").lower() == MAX_OWNER_USERNAME)
SUPPORT_URL = "https://t.me/Boss023rus"

ONE_TIME_PRODUCTS = {
    "once_money_code": {"feature": "money_code", "title": "Денежный код", "amount": 199},
    "once_matrix": {"feature": "matrix", "title": "Матрица судьбы", "amount": 249},
    "once_forecast": {"feature": "forecast", "title": "Прогноз на год", "amount": 299},
    "once_natal": {"feature": "natal", "title": "Натальная карта", "amount": 349},
}
FEATURE_TO_PRODUCT = {v["feature"]: k for k, v in ONE_TIME_PRODUCTS.items()}
PLATFORM_NAME = "MAX"
MOSCOW = ZoneInfo("Europe/Moscow")
BOT_LINK = "https://max.ru/id232007136009_bot"
CHANNEL_LINK = "https://max.ru/join/FGrz60vuvjsYfQoPyUFx7LSx09Pr5kknakutA-mWc1Q"

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
USERS_SHEET_NAME = "Aura MAX"
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

# Фоновые задачи webhook: MAX должен получать HTTP 200 сразу,
# а долгие AI-операции выполняются независимо от времени ответа webhook.
BACKGROUND_TASKS = set()

def create_background_task(coro):
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task

async def run_user_task(chat_id, user_id, operation, coro):
    try:
        await coro
    except Exception as exc:
        logging.exception("Ошибка фоновой операции %s для user_id=%s", operation, user_id)
        try:
            await send_message(
                chat_id,
                "⚠️ Не удалось завершить обработку. Попробуй ещё раз через минуту. "
                "Если ошибка повторится — нажми «💬 Поддержка».",
                back_button(),
            )
        except Exception:
            logging.exception("Не удалось отправить пользователю сообщение об ошибке")
        try:
            await notify_owner("⚠️ Ошибка AuraMAX", user_id, operation, str(exc)[:700])
        except Exception:
            logging.exception("Не удалось уведомить владельца")

# ========== MAX API ==========
async def send_message(chat_id, text, buttons=None):
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    payload = {"text": text[:4000]}
    if buttons:
        payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{MAX_API}/messages?chat_id={chat_id}&disable_link_preview=true", json=payload, headers=headers)
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
    c.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        birth_date TEXT DEFAULT '',
        source TEXT DEFAULT '',
        referrer_id INTEGER,
        stopped INTEGER DEFAULT 0
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
async def _openai_text_request(messages, model="gpt-4o-mini"):
    last_error = None
    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                openai_client.chat.completions.create(
                    model=model, messages=messages, max_tokens=1500
                ),
                timeout=90,
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                raise RuntimeError("AI вернул пустой ответ")
            return content
        except Exception as exc:
            last_error = exc
            logging.warning("Ошибка AI, попытка %s/2: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(2)
    raise RuntimeError(f"AI не ответил после двух попыток: {last_error}")

async def generate_text(system, prompt, model="gpt-4o-mini"):
    return await _openai_text_request(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        model=model,
    )

async def generate_with_history(system, history, new_message):
    messages = [{"role": "system", "content": system}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": new_message})
    return await _openai_text_request(messages, model="gpt-4o-mini")

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
        description = f"Aura MAX Разовый разбор {product_name} — {user_id}"
        receipt_description = f"Разовый персональный разбор: {product_name}"
    else:
        amount, product_name, days = subscriptions.get(plan, subscriptions["aura_pro"])
        purchase_type = "subscription"
        description = f"AuraBot MAX Тариф {product_name} — {user_id}"
        receipt_description = f"AuraBot Тариф {product_name} {days} дней"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": amount, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": BOT_LINK},
                "capture": True,
                "description": description,
                "receipt": {"customer": {"email": "6038484@mail.ru"}, "items": [{
                    "description": receipt_description,
                    "quantity": "1.00",
                    "amount": {"value": amount, "currency": "RUB"},
                    "vat_code": 1, "payment_subject": "service", "payment_mode": "full_payment"
                }]},
                "metadata": {"user_id": user_id, "plan": plan, "purchase_type": purchase_type, "platform": "MAX"}
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
                                    (payment_id, user_id, plan, "MAX", "succeeded", datetime.now().isoformat()),
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
                                (payment_id, user_id, plan, "MAX", "succeeded", datetime.now().isoformat()),
                            )

                        try:
                            log_event(user_id, "payment_succeeded", feature=plan, source="MAX", value=payment_id)
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
        if MAX_OWNER_ID:
            await send_message(MAX_OWNER_ID, text)
        else:
            logging.warning("MAX_OWNER_ID не задан — уведомление владельцу не отправлено: %s", text[:200])
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


def get_profile_text(user_id):
    plan, end = get_subscription(user_id)
    lim = get_limits(user_id)
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT birth_date FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    birth = row[0] if row and row[0] else ""
    plan_name = {"aura_start": "🟢 Аура Старт", "aura_pro": "🔥 Аура Про"}.get(plan, "Бесплатный")
    until = f" до {end.strftime('%d.%m.%Y')}" if end else ""
    return (f"👤 Твой профиль\n\nТариф: {plan_name}{until}\n"
            f"Дата рождения: {birth or 'не указана'}\n"
            f"Бесплатных разборов использовано: {lim['requests']} из {FREE_REQUESTS}\n"
            f"Сообщений психологу: {lim['psycho']}")

def referral_buttons(user_id):
    link = f"{BOT_LINK}?start=ref_{user_id}"
    return [[{"type":"link","text":"📨 Отправить приглашение","url":link}],
            [{"type":"callback","text":"🔙 В меню","payload":"back_menu"}]]

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

async def process_command(chat_id, user_id, text, username="", first_name=""):
    get_user(user_id, username, first_name)
    name = first_name or "друг"

    if text == "/myid":
        await send_message(
            chat_id,
            f"Ваш MAX user_id: {user_id}\nUsername: @{username}" if username else f"Ваш MAX user_id: {user_id}",
            main_menu_buttons()
        )
        return

    if text == "/publish_channel_intro":
        if not is_owner(user_id, username):
            await send_message(
                chat_id,
                f"⛔ Команда доступна владельцу.\n\nВаш MAX user_id: {user_id}\n"
                f"Настроенный MAX_OWNER_ID: {MAX_OWNER_ID}. Проверьте, что запущена актуальная версия файла.",
                main_menu_buttons()
            )
            return
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
    if step == "my_day_birth":
        import re
        m = re.search(r"\b(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/]((?:19|20)\d{2})\b", text or "")
        if not m:
            await send_message(chat_id, "Не смогла распознать дату. Напиши, например: 15.03.1990", back_button())
            return
        try:
            birth = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%d.%m.%Y")
        except ValueError:
            await send_message(chat_id, "Проверь дату и напиши в формате ДД.ММ.ГГГГ", back_button())
            return
        with sqlite3.connect(DB) as conn:
            conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
            conn.execute("UPDATE user_profiles SET birth_date=? WHERE user_id=?", (birth, user_id))
            conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth, user_id))
        set_step(user_id, "idle")
        result = await generate_text(HOROSCOPE_SYSTEM, f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. Дай персональную подсказку: энергия дня, отношения, деньги, главное действие и вечерняя практика. Не обещай неизбежных событий.")
        await send_message(chat_id, "🌟 Твой день\n\n" + result, back_button())
        return

    if step == "support":
        await process_support_message(chat_id, user_id, text)
        return

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
        asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, first_name, username))
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

    if payload == "noop":
        return

    if payload.startswith("cat_"):
        cat = payload.split("_", 1)[1]
        titles = {"situation":"🔮 Разобрать ситуацию", "love":"❤️ Отношения", "money":"💰 Деньги и реализация", "self":"✨ Узнать себя"}
        await send_message(chat_id, titles.get(cat, "Выбери направление"), category_buttons(cat))
        return

    if payload == "profile":
        await send_message(chat_id, get_profile_text(user_id), back_button())
        return

    if payload == "referral":
        await send_message(chat_id,
            "🎁 Пригласи близкого человека\n\nПосле его первой успешной оплаты тебе начислят 30 дней Аура Про.",
            referral_buttons(user_id))
        return

    if payload == "my_day":
        with sqlite3.connect(DB) as conn:
            row = conn.execute("SELECT birth_date FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        birth = row[0] if row and row[0] else ""
        if not birth:
            set_step(user_id, "my_day_birth")
            await send_message(chat_id, "🌟 Мой день\n\nВведи дату рождения в формате ДД.ММ.ГГГГ — я сохраню её и подготовлю личную подсказку.", back_button())
            return
        await send_message(chat_id, "⏳ Собираю личную подсказку...")
        result = await generate_text(HOROSCOPE_SYSTEM, f"Дата рождения: {birth}. Сегодня {datetime.now(MOSCOW).strftime('%d.%m.%Y')}. Дай персональную подсказку: энергия дня, отношения, деньги, главное действие и короткая вечерняя практика. Не обещай неизбежных событий.")
        await send_message(chat_id, "🌟 Твой день\n\n" + result, back_button())
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
                if plan in ONE_TIME_PRODUCTS:
                    product = ONE_TIME_PRODUCTS[plan]
                    plan_name = f"{product['amount']} ₽ — {product['title']}"
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
            name, uname, _ = get_user_identity(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_sync_user, user_id, name, uname if uname != "—" else ""))
        await send_message(chat_id, result, back_button())
    except Exception as e:
        logging.error(f"Ошибка фото-анализа: {e}")
        await notify_owner("⚠️ Ошибка фото-анализа", user_id, step, str(e))
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
                json={"url": WEBHOOK_URL, "update_types": ["message_created", "message_callback", "bot_started", "bot_stopped"]}, headers=headers)
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
            with sqlite3.connect(DB) as conn:
                conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
                if start_payload.startswith("ref_"):
                    try:
                        referrer_id = int(start_payload.split("_", 1)[1])
                        if referrer_id != user_id:
                            conn.execute("UPDATE user_profiles SET referrer_id=COALESCE(referrer_id, ?) WHERE user_id=?", (referrer_id, user_id))
                    except (ValueError, TypeError):
                        logging.warning("Некорректный реферальный payload: %s", start_payload)
                elif start_payload:
                    conn.execute("UPDATE user_profiles SET source=COALESCE(NULLIF(source,''), ?) WHERE user_id=?", (start_payload, user_id))
            set_step(user_id, "idle")
            plan, _ = get_subscription(user_id)
            asyncio.create_task(asyncio.to_thread(sheets_log_visit, user_id, first_name, username, plan))

            if start_payload == "channel_intro":
                try:
                    conn = sqlite3.connect(DB)
                    conn.execute(
                        "INSERT INTO channel_clicks (user_id,source,target,clicked_at) VALUES (?,?,?,?)",
                        (user_id, start_payload, "intro_choice", datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logging.error(f"Ошибка записи перехода из закрепа: {e}")
                await send_message(
                    chat_id,
                    "🎁 Начнём с бесплатного личного разбора.\n\nВыбери, что сейчас волнует тебя сильнее всего:",
                    [
                        [{"type": "callback", "text": "🔮 Разобрать ситуацию", "payload": "cat_situation"}],
                        [
                            {"type": "callback", "text": "❤️ Отношения", "payload": "cat_love"},
                            {"type": "callback", "text": "💰 Деньги", "payload": "cat_money"}
                        ],
                        [
                            {"type": "callback", "text": "🧠 Психолог", "payload": "psycho"},
                            {"type": "callback", "text": "✨ Узнать себя", "payload": "cat_self"}
                        ],
                        [{"type": "callback", "text": "🏠 Главное меню", "payload": "back_menu"}],
                    ]
                )
                return

            routes = {
                "channel_taro": "taro",
                "channel_money": "money_code",
                "channel_psycho": "psycho",
                "channel_love": "compatibility",
                "channel_self": "numerology",
                "channel_day": "my_day",
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
                    "channel_day": "🌟 Открою персональную подсказку на сегодня по твоей дате рождения.",
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
                            create_background_task(run_user_task(
                                chat_id, user_id, "photo",
                                process_photo(chat_id, user_id, photo_url)
                            ))
                            return JSONResponse({"ok": True})
                        else:
                            logging.error(f"Не найден URL фото: {payload_data}")

            if text:
                create_background_task(run_user_task(
                    chat_id, user_id, "message",
                    process_command(chat_id, user_id, text, username, first_name)
                ))
                return JSONResponse({"ok": True})

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
                create_background_task(run_user_task(
                    chat_id, user_id, f"callback:{payload}",
                    process_callback(chat_id, user_id, payload, first_name)
                ))
                return JSONResponse({"ok": True})
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

async def send_to_channel(text, button_text, start_payload, image_path=None):
    headers = {"Authorization": MAX_TOKEN, "Content-Type": "application/json"}
    attachments = []
    if image_path and os.path.exists(image_path):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                meta = await client.post(f"{MAX_API}/uploads?type=image", headers={"Authorization": MAX_TOKEN})
                meta.raise_for_status()
                upload_url = meta.json().get("url")
                with open(image_path, "rb") as fh:
                    uploaded = await client.post(upload_url, files={"data": ("aura.png", fh, "image/png")})
                uploaded.raise_for_status()
                data = uploaded.json()
                token = data.get("token") or data.get("payload", {}).get("token")
                if token:
                    attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            logging.error(f"Канал MAX: визуал не отправлен: {e}")
    attachments.append({
        "type": "inline_keyboard",
        "payload": {"buttons": native_channel_button(button_text, start_payload)}
    })
    payload = {"text": text[:4000], "attachments": attachments}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{MAX_API}/messages?chat_id={MAX_CHANNEL_ID}&disable_link_preview=true",
            json=payload, headers=headers
        )
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
        visual = create_channel_visual(dt, rubric, WEEKLY_FUNNEL[dt.weekday()]["theme"])
        await send_to_channel(text, button_text, start_payload, visual)
        save_channel_post(key, rubric, extract_topic(text), text, "sent")
        return True
    except Exception as e:
        save_channel_post(key, rubric, extract_topic(text), text, "failed")
        logging.exception(f"Ошибка публикации {key}: {e}")
        await notify_owner("⚠️ Не вышел пост в MAX-канале", 0, rubric, f"{key}: {e}")
        return False

async def publish_channel_intro():
    text = """🔮 Добро пожаловать в «Аура — Психология»

Иногда нужен не общий совет, а спокойное место, где можно понять, что происходит именно с тобой.

В этом канале:

🧠 простая психология без сложных терминов
❤️ отношения, границы и самоценность
💰 деньги, реализация и внутренние опоры
🃏 Таро и символические практики для взгляда на ситуацию с другой стороны
🌙 вечерние вопросы и короткие практики для возвращения к себе

Каждый день здесь выходят полезные посты, которые можно применить сразу.

А когда общего совета недостаточно, AuraMAX поможет разобрать твою личную ситуацию: отношения, деньги, внутреннее состояние или жизненный выбор.

🎁 В боте бесплатно доступны 5 персональных разборов и 15 сообщений AI-психологу.

Начни с того, что волнует тебя сейчас."""
    try:
        await send_to_channel(text, "🎁 Начать бесплатный личный разбор", "channel_intro")
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
    config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
