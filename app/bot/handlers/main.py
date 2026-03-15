import os
import logging
import httpx

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.ui import render_menu_from_message, render_menu_from_callback, try_delete_message
from app.bot.utils.screens import (
    show_main_menu_from_message,
    show_main_menu_from_callback,
    show_admin_groups_menu,
    show_groups_menu,
    show_group_menu,
    show_surveys_menu,
    show_survey_preview,
    show_survey_completed,
    show_rename_pick,
    show_delete_pick,
    show_delete_confirm,
)

from app.bot.states import AdminStates, UserStates
from app.bot.keyboards import (
    kb_main,
    kb_groups,
    kb_group_actions,
    kb_admin_request,
    kb_admin_groups,
    kb_groups_pick,
    kb_confirm_delete,
    kb_surveys_back,
    kb_survey_start,
    kb_surveys_list,
)
from app.bot.services.api import (
    api_get,
    api_post,
    api_patch,
    get_user_id_by_tg,
    is_group_member,
)
from app.bot.utils.messages import (
    safe_edit,
    build_join_request_message,
    send_admin_join_request,
)

router = Router()

BOT_ID = int(os.getenv("BOT_ID", "1"))
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000/api/v1").rstrip("/")

# поки лишаємо це як у v4, винесемо окремим кроком
join_req_cache: dict[int, dict] = {}
admin_req_message_id: dict[int, int] = {}


async def show_current_question(
    bot: Bot,
    message: Message,
    state: FSMContext,
    client: httpx.AsyncClient,
    session_id: int,
    group_id: int,
):
    data = await api_get(client, f"/survey-sessions/{session_id}/current")

    if data.get("finished"):
        await state.clear()
        await render_menu_from_message(
            bot,
            message,
            "✅ Дякую! Опитування завершено.",
        )
        return

    q = data["question"]
    qid = int(q["id"])
    qtype = (q["type"] or "").strip().lower()
    text = q["text"]

    if qtype == "single":
        kb = InlineKeyboardBuilder()
        for opt in q.get("options", []):
            kb.button(
                text=opt["text"],
                callback_data=f"survey_ans:{session_id}:{group_id}:{qid}:{qtype}:{opt['id']}",
            )
        kb.adjust(1)

        await render_menu_from_message(
            bot,
            message,
            text,
            reply_markup=kb.as_markup(),
        )
        return

    if qtype == "text":
        await state.set_state(UserStates.survey_text)
        await state.update_data(
            session_id=session_id,
            group_id=group_id,
            question_id=qid,
        )
        await render_menu_from_message(
            bot,
            message,
            text + "\n\n✍️ Напиши відповідь одним повідомленням:",
        )
        return

    await render_menu_from_message(
        bot,
        message,
        "Поки підтримуються лише single/text.",
    )
async def render_members_menu(cb: CallbackQuery, client: httpx.AsyncClient, group_id: int):
    members_list = await api_get(client, f"/groups/{group_id}/members")
    items = members_list.get("members") if isinstance(members_list, dict) else members_list

    if not items:
        await render_menu_from_callback(
            cb,
            "У групі ще немає учасників.",
            reply_markup=kb_group_actions(group_id, is_admin=True, is_member=True),
        )
        return

    kb = InlineKeyboardBuilder()
    lines: list[str] = []

    for m in items[:20]:
        uname = (
            f"@{m.get('username')}"
            if isinstance(m, dict) and m.get("username")
            else f"tg:{m.get('telegram_id')}"
        )
        lines.append(f"• {uname}")
        kb.button(text=f"❌ {uname}", callback_data=f"kick:{group_id}:{m.get('user_id')}")

    kb.button(text="⬅️ До групи", callback_data=f"group:{group_id}")
    kb.adjust(1)

    await render_menu_from_callback(
        cb,
        "👥 Учасники (натисни щоб видалити):\n\n" + "\n".join(lines),
        reply_markup=kb.as_markup(),
    )

def register_handlers(dp, bot: Bot, client: httpx.AsyncClient):
    dp.include_router(router)

    @router.callback_query(F.data == "noop")
    async def noop(cb: CallbackQuery):
        await cb.answer("⏳ Заявка вже на розгляді", show_alert=False)

    @router.message(CommandStart())
    async def start(m: Message):
        await api_post(
            client,
            "/telegram/register",
            {"bot_id": BOT_ID, "telegram_id": m.from_user.id, "username": m.from_user.username},
        )
        is_admin = m.from_user.id == ADMIN_TG_ID
        await show_main_menu_from_message(bot, m, is_admin)

    @router.callback_query(F.data.startswith("survey_ans:"))
    async def survey_answer(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        _, session_id, group_id, qid, qtype, opt_id = cb.data.split(":")
        session_id = int(session_id)
        group_id = int(group_id)
        qid = int(qid)
        opt_id = int(opt_id)

        await api_post(
            client,
            f"/survey-sessions/{session_id}/answer",
            {"question_id": qid, "type": qtype, "option_id": opt_id, "text": None},
        )

        await show_current_question(bot, cb.message, state, client, session_id, group_id)

    @router.callback_query(F.data == "admin_groups")
    async def admin_groups(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return
        await show_admin_groups_menu(cb)
        await cb.answer()

    @router.callback_query(F.data == "back")
    async def back(cb: CallbackQuery):
        is_admin = cb.from_user.id == ADMIN_TG_ID
        await show_main_menu_from_callback(cb, is_admin, text="Обери дію:")
        await cb.answer()

    @router.callback_query(F.data == "groups")
    async def groups(cb: CallbackQuery):
        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        if not groups_list:
            is_admin = cb.from_user.id == ADMIN_TG_ID
            await show_main_menu_from_callback(cb, is_admin, text="Груп поки немає.")
            return

        await show_groups_menu(cb, groups_list)

    @router.callback_query(F.data.startswith("group:"))
    async def open_group(cb: CallbackQuery):
        group_id = int(cb.data.split(":", 1)[1])
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        group = next((x for x in groups_list if x.get("id") == group_id), None)
        if not group:
            await cb.answer("Групу не знайдено", show_alert=True)
            return

        is_admin = cb.from_user.id == ADMIN_TG_ID
        is_member = False
        join_pending = False

        if not is_admin:
            user_id = await get_user_id_by_tg(
                client,
                cb.from_user.id,
                cb.from_user.username,
                cb.from_user.first_name,
                cb.from_user.last_name,
            )
            is_member = await is_group_member(client, group_id, user_id)

            try:
                pending_items = await api_get(client, "/join-requests/pending", params={"bot_id": BOT_ID})
                if isinstance(pending_items, list):
                    for it in pending_items:
                        if int(it.get("group_id", -1)) == group_id and int(it.get("user_id", -1)) == user_id:
                            join_pending = True
                            break
            except Exception:
                pass
        else:
            is_member = True

        text = f"Група: {group['name']}\nОберіть дію:"
        await show_group_menu(
            cb,
            group_id=group_id,
            group_name=group["name"],
            is_admin=is_admin,
            is_member=is_member,
            join_pending=join_pending,
        )
        await cb.answer()

    @router.callback_query(F.data.startswith("surveys:"))
    async def surveys_list(cb: CallbackQuery):
        await cb.answer()
        group_id = int(cb.data.split(":", 1)[1])
        is_admin = cb.from_user.id == ADMIN_TG_ID

        user_id = await get_user_id_by_tg(
            client,
            cb.from_user.id,
            cb.from_user.username,
            cb.from_user.first_name,
            cb.from_user.last_name,
        )

        if not is_admin:
            if not await is_group_member(client, group_id, user_id):
                await render_menu_from_callback(
                    cb,
                    "У вас немає доступу до опитувань цієї групи.\nПодайте заявку на вступ.",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False),
                )
                return

        data = await api_get(client, f"/groups/{group_id}/surveys", params={"user_id": user_id})
        surveys = data.get("surveys", []) if isinstance(data, dict) else (data or [])
        if not isinstance(surveys, list):
            surveys = []

        await show_surveys_menu(cb, surveys, group_id, is_admin)

    @router.callback_query(F.data.startswith("survey_open:"))
    async def survey_open(cb: CallbackQuery):
        await cb.answer()
        _, group_id_s, survey_id_s = cb.data.split(":")
        group_id = int(group_id_s)
        survey_id = int(survey_id_s)

        is_admin = cb.from_user.id == ADMIN_TG_ID

        if not is_admin:
            user_id = await get_user_id_by_tg(
                client,
                cb.from_user.id,
                cb.from_user.username,
                cb.from_user.first_name,
                cb.from_user.last_name,
            )
            if not await is_group_member(client, group_id, user_id):
                await safe_edit(
                    cb.message,
                    "У вас немає доступу до цього опитування.\nПодайте заявку на вступ у групу.",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False),
                )
                return

            status = await api_get(
                client,
                "/survey-sessions/status",
                params={"survey_id": survey_id, "group_id": group_id, "user_id": user_id},
            )
            if status.get("completed") is True:
                info = await api_get(client, f"/surveys/{survey_id}")
                title = info.get("title", f"Опитування #{survey_id}")
                await show_survey_completed(cb, group_id, title)
                return

        info = await api_get(client, f"/surveys/{survey_id}")
        title = info.get("title", f"Опитування #{survey_id}")
        await show_survey_preview(cb, group_id, survey_id, title)

    @router.callback_query(F.data.startswith("survey_start:"))
    async def survey_start(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        _, group_id_s, survey_id_s = cb.data.split(":")
        group_id = int(group_id_s)
        survey_id = int(survey_id_s)

        user_id = await get_user_id_by_tg(
            client,
            cb.from_user.id,
            cb.from_user.username,
            cb.from_user.first_name,
            cb.from_user.last_name,
        )

        resp = await api_post(
            client,
            "/survey-sessions/start",
            {"survey_id": survey_id, "group_id": group_id, "user_id": user_id},
        )
        session_id = int(resp["session_id"])
        await show_current_question(bot, cb.message, state, client, session_id, group_id)

    @router.callback_query(F.data.startswith("survey_create:"))
    async def survey_create_start(cb: CallbackQuery, state: FSMContext):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        await state.set_state(AdminStates.survey_create_title)
        await state.update_data(group_id=group_id)
        await render_menu_from_callback(cb, "Введіть назву опитування одним повідомленням:")

    @router.callback_query(F.data.startswith("join:"))
    async def join(cb: CallbackQuery):
        await cb.answer()
        group_id = int(cb.data.split(":", 1)[1])

        try:
            user_id = await get_user_id_by_tg(
                client,
                cb.from_user.id,
                cb.from_user.username,
                cb.from_user.first_name,
                cb.from_user.last_name,
            )

            try:
                existing = await api_get(client, "/join-requests/pending", params={"bot_id": BOT_ID})
                if isinstance(existing, list):
                    for it in existing:
                        if int(it.get("group_id", -1)) == group_id and int(it.get("user_id", -1)) == user_id:
                            await safe_edit(
                                cb.message,
                                "⏳ Ваша заявка вже на розгляді.",
                                reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False, join_pending=True),
                            )
                            return
            except Exception:
                pass

            req = await api_post(client, "/join-requests", {"group_id": group_id, "user_id": user_id})

            req_id = int(req.get("id"))
            join_req_cache[req_id] = {
                "username": cb.from_user.username,
                "telegram_id": cb.from_user.id,
            }

            await send_admin_join_request(
                bot,
                ADMIN_TG_ID,
                client,
                req_id,
                group_id,
                user_id,
                join_req_cache=join_req_cache,
                admin_req_message_id=admin_req_message_id,
                tg_username=cb.from_user.username,
                tg_id_override=cb.from_user.id,
            )

            await safe_edit(
                cb.message,
                "✅ Заявку відправлено адміну. Очікуйте підтвердження.",
                reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False, join_pending=True),
            )

        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else None
            detail = ""
            try:
                detail = str(e.response.json())
            except Exception:
                detail = str(e)

            if code == 409:
                await safe_edit(
                    cb.message,
                    "⏳ Ваша заявка вже існує або ви вже в групі.",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False, join_pending=True),
                )
                return

            logging.exception("join request failed: %s", detail)
            await render_menu_from_callback(cb, "❌ Не вдалося створити заявку.")
        except Exception as e:
            logging.exception("join request error: %s", e)
            await render_menu_from_callback(cb, "❌ Помилка при створенні заявки.")

    @router.callback_query(F.data.startswith("members:"))
    async def members(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])

        try:
            await render_members_menu(cb, client, group_id)
        except Exception as e:
            logging.exception("members handler error: %s", e)
            await render_menu_from_callback(cb, "Помилка: не вдалося отримати список учасників.")

    @router.callback_query(F.data.startswith("kick:"))
    async def kick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Учасника видалено")
        _, group_id, user_id = cb.data.split(":")
        group_id = int(group_id)
        user_id = int(user_id)

        try:
            r = await client.delete(f"{API_BASE_URL}/groups/{group_id}/members/{user_id}", timeout=20)
            r.raise_for_status()
            resp = r.json()

            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"ℹ️ Вас видалено з групи «{resp['group_name']}».",
                )
            except Exception:
                pass

            await render_members_menu(cb, client, group_id)

        except Exception as e:
            logging.exception("kick error: %s", e)
            await render_menu_from_callback(cb, "❌ Помилка видалення.")

    @router.callback_query(F.data.startswith("approve:"))
    async def approve(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        req_id = int(cb.data.split(":")[1])

        try:
            admin_user_id = await get_user_id_by_tg(client, cb.from_user.id)
            resp = await api_patch(client, f"/join-requests/{req_id}/approve", {"admin_id": admin_user_id})

            if resp.get("already_processed"):
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                try:
                    await cb.message.edit_text(f"ℹ️ Заявка вже оброблена: {resp['status']}")
                except Exception:
                    pass
                return

            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"✅ Вашу заявку до групи «{resp['group_name']}» прийнято.",
                )
            except Exception:
                pass

            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            try:
                await cb.message.edit_text(f"✅ Заявку #{req_id} підтверджено")
            except Exception:
                pass

        except Exception as e:
            logging.exception("approve error: %s", e)
            await render_menu_from_callback(cb, "Помилка approve.")

    @router.callback_query(F.data.startswith("reject:"))
    async def reject(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        req_id = int(cb.data.split(":")[1])

        try:
            admin_user_id = await get_user_id_by_tg(client, cb.from_user.id)
            resp = await api_patch(client, f"/join-requests/{req_id}/reject", {"admin_id": admin_user_id})

            if resp.get("already_processed"):
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                try:
                    await cb.message.edit_text(f"ℹ️ Заявка вже оброблена: {resp['status']}")
                except Exception:
                    pass
                return

            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"❌ Вашу заявку до групи «{resp['group_name']}» відхилено.",
                )
            except Exception:
                pass

            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            try:
                await cb.message.edit_text(f"❌ Заявку #{req_id} відхилено")
            except Exception:
                pass

        except Exception as e:
            logging.exception("reject error: %s", e)
            await render_menu_from_callback(cb, "Помилка reject.")

    @router.callback_query(F.data == "pending")
    async def pending(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        items_raw = await api_get(client, "/join-requests/pending", params={"bot_id": BOT_ID})

        if isinstance(items_raw, dict):
            items = items_raw.get("items") or items_raw.get("pending") or items_raw.get("requests") or []
        else:
            items = items_raw or []

        if not items:
            await render_menu_from_callback(cb, "Немає pending заявок.")
            return

        for it in items[:50]:
            if not isinstance(it, dict):
                continue

            try:
                req_id = int(it.get("id"))
                user_id = int(it.get("user_id") or it.get("applicant_id") or it.get("user"))
                group_id = int(it.get("group_id") or it.get("group"))
            except Exception:
                continue

            tg_username = it.get("username") or it.get("tg_username")
            tg_id = it.get("telegram_id") or it.get("tg_id")
            if isinstance(tg_id, str) and tg_id.isdigit():
                tg_id = int(tg_id)

            cached = join_req_cache.get(req_id) or {}
            cached_username = tg_username or cached.get("username")
            cached_tg_id = tg_id or cached.get("telegram_id")

            text = await build_join_request_message(
                client,
                req_id,
                group_id,
                user_id,
                tg_username=cached_username,
                tg_id_override=cached_tg_id,
            )

            mid = admin_req_message_id.get(req_id)
            if mid:
                try:
                    await cb.bot.edit_message_text(
                        chat_id=ADMIN_TG_ID,
                        message_id=mid,
                        text=text,
                        reply_markup=kb_admin_request(req_id),
                    )
                    continue
                except Exception:
                    pass

            msg = await cb.bot.send_message(
                ADMIN_TG_ID,
                text,
                reply_markup=kb_admin_request(req_id),
            )
            admin_req_message_id[req_id] = msg.message_id

    @router.callback_query(F.data == "group_create")
    async def group_create(cb: CallbackQuery, state: FSMContext):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await state.set_state(AdminStates.create_group)
        await render_menu_from_callback(cb, "Введіть назву нової групи одним повідомленням:")
        await cb.answer()

    @router.callback_query(F.data == "group_rename_pick")
    async def group_rename_pick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})

        if not groups_list:
            await render_menu_from_callback(cb, "Груп поки немає.")
            return

        await show_rename_pick(cb, groups_list)

    @router.callback_query(F.data.startswith("rename_group:"))
    async def rename_group_choose(cb: CallbackQuery, state: FSMContext):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        await state.set_state(AdminStates.rename_group)
        await state.update_data(group_id=group_id)
        await render_menu_from_callback(cb, f"Введіть нову назву для групи id={group_id}:")

    @router.callback_query(F.data == "group_delete_pick")
    async def group_delete_pick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})

        if not groups_list:
            await render_menu_from_callback(cb, "Груп поки немає.")
            return

        await show_delete_pick(cb, groups_list)

    @router.callback_query(F.data.startswith("delete_group:"))
    async def delete_group_choose(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        await show_delete_confirm(cb, group_id)

    @router.callback_query(F.data.startswith("delete_confirm:"))
    async def delete_group_confirm(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])

        try:
            r = await client.delete(f"{API_BASE_URL}/groups/{group_id}", timeout=20)
            r.raise_for_status()
            await cb.message.edit_text(f"🗑 Групу id={group_id} видалено.")
        except Exception as e:
            logging.exception("delete_group_confirm error: %s", e)
            await render_menu_from_callback(cb, "❌ Помилка видалення групи.")

    @router.message(UserStates.survey_text)
    async def survey_text_answer(m: Message, state: FSMContext):
        data = await state.get_data()
        await try_delete_message(m)
        session_id = int(data["session_id"])
        group_id = int(data["group_id"])
        qid = int(data["question_id"])

        try:
            await api_post(
                client,
                f"/survey-sessions/{session_id}/answer",
                {"question_id": qid, "type": "text", "text": (m.text or "").strip()},
            )
        except Exception:
            logging.exception("Failed to submit text answer")
            await render_menu_from_message(bot, m, "❌ Не вдалося зберегти відповідь. Спробуйте ще раз.")
            return

        await show_current_question(bot, m, state, client, session_id, group_id)

    @router.message(AdminStates.create_group)
    async def create_group_message(m: Message, state: FSMContext):
        text = (m.text or "").strip()
        await try_delete_message(m)
        if not text:
            await render_menu_from_message(bot, m, "Порожнє повідомлення. Спробуйте ще раз.")
            return

        try:
            admin_user_id = await get_user_id_by_tg(client, m.from_user.id)
            g = await api_post(client, "/groups", {"bot_id": BOT_ID, "name": text, "created_by": admin_user_id})
            await render_menu_from_message(
                bot,
                m,
                f"✅ Групу створено: {g['name']} (id={g['id']})\n\nПовернуто в меню керування групами.",
                reply_markup=kb_admin_groups(),
            )
        except Exception as e:
            logging.exception("create group error: %s", e)
            await render_menu_from_message(
                bot,
                m,
                "❌ Помилка створення групи.",
                reply_markup=kb_admin_groups(),
            )
        finally:
            await state.clear()

    @router.message(AdminStates.rename_group)
    async def rename_group_message(m: Message, state: FSMContext):
        text = (m.text or "").strip()
        await try_delete_message(m)
        if not text:
            await render_menu_from_message(bot, m, "Порожнє повідомлення. Спробуйте ще раз.")
            return

        data = await state.get_data()
        group_id = int(data["group_id"])

        try:
            await api_patch(client, f"/groups/{group_id}", {"name": text})
            await render_menu_from_message(
                bot,
                m,
                "✅ Назву групи змінено.\n\nПовернуто в меню керування групами.",
                reply_markup=kb_admin_groups(),
            )
        except Exception as e:
            logging.exception("rename group error: %s", e)
            await render_menu_from_message(bot, m, "❌ Помилка перейменування.", reply_markup=kb_admin_groups())
        finally:
            await state.clear()

    @router.message(AdminStates.survey_create_title)
    async def survey_create_title_message(m: Message, state: FSMContext):
        text = (m.text or "").strip()
        await try_delete_message(m)
        if not text:
            await render_menu_from_message(bot, m, "Порожнє повідомлення. Спробуйте ще раз.")
            return

        data = await state.get_data()
        group_id = int(data["group_id"])

        try:
            admin_user_id = await get_user_id_by_tg(client, m.from_user.id)
            survey = await api_post(
                client,
                "/surveys",
                {"bot_id": BOT_ID, "title": text, "description": None, "created_by": admin_user_id},
            )
            survey_id = int(survey["id"])

            await api_post(client, f"/groups/{group_id}/surveys/{survey_id}", {})

            await state.set_state(AdminStates.survey_add_question_type)
            await state.update_data(group_id=group_id, survey_id=survey_id)

            await render_menu_from_message(
                bot,
                m,
                "✅ Опитування створено і прив’язано до групи.\n\n"
                "Додамо питання.\n"
                "Напишіть тип: single або text\n"
                "Або напишіть: done",
            )
        except Exception as e:
            logging.exception("survey_create_title error: %s", e)
            await render_menu_from_message(bot, m, "❌ Помилка створення опитування.")
            await state.clear()

    @router.message(AdminStates.survey_add_question_type)
    async def survey_add_question_type_message(m: Message, state: FSMContext):
        text = (m.text or "").strip().lower()
        await try_delete_message(m)

        if text == "done":
            await state.clear()
            await render_menu_from_message(
                bot,
                m,
                "✅ Готово. Опитування доступне в групі.\n\nПовернуто в меню керування групами.",
                reply_markup=kb_admin_groups(),
            )
            return

        if text not in ("single", "text"):
            await render_menu_from_message(bot, m, "Напишіть тільки: single або text (або done)")
            return

        await state.set_state(AdminStates.survey_add_question_text)
        await state.update_data(qtype=text)
        await render_menu_from_message(bot, m, "Введіть текст питання одним повідомленням:")

    @router.message(AdminStates.survey_add_question_text)
    async def survey_add_question_text_message(m: Message, state: FSMContext):
        text = (m.text or "").strip()
        await try_delete_message(m)
        if not text:
            await render_menu_from_message(bot, m, "Порожнє повідомлення. Спробуйте ще раз.")
            return

        data = await state.get_data()
        survey_id = int(data["survey_id"])
        group_id = int(data["group_id"])
        qtype = data["qtype"]

        if qtype == "text":
            await api_post(
                client,
                f"/surveys/{survey_id}/questions",
                {"type": "text", "text": text, "options": None},
            )
            await state.set_state(AdminStates.survey_add_question_type)
            await state.update_data(group_id=group_id, survey_id=survey_id)
            await render_menu_from_message(
                bot,
                m,
                "✅ Питання додано.\n\nДодати ще? single / text або done",
            )
            return

        await state.set_state(AdminStates.survey_add_question_options)
        await state.update_data(qtext=text)
        await render_menu_from_message(
            bot,
            m,
            "Введіть варіанти відповіді — кожен з нового рядка, мінімум 2:",
        )

    @router.message(AdminStates.survey_add_question_options)
    async def survey_add_question_options_message(m: Message, state: FSMContext):
        await try_delete_message(m)
        data = await state.get_data()
        survey_id = int(data["survey_id"])
        group_id = int(data["group_id"])
        qtext = data["qtext"]

        options = [line.strip() for line in (m.text or "").splitlines() if line.strip()]
        if len(options) < 2:
            await render_menu_from_message(bot, m, "Потрібно мінімум 2 варіанти. Спробуйте ще раз.")
            return

        await api_post(
            client,
            f"/surveys/{survey_id}/questions",
            {"type": "single", "text": qtext, "options": options},
        )
        await state.set_state(AdminStates.survey_add_question_type)
        await state.update_data(group_id=group_id, survey_id=survey_id)
        await render_menu_from_message(
            bot,
            m,
            "✅ Питання додано.\n\nДодати ще? single / text або done",
        )