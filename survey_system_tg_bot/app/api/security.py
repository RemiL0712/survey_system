import hashlib
import hmac
import os

from fastapi import Header, HTTPException, Request, status

ADMIN_COOKIE_NAME = "survey_admin_session"


def _normalized_secret(name: str) -> str:
    return os.getenv(name, "").strip()


def get_admin_password() -> str:
    value = _normalized_secret("ADMIN_WEB_PASSWORD")
    if not value:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_WEB_PASSWORD is not configured",
        )
    return value


def get_admin_session_secret() -> str:
    value = _normalized_secret("ADMIN_WEB_SECRET")
    if not value:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_WEB_SECRET is not configured",
        )
    return value


def get_internal_api_token() -> str:
    return _normalized_secret("ADMIN_API_TOKEN") or _normalized_secret("SECRET_KEY")


def build_admin_cookie_value() -> str:
    return hmac.new(
        get_admin_session_secret().encode("utf-8"),
        get_admin_password().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_admin_authenticated(request: Request) -> bool:
    cookie_value = request.cookies.get(ADMIN_COOKIE_NAME)
    if not cookie_value:
        return False
    try:
        return hmac.compare_digest(cookie_value, build_admin_cookie_value())
    except HTTPException:
        return False


def has_internal_api_access(x_admin_api_token: str | None) -> bool:
    token = get_internal_api_token()
    return bool(token) and bool(x_admin_api_token) and hmac.compare_digest(x_admin_api_token, token)


async def require_api_access(
    request: Request,
    x_admin_api_token: str | None = Header(default=None, alias="X-Admin-Api-Token"),
) -> None:
    if is_admin_authenticated(request) or has_internal_api_access(x_admin_api_token):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )
