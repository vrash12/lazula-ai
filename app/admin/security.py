"""Admin console cookies, CSRF checks and security headers (SECURITY.md §4.1, §4.5).

Cookies are HttpOnly and SameSite=Strict, and Secure with the `__Host-` prefix
outside local development and tests. Before sign-in, the form is protected by a
double-submit token (cookie + hidden field); after sign-in, by the token stored
in the server-side session.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.settings import Settings

ADMIN_PATH = "/admin"

# Scripts are not allowed at all: the admin pages work without JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def session_cookie_name(settings: Settings) -> str:
    return "__Host-lazula_admin" if settings.admin_cookies_secure else "lazula_admin"


def csrf_cookie_name(settings: Settings) -> str:
    return "__Host-lazula_csrf" if settings.admin_cookies_secure else "lazula_csrf"


def pending_cookie_name(settings: Settings) -> str:
    return "__Host-lazula_pending" if settings.admin_cookies_secure else "lazula_pending"


# Display preference only (day or night); not a secret.
THEME_COOKIE = "lazula_theme"
THEMES = ("light", "dark")


def set_cookie(
    response: Response, name: str, value: str, settings: Settings, max_age: int | None = None
) -> None:
    # `__Host-` cookies must use path "/"; the session token is useless outside /admin anyway.
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=settings.admin_cookies_secure,
        samesite="strict",
    )


def clear_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(
        name, path="/", httponly=True, secure=settings.admin_cookies_secure, samesite="strict"
    )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str | None, submitted: str | None) -> bool:
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected.encode(), submitted.encode())


class AdminSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Strict headers on every /admin response. The JSON API keeps its own headers."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path == ADMIN_PATH or path.startswith(ADMIN_PATH + "/"):
            headers = response.headers
            headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            headers["X-Frame-Options"] = "DENY"
            headers["X-Content-Type-Options"] = "nosniff"
            headers["Referrer-Policy"] = "no-referrer"
            headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            headers["Cross-Origin-Opener-Policy"] = "same-origin"
            if not path.startswith(ADMIN_PATH + "/static/"):
                headers["Cache-Control"] = "no-store"
            if self.settings.admin_cookies_secure:
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
