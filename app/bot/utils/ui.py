from aiogram import Bot
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest


menu_message_cache: dict[int, int] = {}


async def render_menu_from_message(
    bot: Bot,
    source_message: Message,
    text: str,
    reply_markup=None,
):
    chat_id = source_message.chat.id
    return await render_menu_to_chat(bot, chat_id, text, reply_markup=reply_markup)


async def render_menu_to_chat(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup=None,
):
    existing_message_id = menu_message_cache.get(chat_id)

    if existing_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=existing_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            return existing_message_id
        except Exception:
            pass

    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    menu_message_cache[chat_id] = msg.message_id
    return msg.message_id


async def render_menu_from_callback(
    cb: CallbackQuery,
    text: str,
    reply_markup=None,
):
    chat_id = cb.message.chat.id
    message_id = cb.message.message_id

    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
        menu_message_cache[chat_id] = message_id
        return message_id
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            menu_message_cache[chat_id] = message_id
            return message_id
    except Exception:
        pass

    msg = await cb.message.answer(text, reply_markup=reply_markup)
    menu_message_cache[chat_id] = msg.message_id
    return msg.message_id


async def try_delete_message(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
