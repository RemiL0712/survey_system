from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
import httpx

from app.bot.keyboards import kb_admin_request
from app.bot.services.api import resolve_group_name, resolve_user_display


async def safe_edit(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        await message.answer(text, reply_markup=reply_markup)


async def build_join_request_message(
    client: httpx.AsyncClient,
    req_id: int,
    group_id: int,
    user_id: int,
    tg_username: str | None = None,
    tg_id_override: int | None = None,
) -> str:
    group_name = await resolve_group_name(client, group_id)

    if tg_username:
        uname = f"@{tg_username}"
        tg_id = tg_id_override
    else:
        uname, tg_id = await resolve_user_display(client, user_id)

    text = (
        "🕓 Pending заявка\n"
        f"Request ID: {req_id}\n"
        f"Група: {group_name} (id={group_id})\n"
        f"Користувач: {uname}"
    )
    if tg_id:
        text += f"\nTelegram ID: {tg_id}"
    return text


async def send_admin_join_request(
    bot: Bot,
    admin_tg_id: int,
    client: httpx.AsyncClient,
    req_id: int,
    group_id: int,
    user_id: int,
    join_req_cache: dict[int, dict],
    admin_req_message_id: dict[int, int],
    tg_username: str | None = None,
    tg_id_override: int | None = None,
) -> None:
    if not admin_tg_id:
        return

    text = await build_join_request_message(
        client,
        req_id,
        group_id,
        user_id,
        tg_username=tg_username,
        tg_id_override=tg_id_override,
    )

    msg = await bot.send_message(
        int(admin_tg_id),
        text,
        reply_markup=kb_admin_request(int(req_id)),
    )

    join_req_cache[int(req_id)] = {"username": tg_username, "telegram_id": tg_id_override}
    admin_req_message_id[int(req_id)] = msg.message_id