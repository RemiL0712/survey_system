import os
import logging
import httpx

BOT_ID = int(os.getenv("BOT_ID", "1"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000/api/v1").rstrip("/")
ADMIN_API_TOKEN = (os.getenv("ADMIN_API_TOKEN") or os.getenv("SECRET_KEY") or "").strip()


def build_api_headers() -> dict[str, str]:
    if not ADMIN_API_TOKEN:
        return {}
    return {"X-Admin-Api-Token": ADMIN_API_TOKEN}


async def api_post(client: httpx.AsyncClient, path: str, json: dict):
    r = await client.post(f"{API_BASE_URL}{path}", json=json, headers=build_api_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


async def api_get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    r = await client.get(f"{API_BASE_URL}{path}", params=params, headers=build_api_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


async def api_patch(client: httpx.AsyncClient, path: str, json: dict):
    r = await client.patch(f"{API_BASE_URL}{path}", json=json, headers=build_api_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


async def api_delete(client: httpx.AsyncClient, path: str):
    r = await client.delete(f"{API_BASE_URL}{path}", headers=build_api_headers(), timeout=20)
    r.raise_for_status()
    return r.json() if r.content else None


def extract_user_id(payload: object) -> int | None:
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
            return extract_user_id(payload.get("value"))
        return None
    if isinstance(payload, list):
        return extract_user_id(payload[0]) if payload else None
    return None


async def get_user_id_by_tg(
    client: httpx.AsyncClient,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> int:
    uid: int | None = None

    try:
        data = await api_get(client, "/users/by-telegram", params={"telegram_id": telegram_id})
        uid = data.get("user_id") or data.get("id")
        if uid is not None:
            uid = int(uid)
    except httpx.HTTPStatusError as e:
        if e.response is None or e.response.status_code != 404:
            raise

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
        pass

    if uid is None:
        data = await api_get(client, "/users/by-telegram", params={"telegram_id": telegram_id})
        uid = data.get("user_id") or data.get("id")
        if uid is None:
            raise RuntimeError(f"User lookup failed for telegram_id={telegram_id}: {data}")
        uid = int(uid)

    return uid


async def is_group_member(client: httpx.AsyncClient, group_id: int, user_id: int) -> bool:
    membership = await api_get(client, f"/groups/{group_id}/memberships/{user_id}")
    return bool(isinstance(membership, dict) and membership.get("is_member"))


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
        u = await api_get(client, f"/users/{user_id}")

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
            logging.warning("User %s not found via /users/{id}", user_id)
        else:
            logging.exception("resolve_user_display failed for user_id=%s", user_id)
    except Exception:
        logging.exception("resolve_user_display failed for user_id=%s", user_id)

    return uname, tg_id
