from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    kb_admin_groups,
    kb_confirm_delete,
    kb_group_actions,
    kb_groups,
    kb_groups_pick,
    kb_main,
    kb_survey_start,
    kb_surveys_back,
    kb_surveys_list,
)
from app.bot.utils.ui import render_menu_from_callback, render_menu_from_message, render_menu_to_chat


async def show_main_menu_from_message(bot: Bot, message: Message, is_admin: bool, text: str = "Готово. Обери дію:"):
    return await render_menu_from_message(
        bot,
        message,
        text,
        reply_markup=kb_main(is_admin),
    )


async def show_main_menu_from_callback(cb: CallbackQuery, is_admin: bool, text: str = "Готово. Обери дію:"):
    return await render_menu_from_callback(
        cb,
        text,
        reply_markup=kb_main(is_admin),
    )


async def show_main_menu_to_chat(bot: Bot, chat_id: int, is_admin: bool, text: str = "Готово. Обери дію:"):
    return await render_menu_to_chat(
        bot,
        chat_id,
        text,
        reply_markup=kb_main(is_admin),
    )


async def show_admin_groups_menu(cb: CallbackQuery, text: str = "⚙️ Керування групами:"):
    return await render_menu_from_callback(
        cb,
        text,
        reply_markup=kb_admin_groups(),
    )


async def show_groups_menu(cb: CallbackQuery, groups_list: list[dict]):
    if not groups_list:
        return await render_menu_from_callback(cb, "Груп поки немає.")
    return await render_menu_from_callback(
        cb,
        "Оберіть групу:",
        reply_markup=kb_groups(groups_list),
    )


async def show_group_menu(
    cb: CallbackQuery,
    group_id: int,
    group_name: str,
    is_admin: bool,
    is_member: bool,
    join_pending: bool = False,
    notice: str | None = None,
):
    text = f"Група: {group_name}\nОберіть дію:"
    if notice:
        text = f"{notice}\n\n{text}"

    return await render_menu_from_callback(
        cb,
        text,
        reply_markup=kb_group_actions(
            group_id,
            is_admin=is_admin,
            is_member=is_member,
            join_pending=join_pending,
        ),
    )


async def show_surveys_menu(cb: CallbackQuery, surveys: list[dict], group_id: int, is_admin: bool):
    return await render_menu_from_callback(
        cb,
        "Оберіть опитування:",
        reply_markup=kb_surveys_list(surveys, group_id, is_admin=is_admin),
    )


async def show_survey_preview(cb: CallbackQuery, group_id: int, survey_id: int, title: str):
    return await render_menu_from_callback(
        cb,
        f"{title}\n\nНатисніть «Почати», щоб розпочати.",
        reply_markup=kb_survey_start(group_id, survey_id),
    )


async def show_survey_completed(cb: CallbackQuery, group_id: int, title: str):
    return await render_menu_from_callback(
        cb,
        f"✅ Ви вже пройшли: {title}",
        reply_markup=kb_surveys_back(group_id),
    )


async def show_rename_pick(cb: CallbackQuery, groups_list: list[dict]):
    return await render_menu_from_callback(
        cb,
        "Оберіть групу для перейменування:",
        reply_markup=kb_groups_pick(groups_list, "rename_group"),
    )


async def show_delete_pick(cb: CallbackQuery, groups_list: list[dict]):
    return await render_menu_from_callback(
        cb,
        "Оберіть групу для видалення:",
        reply_markup=kb_groups_pick(groups_list, "delete_group"),
    )


async def show_delete_confirm(cb: CallbackQuery, group_id: int):
    return await render_menu_from_callback(
        cb,
        f"⚠️ Видалити групу id={group_id}?",
        reply_markup=kb_confirm_delete(group_id),
    )
