from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main(is_admin: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Групи", callback_data="groups")
    if is_admin:
        kb.button(text="🕓 Pending заявки", callback_data="pending")
        kb.button(text="⚙️ Керування групами", callback_data="admin_groups")
    kb.adjust(1)
    return kb.as_markup()


def kb_groups(groups: list[dict]):
    kb = InlineKeyboardBuilder()
    for g in groups:
        kb.button(text=g["name"], callback_data=f"group:{g['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="back")
    return kb.as_markup()


def kb_group_actions(group_id: int, is_admin: bool, is_member: bool, join_pending: bool = False):
    kb = InlineKeyboardBuilder()

    if is_admin or is_member:
        kb.button(text="🧪 Опитування", callback_data=f"surveys:{group_id}")

    if not is_admin and not is_member:
        if join_pending:
            kb.button(text="⏳ Заявка на розгляді", callback_data="noop")
        else:
            kb.button(text="➕ Подати заявку", callback_data=f"join:{group_id}")

    if is_admin:
        kb.button(text="👥 Учасники", callback_data=f"members:{group_id}")

    kb.button(text="⬅️ До груп", callback_data="groups")
    kb.adjust(1)
    return kb.as_markup()


def kb_admin_request(req_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтвердити", callback_data=f"approve:{req_id}")
    kb.button(text="❌ Відхилити", callback_data=f"reject:{req_id}")
    kb.adjust(2)
    return kb.as_markup()


def kb_admin_groups():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Створити групу", callback_data="group_create")
    kb.button(text="✏️ Перейменувати групу", callback_data="group_rename_pick")
    kb.button(text="🗑 Видалити групу", callback_data="group_delete_pick")
    kb.button(text="⬅️ Назад", callback_data="back")
    kb.adjust(1)
    return kb.as_markup()


def kb_groups_pick(groups: list[dict], prefix: str):
    kb = InlineKeyboardBuilder()
    for g in groups:
        kb.button(text=g["name"], callback_data=f"{prefix}:{g['id']}")
    kb.button(text="⬅️ Назад", callback_data="admin_groups")
    kb.adjust(1)
    return kb.as_markup()


def kb_confirm_delete(group_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"delete_confirm:{group_id}")
    kb.button(text="❌ Скасувати", callback_data="admin_groups")
    kb.adjust(1)
    return kb.as_markup()


def kb_surveys_back(group_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"group:{group_id}")
    kb.adjust(1)
    return kb.as_markup()


def kb_survey_start(group_id: int, survey_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Почати", callback_data=f"survey_start:{group_id}:{survey_id}")
    kb.button(text="⬅️ Назад", callback_data=f"surveys:{group_id}")
    kb.adjust(1)
    return kb.as_markup()


def kb_surveys_list(surveys: list[dict], group_id: int, is_admin: bool = False):
    kb = InlineKeyboardBuilder()
    for s in surveys:
        kb.button(text=f"📝 {s.get('title', '(без назви)')}", callback_data=f"survey_open:{group_id}:{s['id']}")
    if is_admin:
        kb.button(text="➕ Створити опитування", callback_data=f"survey_create:{group_id}")
    kb.button(text="⬅️ Назад", callback_data=f"group:{group_id}")
    kb.adjust(1)
    return kb.as_markup()
