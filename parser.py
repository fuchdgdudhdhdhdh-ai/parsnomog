import asyncio
import os
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN в переменных окружения!")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME")  # Render сам подставит домен

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище. На Render при рестарте всё сбросится. Для продакшена нужен Redis.
user_state: Dict[int, Dict[str, Any]] = {}

# --- РЕНДЕР (ФОРМИРОВАНИЕ КОНТЕНТА) ---

def render_nft_text(link: str, number: int) -> str:
    full_link = f"{link.rstrip('/')}-{number}"
    return (
        f"🖼 **Название NFT:** `{full_link}`\n\n"
        f"👉 Ссылка: [{full_link}]({full_link})"
    )

def make_keyboard(number: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"back_{number}")
    builder.button(text="▶️ Далее", callback_data=f"next_{number}")
    builder.adjust(2)
    return builder.as_markup()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ СОСТОЯНИЯ ---

def get_user_data(user_id: int) -> Optional[Dict[str, Any]]:
    return user_state.get(user_id)

def set_user_link(user_id: int, link: str):
    if user_id not in user_state:
        user_state[user_id] = {"link": None, "number": None, "msg_id": None}
    user_state[user_id]["link"] = link

def set_user_number(user_id: int, number: int):
    if user_id not in user_state:
        user_state[user_id] = {"link": None, "number": None, "msg_id": None}
    user_state[user_id]["number"] = number

def save_message_id(user_id: int, msg_id: int):
    if user_id in user_state:
        user_state[user_id]["msg_id"] = msg_id

def reset_user(user_id: int):
    user_state.pop(user_id, None)

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    reset_user(message.from_user.id)
    await message.answer(
        "👋 Привет! Я генератор ссылок на NFT.\n\n"
        "1. Нажми /search\n"
        "2. Отправь ссылку вида `t.me/nft/name`\n"
        "3. Отправь номер (число)",
        parse_mode="Markdown"
    )

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    reset_user(message.from_user.id)
    await message.answer(
        "🔗 Отправь базовую ссылку в формате:\n`t.me/nft/название`",
        parse_mode="Markdown"
    )

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    data = get_user_data(user_id)

    if not data:
        return

    # Шаг 1: ждём ссылку
    if data["link"] is None:
        if not text.startswith("t.me/nft/"):
            await message.answer(
                "❌ Неверный формат!\nСсылка должна начинаться с `t.me/nft/`\nПример: `t.me/nft/mycollection`",
                parse_mode="Markdown"
            )
            return
        set_user_link(user_id, text)
        await message.answer(
            "✅ Ссылка принята!\nТеперь отправь **номер** (целое число), например: `42`",
            parse_mode="Markdown"
        )
        return

    # Шаг 2: ждём номер
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
            parse_mode="Markdown",
            reply_markup=make_keyboard(number)
        )
        save_message_id(user_id, sent_message.message_id)
        return

# --- CALLBACK QUERIES ---

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
            parse_mode="Markdown",
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

# --- WEBHOOK (ДЛЯ RENDER WEB SERVICE) ---

async def webhook_handler(request: web.Request):
    update = await request.json()
    # aiogram умеет обрабатывать обновления из вебхука через dispatch_update
    await dp.feed_update(bot, types.Update(**update))
    return web.Response(status=200)

async def on_startup(app: web.Application):
    # Устанавливаем вебхук на URL, который Render даст в переменной RENDER_EXTERNAL_HOSTNAME
    webhook_url = f"https://{WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()

app = web.Application()
app.router.add_post(WEBHOOK_PATH, webhook_handler)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    # Для Render Web Service мы запускаем aiohttp-сервер, а не polling
    web.run_app(app, port=int(os.getenv("PORT", 8000)))
