import os
import asyncio
import logging
import httpx

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000/api/v1").rstrip("/")
BOT_ID = int(os.getenv("BOT_ID", "1"))
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))

# ---------- State ----------
# admin_state: тільки для адмінських flows (групи + створення опитувань)
admin_state: dict[int, dict] = {}
# user_state: тільки для проходження опитувань (text answers)
user_state: dict[int, dict] = {}
join_req_cache: dict[int, dict] = {}
admin_req_message_id: dict[int, int] = {}

# ======================================================================================
# Utils / helpers (вище main) — одна логіка, один стиль повідомлень
# ======================================================================================

async def safe_edit(message: Message, text: str, reply_markup=None):
    """Edit message if possible; ignore 'message is not modified' and fall back to sending a new one."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        await message.answer(text, reply_markup=reply_markup)

# ---------- Keyboards ----------
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
        kb.button(text=f"📝 {s.get('title','(без назви)')}", callback_data=f"survey_open:{group_id}:{s['id']}")
    if is_admin:
        kb.button(text="➕ Створити опитування", callback_data=f"survey_create:{group_id}")
    kb.button(text="⬅️ Назад", callback_data=f"group:{group_id}")
    kb.adjust(1)
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

def _extract_user_id(payload: object) -> int | None:
    """Try to extract internal user id from different API response shapes."""
    if payload is None:
        return None
    if isinstance(payload, int):
        return payload
    if isinstance(payload, str):
        return int(payload) if payload.isdigit() else None
    if isinstance(payload, dict):
        for k in ("user_id", "id"):
            v = payload.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.isdigit():
                return int(v)
        if "value" in payload:
            return _extract_user_id(payload.get("value"))
        return None
    if isinstance(payload, list):
        return _extract_user_id(payload[0]) if payload else None
    return None

async def get_user_id_by_tg(
    client: httpx.AsyncClient,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> int:
    # 1) отримати user_id
    uid: int | None = None
    try:
        data = await api_get(client, "/users/by-telegram", params={"telegram_id": telegram_id})
        logging.info("BY_TELEGRAM RESPONSE: %s", data)
        uid = data.get("user_id") or data.get("id")
        if uid is not None:
            uid = int(uid)
    except httpx.HTTPStatusError as e:
        if e.response is None or e.response.status_code != 404:
            raise

    # 2) UPSERT telegram профілю (ВАЖЛИВО: робимо завжди, не тільки при 404)
    payload = {"telegram_id": telegram_id, "bot_id": BOT_ID}
    if username:
        payload["username"] = username
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name

    try:
        await api_post(client, "/telegram/register", json=payload)
    except Exception:
        # навіть якщо апдейт не пройшов — user_id нам все одно потрібен
        pass

    # 3) якщо uid не було — беремо ще раз після register
    if uid is None:
        data = await api_get(client, "/users/by-telegram", params={"telegram_id": telegram_id})
        uid = data.get("user_id") or data.get("id")
        if uid is None:
            raise RuntimeError(f"User lookup failed for telegram_id={telegram_id}: {data}")
        uid = int(uid)

    return uid
async def is_group_member(client: httpx.AsyncClient, group_id: int, user_id: int) -> bool:
    members = await api_get(client, f"/groups/{group_id}/members")
    items = members.get("members") if isinstance(members, dict) else members
    if not isinstance(items, list):
        return False
    for m in items:
        if isinstance(m, dict) and _extract_user_id(m) == user_id:
            return True
    return False

async def resolve_group_name(client: httpx.AsyncClient, group_id: int) -> str:
    try:
        groups = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        g = next((x for x in (groups or []) if int(x.get("id", -1)) == int(group_id)), None)
        if isinstance(g, dict) and g.get("name"):
            return str(g["name"])
    except Exception:
        pass
    return f"id={group_id}"

async def resolve_user_display(client: httpx.AsyncClient, user_id: int) -> tuple[str, int | None]:
    uname = f"user_id:{user_id}"
    tg_id = None

    try:
        u = await api_get(client, f"/users/{user_id}")  # <- тут зараз 404
        logging.info("USER %s RESPONSE: %s", user_id, u)

        if isinstance(u, dict) and isinstance(u.get("user"), dict):
            u = u["user"]

        tg_id = u.get("telegram_id")
        username = u.get("username") or u.get("tg_username")

        if username:
            uname = f"@{username}"
        elif tg_id:
            uname = f"tg:{tg_id}"

    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            # просто залишаємо fallback user_id:...
            logging.warning("User %s not found via /users/{id}", user_id)
        else:
            logging.exception("resolve_user_display failed for user_id=%s", user_id)
    except Exception:
        logging.exception("resolve_user_display failed for user_id=%s", user_id)

    return uname, tg_id

async def build_join_request_message(
    client: httpx.AsyncClient,
    req_id: int,
    group_id: int,
    user_id: int,
    tg_username: str | None = None,
    tg_id_override: int | None = None,
) -> str:
    group_name = await resolve_group_name(client, group_id)

    # якщо ми вже знаємо username з Telegram — використовуємо його
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
    client: httpx.AsyncClient,
    req_id: int,
    group_id: int,
    user_id: int,
    tg_username: str | None = None,
    tg_id_override: int | None = None,
) -> None:
    if not ADMIN_TG_ID:
        return

    text = await build_join_request_message(
        client, req_id, group_id, user_id,
        tg_username=tg_username,
        tg_id_override=tg_id_override,
    )

    msg = await bot.send_message(int(ADMIN_TG_ID), text, reply_markup=kb_admin_request(int(req_id)))

    # кешуємо, щоб pending міг показати те саме і щоб не дублювати
    join_req_cache[int(req_id)] = {"username": tg_username, "telegram_id": tg_id_override}
    admin_req_message_id[int(req_id)] = msg.message_id

# ======================================================================================
# main
# ======================================================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set BOT_TOKEN in .env")

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    client = httpx.AsyncClient()

    # ---------- Survey helpers (локальні для main, бо використовують client) ----------
    async def show_current_question(message: Message, session_id: int, group_id: int, tg_user_id: int):
        data = await api_get(client, f"/survey-sessions/{session_id}/current")

        if data.get("finished"):
            await message.answer("✅ Дякую! Опитування завершено.")
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
            await message.answer(text, reply_markup=kb.as_markup())
            return

        if qtype == "text":
            user_state[tg_user_id] = {
                "mode": "survey_text",
                "session_id": session_id,
                "group_id": group_id,
                "question_id": qid,
            }
            await message.answer(text + "\n\n✍️ Напиши відповідь одним повідомленням:")
            return

        await message.answer("Поки підтримуються лише single/text.")

    @dp.callback_query(F.data == "noop")
    async def noop(cb: CallbackQuery):
        await cb.answer("⏳ Заявка вже на розгляді", show_alert=False)
    # ---------- /start ----------
    @dp.message(CommandStart())
    async def start(m: Message):
        await api_post(
            client,
            "/telegram/register",
            {"bot_id": BOT_ID, "telegram_id": m.from_user.id, "username": m.from_user.username},
        )
        is_admin = m.from_user.id == ADMIN_TG_ID
        await m.answer("Готово. Обери дію:", reply_markup=kb_main(is_admin))

    # ---------- Surveys: answer single ----------
    @dp.callback_query(F.data.startswith("survey_ans:"))
    async def survey_answer(cb: CallbackQuery):
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

        await show_current_question(cb.message, session_id, group_id, cb.from_user.id)

    # ---------- Main nav ----------
    @dp.callback_query(F.data == "admin_groups")
    async def admin_groups(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return
        await cb.message.edit_text("⚙️ Керування групами:", reply_markup=kb_admin_groups())
        await cb.answer()

    @dp.callback_query(F.data == "back")
    async def back(cb: CallbackQuery):
        is_admin = cb.from_user.id == ADMIN_TG_ID
        await cb.message.edit_text("Обери дію:", reply_markup=kb_main(is_admin))
        await cb.answer()

    @dp.callback_query(F.data == "groups")
    async def groups(cb: CallbackQuery):
        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        if not groups_list:
            is_admin = cb.from_user.id == ADMIN_TG_ID
            await cb.message.edit_text("Груп поки немає.", reply_markup=kb_main(is_admin))
            return
        await cb.message.edit_text("Оберіть групу:", reply_markup=kb_groups(groups_list))

    @dp.callback_query(F.data.startswith("group:"))
    async def open_group(cb: CallbackQuery):
        group_id = int(cb.data.split(":", 1)[1])
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})
        group = next((x for x in groups_list if x.get("id") == group_id), None)
        if not group:
            await cb.answer("Групу не знайдено", show_alert=True)
            return

        is_admin = (cb.from_user.id == ADMIN_TG_ID)

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

            # спроба визначити чи є pending заявка (не критично, якщо бек не підтримує)
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
        await safe_edit(
            cb.message,
            text,
            reply_markup=kb_group_actions(group_id, is_admin=is_admin, is_member=is_member, join_pending=join_pending),
        )
        await cb.answer()

    # ---------- Surveys: list ----------
    @dp.callback_query(F.data.startswith("surveys:"))
    async def surveys_list(cb: CallbackQuery):
        await cb.answer()
        group_id = int(cb.data.split(":", 1)[1])
        is_admin = (cb.from_user.id == ADMIN_TG_ID)

        user_id = await get_user_id_by_tg(
            client,
            cb.from_user.id,
            cb.from_user.username,
            cb.from_user.first_name,
            cb.from_user.last_name,
        )

        if not is_admin:
            if not await is_group_member(client, group_id, user_id):
                await safe_edit(
                    cb.message,
                    "У вас немає доступу до опитувань цієї групи.\nПодайте заявку на вступ.",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False),
                )
                return

        data = await api_get(client, f"/groups/{group_id}/surveys", params={"user_id": user_id})
        surveys = data.get("surveys", []) if isinstance(data, dict) else (data or [])
        if not isinstance(surveys, list):
            surveys = []

        await safe_edit(
            cb.message,
            "Оберіть опитування:",
            reply_markup=kb_surveys_list(surveys, group_id, is_admin=is_admin),
        )

    # ---------- Surveys: open (preview) ----------
    @dp.callback_query(F.data.startswith("survey_open:"))
    async def survey_open(cb: CallbackQuery):
        await cb.answer()
        _, group_id_s, survey_id_s = cb.data.split(":")
        group_id = int(group_id_s)
        survey_id = int(survey_id_s)

        is_admin = (cb.from_user.id == ADMIN_TG_ID)

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
                await safe_edit(cb.message, f"✅ Ви вже пройшли: {title}", reply_markup=kb_surveys_back(group_id))
                return

        info = await api_get(client, f"/surveys/{survey_id}")
        title = info.get("title", f"Опитування #{survey_id}")
        await safe_edit(
            cb.message,
            f"{title}\n\nНатисніть «Почати», щоб розпочати.",
            reply_markup=kb_survey_start(group_id, survey_id),
        )

    # ---------- Surveys: start session ----------
    @dp.callback_query(F.data.startswith("survey_start:"))
    async def survey_start(cb: CallbackQuery):
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
        await show_current_question(cb.message, session_id, group_id, cb.from_user.id)

    # ---------- Surveys: admin create wizard ----------
    @dp.callback_query(F.data.startswith("survey_create:"))
    async def survey_create_start(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return
        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        admin_state[cb.from_user.id] = {"mode": "survey_create_title", "group_id": group_id}
        await cb.message.answer("Введіть *назву* опитування (1 повідомленням):")

    # ---------- Join request ----------
    @dp.callback_query(F.data.startswith("join:"))
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

            if await is_group_member(client, group_id, user_id):
                await safe_edit(
                    cb.message,
                    "Ви вже є учасником цієї групи ✅",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=True),
                )
                return

            jr = await api_post(client, "/join-requests", {"group_id": group_id, "user_id": user_id})
            req_id = jr.get("id")

            await safe_edit(
                cb.message,
                "✅ Заявку на вступ відправлено. Очікуйте підтвердження.",
                reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False, join_pending=True),
            )

            # ЄДИНЕ повідомлення адміну (в одному форматі, як pending list)
            if req_id:
                try:
                    await send_admin_join_request(
                        bot, client, int(req_id), group_id, user_id,
                        tg_username=cb.from_user.username,
                        tg_id_override=cb.from_user.id,
                    )
                except Exception:
                    logging.exception("Failed to notify admin about join request")

        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (400, 409):
                await safe_edit(
                    cb.message,
                    "⏳ Заявка вже існує або ви вже в групі.",
                    reply_markup=kb_group_actions(group_id, is_admin=False, is_member=False, join_pending=True),
                )
            else:
                logging.exception("join handler error")
                await cb.message.answer("Помилка. Спробуйте пізніше.")
        except Exception:
            logging.exception("join handler error")
            await cb.message.answer("Помилка. Спробуйте пізніше.")

    # ---------- Members (admin) ----------
    @dp.callback_query(F.data.startswith("members:"))
    async def members(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])

        try:
            members_list = await api_get(client, f"/groups/{group_id}/members")
            items = members_list.get("members") if isinstance(members_list, dict) else members_list

            if not items:
                await cb.message.answer("У групі ще немає учасників.")
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

            kb.adjust(1)
            await cb.message.answer(
                "👥 Учасники (натисни щоб видалити):\n\n" + "\n".join(lines),
                reply_markup=kb.as_markup(),
            )

        except Exception as e:
            logging.exception("members handler error: %s", e)
            await cb.message.answer("Помилка: не вдалося отримати список учасників.")

    # ---------- Kick (admin) ----------
    @dp.callback_query(F.data.startswith("kick:"))
    async def kick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        _, group_id, user_id = cb.data.split(":")
        group_id = int(group_id)
        user_id = int(user_id)

        try:
            r = await client.delete(f"{API_BASE_URL}/groups/{group_id}/members/{user_id}", timeout=20)
            r.raise_for_status()
            resp = r.json()

            await cb.message.answer("✅ Учасника видалено.")

            try:
                await bot.send_message(
                    resp["user_telegram_id"],
                    f"ℹ️ Вас видалено з групи «{resp['group_name']}».",
                )
            except Exception:
                pass

        except Exception as e:
            logging.exception("kick error: %s", e)
            await cb.message.answer("Помилка видалення.")

    # ---------- Approve/Reject (admin) ----------
    @dp.callback_query(F.data.startswith("approve:"))
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
            await cb.message.answer("Помилка approve.")

    @dp.callback_query(F.data.startswith("reject:"))
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
            await cb.message.answer("Помилка reject.")

    # ---------- Pending list (admin) ----------
    @dp.callback_query(F.data == "pending")
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
            await cb.message.answer("Немає pending заявок.")
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

            # потім кеш використовуй як fallback:
            cached = join_req_cache.get(req_id) or {}
            cached_username = tg_username or cached.get("username")
            cached_tg_id = tg_id or cached.get("telegram_id")

            text = await build_join_request_message(
                client, req_id, group_id, user_id,
                tg_username=cached_username,
                tg_id_override=cached_tg_id,
            )

            # якщо повідомлення вже було відправлене — оновлюємо його, а не дублюємо
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
                    # якщо не вдалось відредагувати (наприклад, видалили) — відправимо нове
                    pass

            msg = await cb.message.answer(text, reply_markup=kb_admin_request(req_id))
            admin_req_message_id[req_id] = msg.message_id

    # ---------- Admin: create group ----------
    @dp.callback_query(F.data == "group_create")
    async def group_create(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        admin_state[cb.from_user.id] = {"mode": "create_group"}
        await cb.message.answer("Введіть назву нової групи (одним повідомленням):")
        await cb.answer()

    # ---------- Admin: rename group ----------
    @dp.callback_query(F.data == "group_rename_pick")
    async def group_rename_pick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})

        if not groups_list:
            await cb.message.answer("Груп поки немає.")
            return

        await cb.message.answer(
            "Оберіть групу для перейменування:",
            reply_markup=kb_groups_pick(groups_list, "rename_group"),
        )

    @dp.callback_query(F.data.startswith("rename_group:"))
    async def rename_group_choose(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        admin_state[cb.from_user.id] = {"mode": "rename_group", "group_id": group_id}
        await cb.message.answer(f"Введіть нову назву для групи id={group_id}:")

    # ---------- Admin: delete group ----------
    @dp.callback_query(F.data == "group_delete_pick")
    async def group_delete_pick(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        groups_list = await api_get(client, "/groups", params={"bot_id": BOT_ID})

        if not groups_list:
            await cb.message.answer("Груп поки немає.")
            return

        await cb.message.answer(
            "Оберіть групу для видалення:",
            reply_markup=kb_groups_pick(groups_list, "delete_group"),
        )

    @dp.callback_query(F.data.startswith("delete_group:"))
    async def delete_group_choose(cb: CallbackQuery):
        if cb.from_user.id != ADMIN_TG_ID:
            await cb.answer("Нема доступу", show_alert=True)
            return

        await cb.answer()
        group_id = int(cb.data.split(":")[1])
        await cb.message.answer(
            f"⚠️ Видалити групу id={group_id}?",
            reply_markup=kb_confirm_delete(group_id),
        )

    @dp.callback_query(F.data.startswith("delete_confirm:"))
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
            await cb.message.answer("❌ Помилка видалення групи.")

    # ---------- Message handler ----------
    @dp.message()
    async def message_handler(m: Message):
        # 1) USER: survey text answer (працює для всіх)
        st_user = user_state.get(m.from_user.id)
        if st_user and st_user.get("mode") == "survey_text":
            session_id = int(st_user["session_id"])
            group_id = int(st_user["group_id"])
            qid = int(st_user["question_id"])

            try:
                await api_post(
                    client,
                    f"/survey-sessions/{session_id}/answer",
                    {"question_id": qid, "type": "text", "text": (m.text or "").strip()},
                )
            except Exception:
                logging.exception("Failed to submit text answer")
                await m.answer("❌ Не вдалося зберегти відповідь. Спробуйте ще раз.")
                return

            user_state.pop(m.from_user.id, None)
            await show_current_question(m, session_id, group_id, m.from_user.id)
            return

        # 2) якщо НЕ адмін — більше нічого не обробляємо
        if m.from_user.id != ADMIN_TG_ID:
            return

        st = admin_state.get(m.from_user.id)
        if not st:
            return

        text = (m.text or "").strip()
        if not text:
            await m.answer("Порожнє повідомлення. Спробуйте ще раз.")
            return

        mode = st.get("mode")

        # --- create group ---
        if mode == "create_group":
            try:
                admin_user_id = await get_user_id_by_tg(client, m.from_user.id)
                g = await api_post(client, "/groups", {"bot_id": BOT_ID, "name": text, "created_by": admin_user_id})
                await m.answer(f"✅ Групу створено: {g['name']} (id={g['id']})")
            except Exception as e:
                logging.exception("create group error: %s", e)
                await m.answer("❌ Помилка створення групи.")
            finally:
                admin_state.pop(m.from_user.id, None)
            return

        # --- rename group ---
        if mode == "rename_group":
            group_id = int(st["group_id"])
            try:
                await api_patch(client, f"/groups/{group_id}", {"name": text})
                await m.answer("✅ Назву групи змінено.")
            except Exception as e:
                logging.exception("rename group error: %s", e)
                await m.answer("❌ Помилка перейменування.")
            finally:
                admin_state.pop(m.from_user.id, None)
            return

        # --- survey create: title ---
        if mode == "survey_create_title":
            group_id = int(st["group_id"])
            try:
                admin_user_id = await get_user_id_by_tg(client, m.from_user.id)
                survey = await api_post(
                    client,
                    "/surveys",
                    {"bot_id": BOT_ID, "title": text, "description": None, "created_by": admin_user_id},
                )
                survey_id = int(survey["id"])

                # attach to group
                await api_post(client, f"/groups/{group_id}/surveys/{survey_id}", {})

                admin_state[m.from_user.id] = {
                    "mode": "survey_add_question_type",
                    "group_id": group_id,
                    "survey_id": survey_id,
                }

                await m.answer(
                    "✅ Опитування створено і прив’язано до групи.\n\n"
                    "Додамо питання.\n"
                    "Напишіть тип: single або text\n"
                    "Або напишіть: done (щоб завершити)"
                )
            except Exception as e:
                logging.exception("survey_create_title error: %s", e)
                await m.answer("❌ Помилка створення опитування.")
                admin_state.pop(m.from_user.id, None)
            return

        # --- survey create: choose question type ---
        if mode == "survey_add_question_type":
            if text.lower() == "done":
                admin_state.pop(m.from_user.id, None)
                await m.answer("✅ Готово. Опитування доступне в групі.")
                return

            qtype = text.lower().strip()
            if qtype not in ("single", "text"):
                await m.answer("Напишіть тільки: single або text (або done)")
                return

            admin_state[m.from_user.id] = {**st, "mode": "survey_add_question_text", "qtype": qtype}
            await m.answer("Введіть текст питання (1 повідомленням):")
            return

        # --- survey create: question text ---
        if mode == "survey_add_question_text":
            survey_id = int(st["survey_id"])
            group_id = int(st["group_id"])
            qtype = st["qtype"]
            qtext = text

            if qtype == "text":
                await api_post(client, f"/surveys/{survey_id}/questions", {"type": "text", "text": qtext, "options": None})
                admin_state[m.from_user.id] = {"mode": "survey_add_question_type", "group_id": group_id, "survey_id": survey_id}
                await m.answer("✅ Питання додано.\n\nДодати ще? single / text або done")
                return

            admin_state[m.from_user.id] = {**st, "mode": "survey_add_question_options", "qtext": qtext}
            await m.answer("Введіть варіанти відповіді — *кожен з нового рядка* (мінімум 2):")
            return

        # --- survey create: question options (single) ---
        if mode == "survey_add_question_options":
            survey_id = int(st["survey_id"])
            group_id = int(st["group_id"])
            qtext = st["qtext"]

            options = [line.strip() for line in (m.text or "").splitlines() if line.strip()]
            if len(options) < 2:
                await m.answer("Потрібно мінімум 2 варіанти. Спробуйте ще раз.")
                return

            await api_post(client, f"/surveys/{survey_id}/questions", {"type": "single", "text": qtext, "options": options})
            admin_state[m.from_user.id] = {"mode": "survey_add_question_type", "group_id": group_id, "survey_id": survey_id}
            await m.answer("✅ Питання додано.\n\nДодати ще? single / text або done")
            return

        admin_state.pop(m.from_user.id, None)
        await m.answer("Стан скинуто. Спробуйте ще раз.")

    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()
        try:
            await bot.session.close()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())