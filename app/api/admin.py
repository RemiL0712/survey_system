import hashlib
import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter(tags=["admin"])

ADMIN_PASSWORD = os.getenv("ADMIN_WEB_PASSWORD", "admin123")
ADMIN_SESSION_SECRET = os.getenv("ADMIN_WEB_SECRET", "change-me-admin-secret")
ADMIN_COOKIE_NAME = "survey_admin_session"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "admin_web"


def build_admin_cookie_value() -> str:
    return hmac.new(
        ADMIN_SESSION_SECRET.encode("utf-8"),
        ADMIN_PASSWORD.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_admin_authenticated(request: Request) -> bool:
    cookie_value = request.cookies.get(ADMIN_COOKIE_NAME)
    return bool(cookie_value) and hmac.compare_digest(cookie_value, build_admin_cookie_value())


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not is_admin_authenticated(request):
        return HTMLResponse(load_template("login.html"))
    return HTMLResponse(load_template("panel.html"))


@router.post("/admin/login")
async def admin_login(payload: dict = Body(...)):
    password = str(payload.get("password", ""))
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=build_admin_cookie_value(),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/admin/logout")
async def admin_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@router.get("/admin/logout")
async def admin_logout_redirect():
    response = RedirectResponse(url="/admin", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response
