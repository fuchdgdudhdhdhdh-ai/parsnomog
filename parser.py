import asyncio
import os
import logging
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN!")

PORT = int(os.getenv("PORT", 8000))
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", RENDER_HOST)
if not WEBHOOK_HOST:
    raise ValueError("Не удалось определить хост для webhook (RENDER_EXTERNAL_HOSTNAME пуст)")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()

user_data: Dict[int, Dict[str, Any]] = {}

# --- Helpers ---
def init_user(user_id: int):
    if user_id not in user_data:
        user_data[user_id] = {"link": None, "number": None, "msg_id": None}

def get_user_data(user_id: int):
    return user_data.get(user_id)

def reset_user(user_id: int):
    user_data.pop(user_id, None)

def render_nft_text(link: str, number: int) -> str:
    full_link = f"{link.rstrip('/')}-{number}"
    return (
        f"🖼 **NFT:** `{full_link}`\n\n"
        f"🔗 Ссылка: [{full_link}]({full_link})"
    )

def make_keyboard(number: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data=f"back_{number}")
    b.button(text="▶️ Далее", callback_data=f"next_{number}")
    b.adjust(2)
    return b.as_markup()

# --- Обработчики ---
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
    await message.answer("📎 Отправь базовую ссылку в формате:\n`t.me/nft/название`")

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    data = get_user_data(user_id)

    if not data:
        return

    if data["link"] is None:
        if not text.startswith("t.me/nft/"):
            await message.answer(
                "❌ Неверный формат!\n"
                "Ссылка должна начинаться с `t.me/nft/`\n"
                "Пример: `t.me/nft/mycollection`"
            )
            return
        data["link"] = text
        await message.answer(
            "✅ Ссылка принята!\n"
            "Теперь отправь **номер** (целое положительное число), например: `42`"
        )
        return

    if data["number"] is None:
        try:
            number = int(text)
            if number < 1:
                raise ValueError
        except ValueError:
            await message.answer("❌ Номер должен быть целым положительным числом.")
            return
        data["number"] = number
        sent = await message.answer(
            render_nft_text(data["link"], number),
            reply_markup=make_keyboard(number)
        )
        data["msg_id"] = sent.message_id

async def update_nft_message(callback: types.CallbackQuery, new_number: int):
    data = get_user_data(callback.from_user.id)
    if not data or not data.get("link") or not data.get("msg_id"):
        await callback.answer("Сессия истекла. Начни заново с /start", show_alert=True)
        return
    try:
        await bot.edit_message_text(
            chat_id=callback.from_user.id,
            message_id=data["msg_id"],
            text=render_nft_text(data["link"], new_number),
            reply_markup=make_keyboard(new_number)
        )
        data["number"] = new_number
        await callback.answer()
    except TelegramBadRequest as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("back_"))
async def cb_back(callback: types.CallbackQuery):
    await update_nft_message(callback, max(1, int(callback.data.split("_")[1]) - 1))

@dp.callback_query(F.data.startswith("next_"))
async def cb_next(callback: types.CallbackQuery):
    await update_nft_message(callback, int(callback.data.split("_")[1]) + 1)

# --- Webhook ---
async def webhook_handler(request: web.Request):
    try:
        update_data = await request.json()
        uid = update_data.get("update_id")
        logger.info(f"📥 Webhook получен: update_id={uid}")
        update = types.Update(**update_data)
        await dp.feed_update(bot, update)
        logger.info(f"✅ Update {uid} обработан")
    except Exception as e:
        logger.exception(f"❌ Ошибка обработки вебхука: {e}")
    return web.Response(status=200)

async def health(request: web.Request):
    return web.Response(text="OK")

async def on_startup(app: web.Application):
    # Сначала удаляем возможный старый вебхук, чтобы не было конфликтов
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    me = await bot.get_me()
    logger.info(f"🤖 Бот @{me.username} (id={me.id}) запущен")
    logger.info(f"🔗 Webhook: {WEBHOOK_URL}")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Webhook удалён, бот остановлен")

if __name__ == "__main__":
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    logger.info(f"Запуск aiohttp на порту {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)