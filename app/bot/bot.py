import os
import asyncio
import logging
import httpx

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000/api/v1").rstrip("/")
BOT_ID = int(os.getenv("BOT_ID", "1"))
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))


# ---------- Keyboards ----------
def kb_main(is_admin: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Групи", callback_data="groups")
    if is_admin:
        kb.button(text="🕓 Pending заявки", callback_data="pending")
    kb.adjust(1)
    return kb.as_markup()

def kb_groups(groups: list[dict]):
    kb = InlineKeyboardBuilder()
    for g in groups:
        kb.button(text=g["name"], callback_data=f"group:{g['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="back")
    return kb.as_markup()


def kb_group_actions(group_id: int, is_admin: bool):
    kb = InlineKeyboardBuilder()
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


# ---------- API helpers ----------
async def api_post(client: httpx.AsyncClient, path: str, json: dict):
    r = await client.post(f"{API_BASE_URL}{path}", json=json, timeout=20)
    r.raise_for_status()
    return r.json()


async def api_get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    r = await client.get(f"{API_BASE_URL}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


async def api_patch(client: httpx.AsyncClient, path: str, json: dict):
    r = await client.patch(f"{API_BASE_URL}{path}", json=json, timeout=20)
    r.raise_for_status()
    return r.json()


async def get_user_id_by_tg(client: httpx.AsyncClient, telegram_id: int) -> int:
    data = await api_get(client, "/users/by-telegram", params={"telegram_id": telegram_id})
    return int(data["user_id"])


# ---------- Bot ----------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set BOT_TOKEN in .env")

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    client = httpx.AsyncClient()

    # --- /start ---
    @dp.message(CommandStart())
    async def start(m: Message):
        await api_post(
            client,
            "/telegram/register",
            {
                "bot_id": BOT_ID,
                "telegram_id": m.from_user.id,
                "username": m.from_user.username,
            },
        )
        is_admin = m.from_user.id == ADMIN_TG_ID
        await m.answer("Готово. Обери дію:", reply_markup=kb_main(is_admin))

    # --- main nav ---
    @dp.callback_query(F.data == "back")
    async def back(cb: CallbackQuery):
        is_admin = cb.from_user.id == ADMIN_TG_ID
        await cb.message.edit_text("Обери дію:", reply_markup=kb_main(is_admin))
        await cb.answer()

    @dp.callback_query(F.data == "groups")
    async def groups(cb: CallbackQuery):
        await cb.answer("Завантажую...")
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        if not groups_list:
            await cb.message.edit_text("Груп поки немає.", reply_markup=kb_main())
            return
        await cb.message.edit_text("Оберіть групу:", reply_markup=kb_groups(groups_list))

    @dp.callback_query(F.data.startswith("group:"))
    async def open_group(cb: CallbackQuery):
        group_id = int(cb.data.split(":")[1])
        is_admin = cb.from_user.id == ADMIN_TG_ID
        await cb.message.edit_text(
            f"Група #{group_id}. Дія:",
            reply_markup=kb_group_actions(group_id, is_admin),
        )
        await cb.answer()

    # --- join request ---
    @dp.callback_query(F.data.startswith("join:"))
    async def join(cb: CallbackQuery):
        await cb.answer("Відправляю...")
        group_id = int(cb.data.split(":")[1])

        try:
            user_id = await get_user_id_by_tg(client, cb.from_user.id)
            jr = await api_post(client, "/join-requests", {"user_id": user_id, "group_id": group_id})
            req_id = jr["id"]

            await cb.message.answer("Заявку відправлено адміну ✅")

            # notify admin
            if ADMIN_TG_ID:
                username = cb.from_user.username or "(no username)"
                text = (
                    "🆕 Запит на вступ\n"
                    f"User: @{username} (tg_id={cb.from_user.id})\n"
                    f"Group ID: {group_id}\n"
                    f"Request ID: {req_id}"
                )
                await bot.send_message(ADMIN_TG_ID, text, reply_markup=kb_admin_request(req_id))

        except httpx.HTTPStatusError as e:
            # friendly messages
            try:
                detail = e.response.json().get("detail", "")
            except Exception:
                detail = e.response.text

            if "Already a member" in detail:
                await cb.message.answer("Ви вже в цій групі ✅")
            elif "Request already pending" in detail:
                await cb.message.answer("Заявка вже на розгляді ⏳")
            else:
                await cb.message.answer("Не вдалося відправити заявку. Спробуйте пізніше.")
        except Exception as e:
            logging.exception("join handler error: %s", e)
            await cb.message.answer("Помилка. Спробуйте пізніше.")

    # --- members (admin) ---
    @dp.callback_query(F.data.startswith("members:"))
    async def members(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Завантажую...")
        group_id = int(cb.data.split(":")[1])

        try:
            members_list = await api_get(client, f"/groups/{group_id}/members")

            if not members_list:
                await cb.message.answer("У групі ще немає учасників.")
                return

            kb = InlineKeyboardBuilder()

            lines = []
            for m in members_list[:20]:
                uname = f"@{m.get('username')}" if m.get("username") else f"tg:{m['telegram_id']}"
                lines.append(f"• {uname}")
                kb.button(text=f"❌ {uname}", callback_data=f"kick:{group_id}:{m['user_id']}")

            kb.adjust(1)

            await cb.message.answer(
                "👥 Учасники (натисни щоб видалити):\n\n" + "\n".join(lines),
                reply_markup=kb.as_markup()
            )

        except Exception as e:
            logging.exception("members handler error: %s", e)
            await cb.message.answer("Помилка: не вдалося отримати список учасників.")

    # --- approve/reject (admin) ---
    @dp.callback_query(F.data.startswith("approve:"))
    async def approve(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Оновлюю...")
        req_id = int(cb.data.split(":")[1])

        try:
            admin_user_id = await get_user_id_by_tg(client, cb.from_user.id)
            resp = await api_patch(
                client,
                f"/join-requests/{req_id}/approve",
                {"admin_id": admin_user_id},
            )

            # Якщо вже оброблено
            if resp.get("already_processed"):
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

                try:
                    await cb.message.edit_text(
                        f"ℹ️ Заявка вже оброблена: {resp['status']}"
                    )
                except Exception:
                    pass

                return

            # Повідомити користувача
            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"✅ Вашу заявку до групи «{resp['group_name']}» прийнято."
                )
            except Exception:
                pass

            # Закрити кнопки
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            # Оновити текст
            try:
                await cb.message.edit_text(
                    f"✅ Заявку #{req_id} підтверджено"
                )
            except Exception:
                pass

        except Exception as e:
            logging.exception("approve error: %s", e)
            await cb.message.answer("Помилка approve.")


    @dp.callback_query(F.data.startswith("reject:"))
    async def reject(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Оновлюю...")
        req_id = int(cb.data.split(":")[1])

        try:
            admin_user_id = await get_user_id_by_tg(client, cb.from_user.id)
            resp = await api_patch(
                client,
                f"/join-requests/{req_id}/reject",
                {"admin_id": admin_user_id},
            )

            if resp.get("already_processed"):
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

                try:
                    await cb.message.edit_text(
                        f"ℹ️ Заявка вже оброблена: {resp['status']}"
                    )
                except Exception:
                    pass

                return

            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"❌ Вашу заявку до групи «{resp['group_name']}» відхилено."
                )
            except Exception:
                pass

            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            try:
                await cb.message.edit_text(
                    f"❌ Заявку #{req_id} відхилено"
                )
            except Exception:
                pass

        except Exception as e:
            logging.exception("reject error: %s", e)
            await cb.message.answer("Помилка reject.")


    @dp.callback_query(F.data.startswith("kick:"))
    async def kick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Видаляю...")

        _, group_id, user_id = cb.data.split(":")
        group_id = int(group_id)
        user_id = int(user_id)

        try:
            r = await client.delete(f"{API_BASE_URL}/groups/{group_id}/members/{user_id}", timeout=20)
            r.raise_for_status()
            resp = r.json()  # тут буде {"ok": true, "user_telegram_id": ..., "group_name": ...}

            await cb.message.answer("✅ Учасника видалено.")

            # повідомлення користувачу
            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"ℹ️ Вас видалено з групи «{resp['group_name']}»."
                )
            except Exception:
                pass

        except Exception as e:
            logging.exception("kick error: %s", e)
            await cb.message.answer("Помилка видалення.")

    @dp.callback_query(F.data == "pending")
    async def pending(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer("Завантажую...")
        items = await api_get(client, "/join-requests/pending", params={"bot_id": BOT_ID})

        if not items:
            await cb.message.answer("Немає pending заявок.")
            return

        # показуємо останні 20
        for it in items[:20]:
            text = (
                "🕓 Pending заявка\n"
                f"Request ID: {it['id']}\n"
                f"User ID: {it['user_id']}\n"
                f"Group: {it['group_name']} (id={it['group_id']})"
            )
            await cb.message.answer(text, reply_markup=kb_admin_request(it["id"]))


    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
