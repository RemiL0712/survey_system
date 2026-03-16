import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.api.security import (
    ADMIN_COOKIE_NAME,
    build_admin_cookie_value,
    get_admin_password,
    is_admin_authenticated,
)

router = APIRouter(tags=["admin"])

ADMIN_COOKIE_SECURE = os.getenv("ADMIN_WEB_COOKIE_SECURE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "admin_web"


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not is_admin_authenticated(request):
        return HTMLResponse(load_template("login.html"))
    return HTMLResponse(load_template("panel.html"))


@router.get("/admin/favicon.svg")
async def admin_favicon():
    return FileResponse(TEMPLATES_DIR / "favicon.svg", media_type="image/svg+xml")


@router.post("/admin/login")
async def admin_login(payload: dict = Body(...)):
    password = str(payload.get("password", ""))
    admin_password = get_admin_password()
    if not hmac.compare_digest(password, admin_password):
        raise HTTPException(status_code=401, detail="Invalid password")

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=build_admin_cookie_value(),
        httponly=True,
        samesite="lax",
        secure=ADMIN_COOKIE_SECURE,
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
