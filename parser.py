import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Вставьте сюда токен от BotFather
BOT_TOKEN = "8899214278:AAFmTBuv2pjAEZvw94WRHvUfIsYR7havHfA"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище временных данных пользователей: user_id -> {link, number}
user_data = {}

def make_keyboard(number: int):
    builder = InlineKeyboardBuilder()
    # Кнопки «Назад» и «Далее» передают номер через callback_data
    builder.button(text="🔙 Назад", callback_data=f"back_{number}")
    builder.button(text="▶️ Далее", callback=f"next_{number}")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_data[user_id] = {"link": None, "number": None}

    await message.answer(
        "👋 Привет! Я помогу сформировать ссылку на NFT.\n\n"
        "Отправьте команду /search, затем пришлите ссылку в формате:\n"
        "`t.me/nft/названиенфт`\n\n"
        "После этого укажите номер (целое число).",
        parse_mode="Markdown"
    )

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    user_id = message.from_user.id
    user_data[user_id] = {"link": None, "number": None}
    await message.answer("Пожалуйста, отправьте ссылку в формате: `t.me/nft/названиенфт`", parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Если пользователь ещё не начал процесс — ничего не делаем
    if user_id not in user_data:
        return

    data = user_data[user_id]

    # Шаг 1: ждём ссылку
    if data["link"] is None:
        # Простая проверка формата: начинается с t.me/nft/
        if not text.startswith("t.me/nft/"):
            await message.answer("Ссылка должна быть в формате: `t.me/nft/названиенфт`. Попробуйте ещё раз.", parse_mode="Markdown")
            return
        data["link"] = text
        await message.answer("Теперь пришлите номер (целое число), например: `123`", parse_mode="Markdown")
        return

    # Шаг 2: ждём номер
    if data["number"] is None:
        try:
            number = int(text)
        except ValueError:
            await message.answer("Номер должен быть целым числом. Попробуйте ещё раз.")
            return
        data["number"] = number

        # Формируем итоговую ссылку
        base_link = data["link"].rstrip("/")
        full_link = f"{base_link}-{number}"
        text_to_send = (
            f"🖼 Название NFT: {full_link}\n\n"
            f"Ссылка: [{full_link}]({full_link})"
        )

        sent_message = await message.answer(
            text_to_send,
            parse_mode="Markdown",
            reply_markup=make_keyboard(number)
        )
        # Сохраняем ID отправленного сообщения, чтобы потом редактировать
        user_data[user_id]["msg_id"] = sent_message.message_id
        return

@dp.callback_query(F.data.startswith("back_"))
async def cb_back(callback: types.CallbackQuery):
    number = int(callback.data.split("_")[1])
    new_number = max(1, number - 1)  # чтобы не уйти в 0 или отрицательные
    await update_nft_message(callback, new_number)

@dp.callback_query(F.data.startswith("next_"))
async def cb_next(callback: types.CallbackQuery):
    number = int(callback.data.split("_")[1])
    new_number = number + 1
    await update_nft_message(callback, new_number)

async def update_nft_message(callback: types.CallbackQuery, new_number: int):
    user_id = callback.from_user.id
    if user_id not in user_data or "link" not in user_data[user_id]:
        await callback.answer("Сессия истекла или данные утеряны. Используйте /start", show_alert=True)
        return

    data = user_data[user_id]
    base_link = data["link"].rstrip("/")
    full_link = f"{base_link}-{new_number}"

    text_to_edit = (
        f"🖼 Название NFT: {full_link}\n\n"
        f"Ссылка: [{full_link}]({full_link})"
    )

    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=data["msg_id"],
            text=text_to_edit,
            parse_mode="Markdown",
            reply_markup=make_keyboard(new_number)
        )
        await callback.answer()
    except Exception as e:
        # Например, если сообщение уже нельзя редактировать (слишком старое)
        await callback.answer(f"Не удалось обновить сообщение: {e}", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
