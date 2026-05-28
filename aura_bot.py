import asyncio
import sqlite3
import logging
import uuid
from datetime import datetime, timedelta
from openai import AsyncOpenAI
import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from yookassa import Configuration, Payment
import gspread
from google.oauth2.service_account import Credentials

# ========== КОНФИГ ==========
BOT_TOKEN = "8757527368:AAFWH8CkEmfiBVhd6wiltSKN9FRiJg_741c"
OPENAI_KEY = "sk-mfvVI3QN2uQvXPlhMkAeUUzmbjK5aQzj"
OWNER_ID = 549639607
SUPPORT_URL = "https://t.me/Boss023rus"

# Лимиты
FREE_REQUESTS = 15      # бесплатных запросов на все функции
FREE_PSYCHO = 30        # бесплатных сообщений психологу
START_PSYCHO = 100      # сообщений психологу на Старте
START_PHOTO = 5         # фото-анализов на Старте (каждый тип)
PRO_DAILY_HOROSCOPE = True  # ежедневный гороскоп только на Про

# ========== ЮКАССА ==========
YOOKASSA_SHOP_ID = "1363324"
YOOKASSA_SECRET = "live_-RKE9nsi8wZiM-5f00z78E84OYSi3M0Dj9w_-pE0Mvw"
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET

# ========== GOOGLE SHEETS ==========
SPREADSHEET_ID = "1PE7CaFuWOe_eygQqIoMAmUdJBtATbIaNfZR4cvarPCA"
CREDENTIALS_FILE = "/root/google_credentials.json"

def get_sheet():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        try:
            sheet = spreadsheet.worksheet("AuraBot")
        except Exception:
            sheet = spreadsheet.add_worksheet(title="AuraBot", rows=1000, cols=10)
            sheet.insert_row(["ID", "Username", "Имя", "Дата регистрации", "Тариф", "Дата оплаты", "Запросов", "Последняя активность"], 1)
        return sheet
    except Exception as e:
        logging.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def sheets_add_review(user_id, username, first_name, review_text):
    try:
        if not SPREADSHEET_ID:
            return
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        # Открываем или создаём лист Отзывы
        try:
            sheet = spreadsheet.worksheet("AuraBot Отзывы")
        except Exception:
            sheet = spreadsheet.add_worksheet(title="AuraBot Отзывы", rows=1000, cols=5)
            sheet.insert_row(["ID", "Username", "Имя", "Дата", "Отзыв"], 1)
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            review_text
        ])
    except Exception as e:
        logging.error(f"Ошибка Google Sheets (отзыв): {e}")

def sheets_update_activity(user_id):
    try:
        if not SPREADSHEET_ID:
            return
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if str(user_id) in col:
            row = col.index(str(user_id)) + 1
            sheet.update_cell(row, 8, datetime.now().strftime("%d.%m.%Y %H:%M"))
            # Обновляем счётчик запросов
            lim = get_limits(user_id)
            sheet.update_cell(row, 7, str(lim["requests"]))
    except Exception as e:
        logging.error(f"Ошибка Google Sheets (активность): {e}")

def sheets_add_user(user_id, username, first_name):
    try:
        if not SPREADSHEET_ID:
            return
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if not col or col[0] != "ID":
            sheet.insert_row(["ID", "Username", "Имя", "Дата регистрации", "Тариф", "Дата оплаты", "Запросов", "Последняя активность"], 1)
            col = sheet.col_values(1)
        if str(user_id) in col:
            return
        sheet.append_row([
            str(user_id),
            f"@{username}" if username else "—",
            first_name or "—",
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "Бесплатный", "—", "0",
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ])
    except Exception as e:
        logging.error(f"Ошибка Google Sheets (новый пользователь): {e}")

def sheets_update_subscription(user_id, plan):
    try:
        if not SPREADSHEET_ID:
            return
        sheet = get_sheet()
        if not sheet:
            return
        col = sheet.col_values(1)
        if str(user_id) in col:
            row = col.index(str(user_id)) + 1
            plan_name = "🟢 Старт" if plan == "aura_start" else "🔥 Про"
            sheet.update_cell(row, 5, plan_name)
            sheet.update_cell(row, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
    except Exception as e:
        logging.error(f"Ошибка Google Sheets (подписка): {e}")

# ========== ЛОГИ ==========
logging.basicConfig(level=logging.INFO)

# ========== КЛИЕНТ OPENAI ==========
client = AsyncOpenAI(api_key=OPENAI_KEY, base_url="https://api.proxyapi.ru/openai/v1")

# ========== КЛИЕНТ CLAUDE ==========
CLAUDE_MODEL = "claude-sonnet-4-20250514"
claude_client = anthropic.Anthropic(
    api_key=OPENAI_KEY,
    base_url="https://api.proxyapi.ru/anthropic"
)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        step TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        zodiac_sign TEXT DEFAULT '',
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
    conn.commit()
    conn.close()

def get_user(user_id, username="", first_name=""):
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at) VALUES (?,?,?,?)",
              (user_id, username, first_name, datetime.now().isoformat()))
    c.execute("INSERT OR IGNORE INTO limits (user_id) VALUES (?)", (user_id,))
    conn.commit()
    c.execute("SELECT step, birth_date, zodiac_sign FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {"step": row[0], "birth_date": row[1], "zodiac_sign": row[2]}

def set_step(user_id, step):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("UPDATE users SET step=? WHERE user_id=?", (step, user_id))
    conn.commit()
    conn.close()

def set_birth_date(user_id, birth_date):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("UPDATE users SET birth_date=? WHERE user_id=?", (birth_date, user_id))
    conn.commit()
    conn.close()

def set_zodiac(user_id, zodiac):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("UPDATE users SET zodiac_sign=? WHERE user_id=?", (zodiac, user_id))
    conn.commit()
    conn.close()

def get_subscription(user_id):
    conn = sqlite3.connect("/root/aura.db")
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
    conn = sqlite3.connect("/root/aura.db")
    end = (datetime.now() + timedelta(days=days)).isoformat()
    conn.execute("INSERT OR REPLACE INTO subscriptions (user_id, plan, sub_end) VALUES (?,?,?)",
                 (user_id, plan, end))
    conn.commit()
    conn.close()

def get_limits(user_id):
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO limits (user_id) VALUES (?)", (user_id,))
    c.execute("SELECT requests, psycho_messages, photo_chiromancy, photo_physio, photo_grapho FROM limits WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return {"requests": row[0], "psycho": row[1], "chiromancy": row[2], "physio": row[3], "grapho": row[4]}

def increment_limit(user_id, field):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute(f"UPDATE limits SET {field}={field}+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def reset_limits_all():
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("DELETE FROM limits")
    conn.commit()
    conn.close()

def get_psycho_history(user_id, limit=20):
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("SELECT role, content FROM psycho_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def add_psycho_message(user_id, role, content):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("INSERT INTO psycho_history (user_id, role, content, created_at) VALUES (?,?,?,?)",
                 (user_id, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def clear_psycho_history(user_id):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("DELETE FROM psycho_history WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def add_diary_entry(user_id, entry, response):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("INSERT INTO diary (user_id, entry, response, created_at) VALUES (?,?,?,?)",
                 (user_id, entry, response, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_diary_history(user_id, limit=5):
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("SELECT entry, response, created_at FROM diary WHERE user_id=? ORDER BY id DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def save_pending_payment(payment_id, user_id, plan):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("INSERT INTO pending_payments (payment_id, user_id, plan, created_at) VALUES (?,?,?,?)",
                 (payment_id, user_id, plan, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_pending_payments():
    conn = sqlite3.connect("/root/aura.db")
    c = conn.cursor()
    c.execute("SELECT payment_id, user_id, plan FROM pending_payments")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_pending_payment(payment_id):
    conn = sqlite3.connect("/root/aura.db")
    conn.execute("DELETE FROM pending_payments WHERE payment_id=?", (payment_id,))
    conn.commit()
    conn.close()

# ========== ПРОВЕРКА ДОСТУПА ==========
async def check_access(user_id, feature="general"):
    plan, sub_end = get_subscription(user_id)
    lim = get_limits(user_id)

    # Про — всё безлимит
    if plan == "aura_pro":
        return "pro"

    # Психолог — отдельный счётчик
    if feature == "psycho":
        if plan == "aura_start":
            return "ok" if lim["psycho"] < START_PSYCHO else "limit_psycho_start"
        return "ok" if lim["psycho"] < FREE_PSYCHO else "limit_psycho_free"

    # Фото-анализы — отдельные счётчики на Старте
    photo_map = {"chiromancy": "chiromancy", "physio": "physio", "grapho": "grapho", "taro_photo": "taro_photo"}
    if feature in photo_map:
        if plan == "aura_pro":
            return "pro"
        if plan == "aura_start":
            return "ok" if lim[photo_map[feature]] < START_PHOTO else "limit_photo"
        # Бесплатно — входят в общий лимит 15 запросов
        return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

    # Совместимость по фото — только Про
    if feature == "compat_photo":
        if plan == "aura_pro":
            return "pro"
        return "start_block"

    # Функции только для Про
    if feature in ("matrix", "forecast", "natal"):
        if plan == "aura_start":
            return "start_block"
        return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

    # Старт — безлимит на базовые функции
    if plan == "aura_start":
        return "ok"

    # Бесплатно — общий лимит
    return "ok" if lim["requests"] < FREE_REQUESTS else "limit_free"

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Нумерология", callback_data="numerology"),
         InlineKeyboardButton(text="🃏 Таро", callback_data="taro")],
        [InlineKeyboardButton(text="💤 Сны", callback_data="dreams"),
         InlineKeyboardButton(text="🌈 Аура", callback_data="aura")],
        [InlineKeyboardButton(text="🌟 Гороскоп", callback_data="horoscope"),
         InlineKeyboardButton(text="❤️ Совместимость", callback_data="compatibility")],
        [InlineKeyboardButton(text="🧠 AI-Психолог", callback_data="psycho"),
         InlineKeyboardButton(text="📔 Голосовой дневник", callback_data="diary")],
        [InlineKeyboardButton(text="🔥 ПРО ФУНКЦИИ 🔥", callback_data="noop")],
        [InlineKeyboardButton(text="🌌 Матрица судьбы", callback_data="matrix"),
         InlineKeyboardButton(text="📅 Прогноз", callback_data="forecast")],
        [InlineKeyboardButton(text="♈ Натальная карта", callback_data="natal"),
         InlineKeyboardButton(text="💰 Денежный код", callback_data="money_code")],
        [InlineKeyboardButton(text="🖐 Хиромантия", callback_data="chiromancy"),
         InlineKeyboardButton(text="😊 Физиогномика", callback_data="physio")],
        [InlineKeyboardButton(text="✍️ Графология", callback_data="grapho"),
         InlineKeyboardButton(text="🃏 Таро по фото", callback_data="taro_photo")],
        [InlineKeyboardButton(text="👫 Совместимость фото", callback_data="compat_photo")],
        [InlineKeyboardButton(text="💎 Тарифы и оплата", callback_data="tariffs")],
        [InlineKeyboardButton(text="⭐️ Оставить отзыв", callback_data="review")],
        [InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL)],
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
    ])

def tariffs_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Старт — 190 руб/мес", callback_data="pay_start")],
        [InlineKeyboardButton(text="🔥 Про — 390 руб/мес", callback_data="pay_pro")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")],
    ])

def upgrade_menu(plan="any"):
    if plan == "start":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Купить Про — 390 руб", callback_data="pay_pro")],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Старт — 190 руб", callback_data="pay_start")],
        [InlineKeyboardButton(text="🔥 Про — 390 руб", callback_data="pay_pro")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
    ])

def psycho_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новый разговор", callback_data="psycho_new")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
    ])

# ========== ПРОМПТЫ ==========
PSYCHO_SYSTEM = """Ты — мудрый психолог и коуч с 20-летним опытом. Помогаешь людям разобраться в себе и своих ситуациях.
Говоришь тепло, человечно, как близкий друг который умеет слушать и видит суть. Пишешь только на русском.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты.
Задаёшь уточняющие вопросы. Даёшь конкретные техники и советы. Помнишь всё что человек рассказывал раньше.
Никогда не говори что ты ИИ — ты просто мудрый собеседник."""

NUMEROLOGY_SYSTEM = """Ты — мудрый нумеролог с 20-летним опытом. Видишь судьбу человека через цифры.
Говоришь тепло, лично, как близкий друг который знает больше. Пишешь только на русском.
Никаких звёздочек и решёток — только чистый текст с эмодзи. Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

TARO_SYSTEM = """Ты — мудрый таролог с 20-летним опытом. Говоришь тепло, немного загадочно, как человек который видит больше чем другие.
Пишешь только на русском. Никаких звёздочек и решёток. Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

DREAMS_SYSTEM = """Ты — мудрый толкователь снов с 20-летним опытом. Видишь послания подсознания через образы снов.
Говоришь тепло, немного мистически. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

AURA_SYSTEM = """Ты — мудрый энергетик и ясновидящий с 20-летним опытом. Видишь энергетику человека через дату рождения.
Говоришь тепло, глубоко. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

MATRIX_SYSTEM = """Ты — мудрый мастер Матрицы Судьбы с 20-летним опытом. Читаешь предназначение через дату рождения.
Говоришь глубоко, тепло. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

FORECAST_SYSTEM = """Ты — мудрый прорицатель с 20-летним опытом. Видишь пути развития событий через нумерологию.
Говоришь тепло, честно, без страшилок. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

COMPATIBILITY_SYSTEM = """Ты — мудрый астропсихолог с 20-летним опытом. Анализируешь совместимость через дату рождения и энергетику.
Говоришь тепло, честно. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

NATAL_SYSTEM = """Ты — мудрый астролог-натальщик с 20-летним опытом. Составляешь и читаешь натальные карты.
Говоришь глубоко, точно. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты."""

HOROSCOPE_SYSTEM = """Ты — мудрый астролог с 20-летним опытом. Читаешь судьбу через звёзды и планеты.
Говоришь тепло, вдохновляюще. Пишешь только на русском. Никаких звёздочек и решёток.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты. Каждый ответ — личное послание именно этому человеку."""

DIARY_SYSTEM = """Ты — внимательный слушатель и зеркало. Человек ведёт голосовой дневник.
Ты НЕ даёшь советов и НЕ лечишь. Ты отражаешь: что слышишь, какое настроение чувствуешь, какие темы повторяются.
Говоришь мягко, бережно, как близкий человек который просто слушает. Пишешь только на русском.
Никогда не начинай с Конечно, Отлично, Вот, Готово. Обращайся на ты.
Структура: что услышал в этой записи, какое настроение чувствуется, одно мягкое наблюдение о повторяющейся теме (если есть), один тёплый вопрос для следующей записи."""

MONEY_CODE_SYSTEM = """Ты мудрый нумеролог специализирующийся на денежном коде.
Анализируешь числовой код человека для привлечения достатка. Говоришь конкретно и вдохновляюще.
Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты."""

COMPAT_PHOTO_SYSTEM = """На фото два человека. Ты опытный физиогномист и психолог.
Анализируешь совместимость по чертам лица, энергетике и языку тела.
Пишешь только на русском. Никаких звёздочек и решёток. Обращайся на ты.
Расскажи: первое впечатление от каждого, совместимость по характеру, сильные стороны пары, зоны роста."""

LUNAR_SYSTEM = """Ты мудрый астролог и знаток лунного календаря. Пишешь ежедневный лунный прогноз.
Пишешь тепло, конкретно, практично. Только на русском. Никаких звёздочек и решёток."""

TARO_PHOTO_SYSTEM = """Пользователь прислал фото карт Таро которые он вытащил для расклада. 
Ты — опытный таролог с 20-летним опытом. Смотришь на карты на фото и читаешь расклад.
Определи какие карты изображены на фото и дай полное толкование расклада.
Пишешь только на русском. Никаких звёздочек и решёток — только чистый текст с эмодзи.
Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово". Обращайся на ты.
Расскажи: какие карты видишь, что каждая означает в позиции расклада, общий совет."""

CHIROMANCY_SYSTEM = """Пользователь прислал фото ладони. Ты — опытный хиромант который умеет читать руку как открытую книгу.
Смотришь внимательно и рассказываешь что видишь — конкретно, лично, без общих фраз. Пишешь только на русском.
Никаких звёздочек и решёток. Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово".
Анализируй: линия жизни, линия ума, линия сердца, линия судьбы, форма руки, пальцы. Говори конкретно о ЭТОМ человеке."""

PHYSIO_SYSTEM = """Пользователь прислал фото лица. Ты — опытный физиогномист который читает характер по чертам лица.
Смотришь внимательно и рассказываешь что видишь — конкретно, без общих фраз, только о характере. Пишешь только на русском.
Никаких звёздочек и решёток. Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово".
Анализируй: лоб, глаза, нос, губы, подбородок, общая форма лица. Говори о характере и внутреннем мире ЭТОГО человека."""

GRAPHO_SYSTEM = """Пользователь прислал фото рукописного текста. Ты — опытный графолог который читает характер по почерку как открытую книгу.
Смотришь внимательно на каждую деталь и рассказываешь что видишь — конкретно и лично. Пишешь только на русском.
Никаких звёздочек и решёток. Никогда не начинай с "Конечно", "Отлично", "Вот", "Готово".
Анализируй: наклон, нажим, размер букв, связность, поля, расстояние между словами. Говори о характере ЭТОГО человека."""

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

WELCOME_TEXT = """🔮 Привет, {name}!

Я AuraBot — эзотерик и психолог в одном. Уже чувствую твою энергию — она интересная 👀

Что умею:
🔢 Нумерология — числа твоей судьбы
🃏 Таро — расклад на 3 карты
💤 Толкование снов
🌈 Чтение ауры по дате рождения
🌟 Гороскоп на сегодня
❤️ Совместимость двух людей
🧠 AI-Психолог — помнит всю твою историю
📔 Голосовой дневник с разбором психолога

🔥 На тарифе Про:
🌌 Матрица судьбы и натальная карта
📅 Нумерологический прогноз
💰 Денежный код
🖐 Фото-анализы — безлимит
👫 Совместимость по фото двух людей
🃏 Таро по фото реальных карт
⭐️ Персональный гороскоп по дате рождения каждое утро

🌙 Каждое утро всем — лунный календарь и советы дня
🎤 Можно общаться голосовыми — бот всё поймёт!

🎁 Бесплатно: 15 запросов + 20 сообщений психологу

Выбери с чего начнём 👇"""

async def generate_text(system, user_content, model="gpt-4o-mini"):
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content}
        ],
        max_tokens=1500
    )
    return response.choices[0].message.content

async def generate_with_history(system, history, new_message):
    messages = [{"role": "system", "content": system}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": new_message})
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=1500
    )
    return response.choices[0].message.content

async def generate_with_photo(system_prompt, image_url):
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": system_prompt}
            ]
        }],
        max_tokens=1500
    )
    return response.choices[0].message.content

async def generate_with_claude_photo(system_prompt, image_url):
    """Используем Claude для фото-анализов — он не отказывает от хиромантии и эзотерики"""
    import httpx
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(image_url)
        image_data = response.content
        import base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')

    response = await asyncio.to_thread(
        claude_client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_base64
                    }
                },
                {"type": "text", "text": system_prompt}
            ]
        }]
    )
    return response.content[0].text

async def transcribe_voice(file_path):
    with open(file_path, "rb") as audio_file:
        response = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru"
        )
    return response.text

# ========== ФОНОВАЯ ПРОВЕРКА ОПЛАТЫ ==========
async def check_payments_loop():
    while True:
        await asyncio.sleep(15)
        try:
            pending = get_pending_payments()
            for payment_id, user_id, plan in pending:
                try:
                    payment = Payment.find_one(payment_id)
                    if payment.status == "succeeded":
                        set_subscription(user_id, plan, 30)
                        delete_pending_payment(payment_id)
                        asyncio.create_task(asyncio.to_thread(sheets_update_subscription, user_id, plan))
                        plan_name = "🟢 Старт" if plan == "aura_start" else "🔥 Про"
                        await bot.send_message(
                            user_id,
                            f"✅ Оплата прошла успешно!\n\nТариф {plan_name} активирован на 30 дней.\n\nПользуйся на здоровье! 🔮",
                            reply_markup=main_menu()
                        )
                    elif payment.status == "canceled":
                        delete_pending_payment(payment_id)
                        await bot.send_message(user_id, "❌ Платёж отменён. Попробуй снова.", reply_markup=main_menu())
                except Exception as e:
                    logging.error(f"Ошибка проверки платежа {payment_id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка в check_payments_loop: {e}")

# ========== ЕЖЕДНЕВНЫЙ ГОРОСКОП ==========
async def daily_horoscope_loop():
    while True:
        now = datetime.now()
        # Отправляем в 8:00 утра
        next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= next_8am:
            next_8am += timedelta(days=1)
        wait_seconds = (next_8am - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # Отправляем гороскоп всем Про пользователям с датой рождения
        conn = sqlite3.connect("/root/aura.db")
        c = conn.cursor()
        c.execute("""SELECT u.user_id, u.birth_date FROM users u
                     JOIN subscriptions s ON u.user_id = s.user_id
                     WHERE s.plan = 'aura_pro' AND s.sub_end > ? AND u.birth_date != ''""",
                  (datetime.now().isoformat(),))
        pro_users = c.fetchall()
        conn.close()

        # Лунный календарь - всем пользователям
        try:
            today = datetime.now().strftime("%d.%m.%Y")
            lunar_text = await generate_text(
                LUNAR_SYSTEM,
                f"Сегодня {today}. Составь лунный прогноз: фаза луны, день лунного цикла, что благоприятно делать, чего избегать, совет дня."
            )
            conn2 = sqlite3.connect("/root/aura.db")
            c2 = conn2.cursor()
            c2.execute("SELECT user_id FROM users")
            all_users = c2.fetchall()
            conn2.close()
            for (uid,) in all_users:
                try:
                    await bot.send_message(uid, f"🌙 Лунный календарь на {today}\n\n{lunar_text}")
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Ошибка лунного календаря: {e}")

        # Персональный гороскоп - только Про
        for user_id, birth_date in pro_users:
            try:
                today = datetime.now().strftime("%d.%m.%Y")
                text = await generate_text(
                    HOROSCOPE_SYSTEM,
                    f"Дата рождения: {birth_date}\n\nСоставь персональный гороскоп на сегодня {today} по дате рождения. Расскажи о энергии дня, главной теме и совете."
                )
                await bot.send_message(user_id, f"⭐️ Твой персональный гороскоп на {today}\n\n{text}")
            except Exception as e:
                logging.error(f"Ошибка отправки гороскопа {user_id}: {e}")

# ========== КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    get_user(user_id, message.from_user.username or "", message.from_user.first_name or "")
    set_step(user_id, "idle")
    name = message.from_user.first_name or "друг"
    asyncio.create_task(asyncio.to_thread(sheets_add_user, user_id, message.from_user.username, message.from_user.first_name))
    await message.answer(WELCOME_TEXT.format(name=name), reply_markup=main_menu())

@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    reset_limits_all()
    await message.answer("✅ Лимиты сброшены!")

@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /activate user_id plan\nПример: /activate 123456 aura_pro")
        return
    user_id = int(parts[1])
    plan = parts[2]
    set_subscription(user_id, plan, 30)
    asyncio.create_task(asyncio.to_thread(sheets_update_subscription, user_id, plan))
    await message.answer(f"✅ Подписка {plan} активирована для {user_id} на 30 дней!")

@dp.callback_query(F.data == "back_menu")
async def back_to_menu(callback: CallbackQuery):
    set_step(callback.from_user.id, "idle")
    name = callback.from_user.first_name or "друг"
    await callback.message.answer(WELCOME_TEXT.format(name=name), reply_markup=main_menu())
    await callback.answer()

# ========== ТАРИФЫ И ОПЛАТА ==========
@dp.callback_query(F.data == "tariffs")
async def tariffs(callback: CallbackQuery):
    plan, sub_end = get_subscription(callback.from_user.id)
    current = ""
    if plan == "aura_start":
        current = f"\n\n✅ Твой тариф: 🟢 Старт (до {sub_end.strftime('%d.%m.%Y')})"
    elif plan == "aura_pro":
        current = f"\n\n✅ Твой тариф: 🔥 Про (до {sub_end.strftime('%d.%m.%Y')})"

    await callback.message.answer(
        f"💎 Тарифы AuraBot\n\n"
        f"🟢 Старт — 190 руб / 1 месяц\n"
        f"Нумерология, Таро, Сны, Аура, Гороскоп — безлимит\n"
        f"Совместимость, Голосовой дневник — безлимит\n"
        f"Хиромантия, Физиогномика, Графология — по 5 раз\n"
        f"AI-Психолог — 100 сообщений\n\n"
        f"🔥 Про — 390 руб / 1 месяц\n"
        f"Всё из Старта без ограничений ПЛЮС:\n"
        f"Матрица судьбы, Прогноз, Натальная карта\n"
        f"Денежный код, Хиромантия, Физиогномика\n"
        f"Графология, Таро по фото, Совместимость по фото\n"
        f"Персональный гороскоп по дате рождения каждое утро\n"
        f"AI-Психолог — безлимит\n\n"
        f"🌙 Всем бесплатно: лунный календарь каждое утро\n\n"
        f"🎁 Бесплатно: 15 запросов на всё (включая фото) + 20 сообщений психологу{current}",
        reply_markup=tariffs_menu()
    )
    await callback.answer()

async def create_payment(user_id, plan):
    amount = "190.00" if plan == "aura_start" else "390.00"
    plan_name = "Старт" if plan == "aura_start" else "Про"
    payment = Payment.create({
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://t.me/AuraBotTest_Bot"},
        "capture": True,
        "description": f"AuraBot Тариф {plan_name} — пользователь {user_id}",
        "receipt": {
            "customer": {"email": "client@aurabot.ru"},
            "items": [{
                "description": f"AuraBot Тариф {plan_name} 30 дней",
                "quantity": "1.00",
                "amount": {"value": amount, "currency": "RUB"},
                "vat_code": 1,
                "payment_subject": "service",
                "payment_mode": "full_payment"
            }]
        },
        "metadata": {"user_id": user_id, "plan": plan}
    }, str(uuid.uuid4()))
    return payment

@dp.callback_query(F.data == "pay_start")
async def pay_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()
    try:
        payment = await create_payment(user_id, "aura_start")
        save_pending_payment(payment.id, user_id, "aura_start")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 190 руб", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
        ])
        await callback.message.answer(
            "🟢 Тариф Старт — 190 руб / 30 дней\n\nНажми кнопку для оплаты.\nПодписка активируется автоматически! ✅",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Ошибка платежа: {e}")
        await callback.message.answer(f"❌ Ошибка при создании платежа. Обратись в поддержку: {SUPPORT_URL}")

@dp.callback_query(F.data == "pay_pro")
async def pay_pro(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()
    try:
        payment = await create_payment(user_id, "aura_pro")
        save_pending_payment(payment.id, user_id, "aura_pro")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 390 руб", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton(text="🔙 В меню", callback_data="back_menu")]
        ])
        await callback.message.answer(
            "🔥 Тариф Про — 390 руб / 30 дней\n\nНажми кнопку для оплаты.\nПодписка активируется автоматически! ✅",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Ошибка платежа: {e}")
        await callback.message.answer(f"❌ Ошибка при создании платежа. Обратись в поддержку: {SUPPORT_URL}")

# ========== ФУНКЦИИ БОТА ==========
async def handle_limit(callback, access):
    if access == "limit_free":
        await callback.message.answer(
            "🚫 Бесплатные запросы закончились (15 из 15).\n\nОформи подписку чтобы продолжить 👇",
            reply_markup=upgrade_menu()
        )
    elif access == "limit_psycho_free":
        await callback.message.answer(
            "🚫 Бесплатные сообщения психологу закончились (30 из 30).\n\nОформи подписку 👇",
            reply_markup=upgrade_menu()
        )
    elif access == "limit_psycho_start":
        await callback.message.answer(
            "🚫 Лимит психолога на тарифе Старт исчерпан (100 сообщений).\n\nПерейди на Про 👇",
            reply_markup=upgrade_menu("start")
        )
    elif access == "limit_photo":
        await callback.message.answer(
            "🚫 Лимит фото-анализов на тарифе Старт исчерпан (5 раз).\n\nПерейди на Про для безлимита 👇",
            reply_markup=upgrade_menu("start")
        )
    elif access == "diary_blocked":
        await callback.message.answer(
            "📔 Голосовой дневник доступен с тарифа 🟢 Старт.\n\n190 руб/мес — открой доступ 👇",
            reply_markup=upgrade_menu()
        )
    elif access == "start_block":
        await callback.message.answer(
            "🔒 Эта функция доступна только на тарифе 🔥 Про.\n\n390 руб/мес — всё без ограничений 👇",
            reply_markup=upgrade_menu("start")
        )

@dp.callback_query(F.data == "numerology")
async def numerology(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "numerology")
    await callback.message.answer(
        "🔢 Нумерология\n\nВведи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "matrix")
async def matrix(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "matrix")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "matrix")
    await callback.message.answer(
        "🌌 Матрица Судьбы\n\nВведи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "taro")
async def taro(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "taro")
    await callback.message.answer(
        "🃏 Таро — расклад на 3 карты\n\nНапиши свой вопрос или опиши ситуацию которая тебя беспокоит.",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "dreams")
async def dreams(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "dreams")
    await callback.message.answer(
        "💤 Толкование снов\n\nОпиши свой сон как можно подробнее: что происходило, кто был, какие образы, какие чувства.",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "aura")
async def aura(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "aura")
    await callback.message.answer(
        "🌈 Чтение Ауры\n\nВведи дату рождения в формате ДД.ММ.ГГГГ\nНапример: 15.03.1990",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "forecast")
async def forecast(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "forecast")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "forecast")
    await callback.message.answer(
        "📅 Нумерологический прогноз\n\nВведи дату рождения и период в одном сообщении:\n\n15.03.1990, месяц\nИли: 15.03.1990, год",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "compatibility")
async def compatibility(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "compatibility")
    await callback.message.answer(
        "❤️ Совместимость\n\nВведи данные обоих людей в одном сообщении:\n\nМария 15.03.1990 Александр 22.07.1988",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "natal")
async def natal(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "natal")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "natal")
    await callback.message.answer(
        "♈ Натальная карта\n\nВведи три вещи в одном сообщении через пробел:\n\n15.03.1990 14:30 Москва\n\nЕсли не знаешь время — напиши 00:00",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "horoscope")
async def horoscope(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "general")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "horoscope")
    await callback.message.answer(
        "🌟 Гороскоп на сегодня\n\nНапиши свой знак зодиака:\nНапример: Телец, Скорпион, Водолей",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "chiromancy")
async def chiromancy(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "chiromancy")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "chiromancy")
    await callback.message.answer(
        "🖐 Хиромантия\n\nПришли фото ладони:\n— Хорошее освещение\n— Ладонь вверх, пальцы расслаблены\n— Лучше правая рука\n— Без украшений",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "physio")
async def physio(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "physio")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "physio")
    await callback.message.answer(
        "😊 Физиогномика\n\nПришли фото лица:\n— Анфас, смотри прямо в камеру\n— Хорошее освещение без теней\n— Без фильтров\n— Лицо занимает большую часть кадра",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "grapho")
async def grapho(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "grapho")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "grapho")
    await callback.message.answer(
        "✍️ Графология\n\nНапиши от руки 5-7 предложений на белом листе и пришли фото:\n— Пиши как обычно\n— Хорошее освещение\n— Чёткое фото",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "psycho")
async def psycho(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "psycho")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "psycho")
    history = get_psycho_history(user_id)
    if history:
        await callback.message.answer(
            "🧠 AI-Психолог\n\nПродолжаем наш разговор. Я помню всё что ты рассказывал.\n\nНапиши что тебя беспокоит.",
            reply_markup=psycho_menu()
        )
    else:
        await callback.message.answer(
            "🧠 AI-Психолог\n\nЯ здесь чтобы выслушать и помочь разобраться.\n\nРасскажи что тебя беспокоит прямо сейчас.",
            reply_markup=psycho_menu()
        )
    await callback.answer()

@dp.callback_query(F.data == "psycho_new")
async def psycho_new(callback: CallbackQuery):
    user_id = callback.from_user.id
    clear_psycho_history(user_id)
    set_step(user_id, "psycho")
    await callback.message.answer(
        "🧠 Начинаем новый разговор.\n\nРасскажи что тебя беспокоит.",
        reply_markup=psycho_menu()
    )
    await callback.answer()

# ========== ОБРАБОТКА ТЕКСТА ==========
@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    user = get_user(user_id)
    step = user.get("step", "")

    if not step or step == "idle":
        await message.answer("Выбери действие из меню 👇", reply_markup=main_menu())
        return

    # Отзыв
    if step == "review":
        set_step(user_id, "idle")
        asyncio.create_task(asyncio.to_thread(
            sheets_add_review, user_id,
            message.from_user.username,
            message.from_user.first_name,
            text
        ))
        await message.answer(
            "⭐️ Спасибо за отзыв! Мы обязательно его учтём.",
            reply_markup=main_menu()
        )
        return

    # Психолог
    if step == "psycho":
        access = await check_access(user_id, "psycho")
        if access not in ("ok", "pro"):
            await handle_limit_msg(message, access)
            return
        await message.answer("💭 Слушаю...")
        try:
            history = get_psycho_history(user_id)
            response = await generate_with_history(PSYCHO_SYSTEM, history, text)
            add_psycho_message(user_id, "user", text)
            add_psycho_message(user_id, "assistant", response)
            if access == "ok":
                increment_limit(user_id, "psycho_messages")
            await message.answer(response, reply_markup=psycho_menu())
        except Exception as e:
            await message.answer(f"Ошибка: {e}", reply_markup=back_menu())
        return

    # Все остальные текстовые функции
    step_map = {
        "numerology": (NUMEROLOGY_SYSTEM, "Дата рождения пользователя: {text}\n\nРассчитай числа судьбы, личности, души и назови. Объясни что они означают для этого человека — его миссия, характер, таланты. Говори конкретно и лично."),
        "matrix": (MATRIX_SYSTEM, "Дата рождения: {text}\n\nРассчитай Матрицу Судьбы. Расскажи о кармических задачах, предназначении, талантах и главных уроках этой жизни."),
        "taro": (TARO_SYSTEM, "Вопрос пользователя: {text}\n\nВытащи 3 карты Таро. Расклад: прошлое → настоящее → будущее/совет. Расскажи какие карты выпали и что они означают именно для этой ситуации."),
        "dreams": (DREAMS_SYSTEM, "Сон пользователя: {text}\n\nДай толкование с двух сторон: психологической (что говорит подсознание) и эзотерической (духовное послание). Говори конкретно и лично."),
        "aura": (AURA_SYSTEM, "Дата рождения: {text}\n\nРасскажи об ауре: цвет ауры, энергетика, сильные стороны, уязвимости, как работать со своей энергией."),
        "forecast": (FORECAST_SYSTEM, "Данные пользователя: {text}\n\nСоставь нумерологический прогноз. Расскажи о текущей энергии, ближайшем периоде, советах для любви, карьеры, финансов и здоровья."),
        "compatibility": (COMPATIBILITY_SYSTEM, "Данные: {text}\n\nПроанализируй совместимость двух людей: общая совместимость, сильные стороны союза, зоны роста, прогноз отношений."),
        "money_code": (MONEY_CODE_SYSTEM, "Имя и дата рождения: {text}\n\nРассчитай денежный код. Расскажи: личный денежный код (число), что означает, как активировать, три персональные аффирмации."),
        "natal": (NATAL_SYSTEM, "Данные (дата, время, место рождения): {text}\n\nПрочитай натальную карту: солнечный знак и асцендент, лунный знак, сильные планеты, сферы жизни, таланты и вызовы."),
        "horoscope": (HOROSCOPE_SYSTEM, f"Знак зодиака: {{text}}\n\nРасскажи гороскоп на сегодня {datetime.now().strftime('%d.%m.%Y')}: энергия дня, главная тема, совет дня, благоприятные действия."),
    }

    if step not in step_map:
        await message.answer("Выбери действие из меню 👇", reply_markup=main_menu())
        return

    access = await check_access(user_id, step if step in ("matrix", "forecast", "natal") else "general")
    if access not in ("ok", "pro"):
        await handle_limit_msg(message, access)
        return

    system, prompt_template = step_map[step]
    prompt = prompt_template.replace("{text}", text)
    set_step(user_id, "idle")
    await message.answer("⏳ Анализирую...")
    try:
        result = await generate_text(system, prompt)
        if access == "ok":
            increment_limit(user_id, "requests")
        asyncio.create_task(asyncio.to_thread(sheets_update_activity, user_id))
        await message.answer(result, reply_markup=back_menu())
    except Exception as e:
        await message.answer(f"Ошибка: {e}", reply_markup=back_menu())

async def handle_limit_msg(message, access):
    if access == "limit_free":
        await message.answer("🚫 Бесплатные запросы закончились (10 из 10).\n\nОформи подписку 👇", reply_markup=upgrade_menu())
    elif access in ("limit_psycho_free",):
        await message.answer("🚫 Бесплатные сообщения психологу закончились.\n\nОформи подписку 👇", reply_markup=upgrade_menu())
    elif access == "limit_psycho_start":
        await message.answer("🚫 Лимит психолога на Старте исчерпан.\n\nПерейди на Про 👇", reply_markup=upgrade_menu("start"))
    elif access == "start_block":
        await message.answer("🔒 Эта функция доступна только на тарифе 🔥 Про 👇", reply_markup=upgrade_menu("start"))

@dp.callback_query(F.data == "review")
async def review_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    set_step(user_id, "review")
    await callback.message.answer(
        "⭐️ Оставить отзыв\n\n"
        "Расскажи как тебе бот — что понравилось, что можно улучшить.\n\n"
        "Можешь написать текстом или записать голосовое:",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data == "diary")
async def diary_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "diary")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "diary")
    await callback.message.answer(
        "📔 Голосовой дневник\n\n"
        "Это твоё личное пространство — говори что думаешь и чувствуешь.\n\n"
        "Как пользоваться:\n"
        "— Нажми на микрофон и запиши голосовое\n"
        "— Расскажи как прошёл день, что на душе, что заметил\n"
        "— Я выслушаю и отражу что слышу — без советов, просто как зеркало\n\n"
        "Для советов и помощи используй 🧠 AI-Психолог\n\n"
        "🎤 Запиши голосовое прямо сейчас:",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "money_code")
async def money_code_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    access = await check_access(user_id, "money_code")
    if access not in ("ok", "pro"):
        await handle_limit(callback, access)
        await callback.answer()
        return
    set_step(user_id, "money_code")
    await callback.message.answer(
        "Денежный код\n\nВведи своё полное имя и дату рождения:\nНапример: Мария Иванова 15.03.1990",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "compat_photo")
async def compat_photo_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    plan, sub_end = get_subscription(user_id)
    if plan != "aura_pro":
        await callback.message.answer(
            "Совместимость по фото доступна только на тарифе Про.\n\n390 руб/мес — всё без ограничений",
            reply_markup=upgrade_menu("start")
        )
        await callback.answer()
        return
    set_step(user_id, "compat_photo")
    await callback.message.answer(
        "Совместимость по фото\n\nПришли фото где видны оба человека.\nЯ проанализирую совместимость по чертам лица и энергетике.",
        reply_markup=back_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "taro_photo")
async def taro_photo(callback: CallbackQuery):
    user_id = callback.from_user.id
    plan, sub_end = get_subscription(user_id)
    if plan != "aura_pro":
        await callback.message.answer(
            "Таро по фото карт доступно только на тарифе Про.\n\n390 руб/мес — всё без ограничений",
            reply_markup=upgrade_menu("start")
        )
        await callback.answer()
        return
    set_step(user_id, "taro_photo")
    await callback.message.answer(
        "Таро по фото карт\n\nВытащи карты и сфотографируй их.\nЯ прочитаю расклад по реальным картам на фото!\n\nПришли фото карт:",
        reply_markup=back_menu()
    )
    await callback.answer()

# ========== ОБРАБОТКА ФОТО ==========
@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
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
        await message.answer("Выбери функцию из меню чтобы отправить фото 👇", reply_markup=main_menu())
        return

    system, feature, limit_field = photo_steps[step]
    access = await check_access(user_id, feature)
    if access not in ("ok", "pro"):
        kb = upgrade_menu("start") if access == "limit_photo" else upgrade_menu()
        await message.answer(
            "🚫 Лимит фото-анализов исчерпан.\n\nОформи подписку 👇" if access == "limit_free" else
            "🚫 Лимит фото-анализов на тарифе Старт исчерпан (5 раз).\n\nПерейди на Про 👇",
            reply_markup=kb
        )
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"

    set_step(user_id, "idle")
    await message.answer("⏳ Анализирую фото...")
    try:
        result = await generate_with_claude_photo(system, file_url)
        if access == "ok":
            increment_limit(user_id, "requests")
        else:
            increment_limit(user_id, limit_field)
        await message.answer(result, reply_markup=back_menu())
    except Exception as e:
        await message.answer(f"Ошибка анализа: {e}", reply_markup=back_menu())

# ========== ОБРАБОТКА ГОЛОСОВЫХ ==========
@dp.message(F.voice)
async def handle_voice(message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    step = user.get("step", "")

    if not step or step == "idle":
        await message.answer("Выбери функцию из меню, потом отправь голосовое 👇", reply_markup=main_menu())
        return

    await message.answer("🎤 Распознаю голосовое...")
    try:
        file = await bot.get_file(message.voice.file_id)
        file_path = f"/tmp/voice_{user_id}.ogg"
        await bot.download_file(file.file_path, file_path)
        text = await transcribe_voice(file_path)
        await message.answer(f"📝 Распознал: {text}\n\n⏳ Обрабатываю...")

        # Создаём фейковое сообщение с текстом и обрабатываем
        # Отзыв голосом
        if step == "review":
            set_step(user_id, "idle")
            asyncio.create_task(asyncio.to_thread(
                sheets_add_review, user_id,
                message.from_user.username,
                message.from_user.first_name,
                f"[Голосовой] {text}"
            ))
            await message.answer(
                "⭐️ Спасибо за голосовой отзыв! Мы обязательно его учтём.",
                reply_markup=main_menu()
            )
            return

        # Дневник - особая обработка
        if step == "diary":
            await message.answer(f"Запись: {text}\n\nАнализирую...")
            history = get_diary_history(user_id)
            history_ctx = ""
            if history:
                history_ctx = "\n\nПредыдущие записи:"
                for entry, resp, date in history[-3:]:
                    history_ctx += f"\n[{date[:10]}] {entry[:80]}"
            response = await generate_text(DIARY_SYSTEM, f"Запись в дневник: {text}{history_ctx}")
            add_diary_entry(user_id, text, response)
            await message.answer(response, reply_markup=back_menu())
            return

        message.text = text
        await handle_text(message)
    except Exception as e:
        await message.answer(f"Ошибка распознавания голоса: {e}", reply_markup=back_menu())

# ========== MAIN ==========
async def main():
    init_db()
    asyncio.create_task(check_payments_loop())
    asyncio.create_task(daily_horoscope_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
