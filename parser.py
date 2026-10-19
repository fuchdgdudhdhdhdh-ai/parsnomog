import asyncio
import os
import logging
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN в переменных окружения!")

# Порт для Render
PORT = int(os.getenv("PORT", 8000))
# Хост для webhook: Render автоматически предоставляет RENDER_EXTERNAL_HOSTNAME
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
# Если RENDER_HOST не задан, можно использовать WEBHOOK_HOST (для локального теста)
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", RENDER_HOST)

if not WEBHOOK_HOST:
    raise ValueError("Не удалось определить хост для webhook. Задайте WEBHOOK_HOST или запустите на Render.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

# Временное хранилище (in-memory). Для продакшена замените на Redis.
user_data: Dict[int, Dict[str, Any]] = {}

# --- Вспомогательные функции ---
def get_user_data(user_id: int) -> Optional[Dict[str, Any]]:
    return user_data.get(user_id)

def init_user(user_id: int):
    if user_id not in user_data:
        user_data[user_id] = {"link": None, "number": None, "msg_id": None}

def set_user_link(user_id: int, link: str):
    init_user(user_id)
    user_data[user_id]["link"] = link

def set_user_number(user_id: int, number: int):
    init_user(user_id)
    user_data[user_id]["number"] = number

def save_message_id(user_id: int, msg_id: int):
    if user_id in user_data:
        user_data[user_id]["msg_id"] = msg_id

def reset_user(user_id: int):
    user_data.pop(user_id, None)

def render_nft_text(link: str, number: int) -> str:
    """Формирует текст сообщения с полной ссылкой."""
    full_link = f"{link.rstrip('/')}-{number}"
    return (
        f"🖼 **NFT:** `{full_link}`\n\n"
        f"🔗 Ссылка: [{full_link}]({full_link})"
    )

def make_keyboard(number: int) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру с кнопками Назад/Далее."""
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data=f"back_{number}")
    builder.button(text="▶️ Далее", callback_data=f"next_{number}")
    builder.adjust(2)
    return builder.as_markup()

# --- Обработчики команд ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    reset_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я генератор ссылок на NFT.\n\n"
        "1. Нажми /search\n"
        "2. Отправь ссылку вида `t.me/nft/name`\n"
        "3. Отправь номер (целое число)"
    )

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    reset_user(message.from_user.id)
    await message.answer(
        "📎 Отправь базовую ссылку в формате:\n`t.me/nft/название`"
    )

# --- Обработка текстовых сообщений ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    data = get_user_data(user_id)

    # Если пользователь не начинал сессию через /search, игнорируем
    if not data:
        return

    # Шаг 1: ожидаем ссылку
    if data["link"] is None:
        if not text.startswith("t.me/nft/"):
            await message.answer(
                "❌ Неверный формат!\n"
                "Ссылка должна начинаться с `t.me/nft/`\n"
                "Пример: `t.me/nft/mycollection`"
            )
            return
        set_user_link(user_id, text)
        await message.answer(
            "✅ Ссылка принята!\n"
            "Теперь отправь **номер** (целое положительное число), например: `42`"
        )
        return

    # Шаг 2: ожидаем номер
    if data["number"] is None:
        try:
            number = int(text)
            if number < 1:
                raise ValueError
        except ValueError:
            await message.answer("❌ Номер должен быть целым положительным числом. Попробуй ещё раз.")
            return
        set_user_number(user_id, number)
        text_to_send = render_nft_text(data["link"], number)
        sent_message = await message.answer(
            text_to_send,
            reply_markup=make_keyboard(number)
        )
        save_message_id(user_id, sent_message.message_id)
        return

# --- Обработка callback-запросов (кнопки) ---
async def update_nft_message(callback: types.CallbackQuery, new_number: int):
    user_id = callback.from_user.id
    data = get_user_data(user_id)

    if not data or not data.get("link"):
        await callback.answer("Сессия истекла. Начни заново с /start", show_alert=True)
        return

    base_link = data["link"].rstrip("/")
    text_to_edit = render_nft_text(base_link, new_number)
    keyboard = make_keyboard(new_number)

    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=data["msg_id"],
            text=text_to_edit,
            reply_markup=keyboard
        )
        await callback.answer()
    except TelegramBadRequest as e:
        err_str = str(e)
        if "message can't be edited" in err_str or "not found" in err_str:
            await callback.answer(
                "Не удалось обновить сообщение (удалено или старше 48 ч). Отправь номер заново.",
                show_alert=True
            )
        else:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("back_"))
async def cb_back(callback: types.CallbackQuery):
    number = int(callback.data.split("_")[1])
    new_number = max(1, number - 1)
    await update_nft_message(callback, new_number)

@dp.callback_query(F.data.startswith("next_"))
async def cb_next(callback: types.CallbackQuery):
    number = int(callback.data.split("_")[1])
    new_number = number + 1
    await update_nft_message(callback, new_number)

# --- Webhook для Render ---
async def webhook_handler(request: web.Request):
    update = await request.json()
    await dp.feed_update(bot, types.Update(**update))
    return web.Response(status=200)

async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    logger.info("Webhook удалён")

# --- Точка входа ---
if __name__ == "__main__":
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    logger.info(f"Запуск aiohttp-сервера на порту {PORT}")
    web.run_app(app, port=PORT)