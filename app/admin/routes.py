"""Admin console pages: two-step sign in, overview, sign out and the Day/Night switch
(UI-UX.md §8, SECURITY.md §4.1)."""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.admin.auth import (
    Clock,
    SignInFailed,
    TooManyAttempts,
    actor,
    check_code,
    check_ip_allowance,
    check_password,
    email_hint,
)
from app.admin.roles import Role, screens_for
from app.admin.security import (
    THEME_COOKIE,
    THEMES,
    clear_cookie,
    csrf_cookie_name,
    new_csrf_token,
    pending_cookie_name,
    session_cookie_name,
    set_cookie,
    tokens_match,
)
from app.admin.sessions import (
    PENDING_SECONDS,
    AdminSession,
    PendingSignIn,
    create_session,
    end_pending,
    end_session,
    load_pending,
    load_session,
    start_pending,
)
from app.api.deps import SessionDep
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.models import AdminUser, AuditEvent
from app.settings import Settings

log = get_logger()
router = APIRouter(prefix="/admin", include_in_schema=False)

STATIC_DIR = Path(__file__).parent / "static"
# Not every OS's MIME table knows web fonts; with `nosniff` the type must be right.
mimetypes.add_type("font/woff2", ".woff2")
OVERVIEW_URL = "/admin"
SIGN_IN_URL = "/admin/login"
CODE_URL = "/admin/login/code"
# The Day/Night switch only ever sends people back to one of these pages.
THEME_RETURN_PATHS = frozenset({OVERVIEW_URL, SIGN_IN_URL, CODE_URL})

# One message for every step-1 failure, so the page never reveals whether an account exists.
SIGN_IN_FAILED = "We couldn't sign you in with those details. Check your email and password."
CODE_FAILED = "That code didn't match. Codes change every 30 seconds, so try the newest one."
FORM_EXPIRED = "This page was open for a while, so we refreshed it. Please try again."
TOO_MANY = "Too many attempts from this network. Please wait {minutes} minutes, then try again."

FormText = Annotated[str, Form()]


def _page_context(request: Request) -> dict[str, Any]:
    """Values every admin page needs: the chosen theme and the current year."""
    theme = request.cookies.get(THEME_COOKIE)
    return {
        "theme": theme if theme in THEMES else "system",
        "here": request.url.path,
        "year": utcnow().year,
    }


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_page_context]
)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _clock() -> Clock:
    return Clock(utc=utcnow(), epoch=time.time())


async def _signed_in(request: Request, db: SessionDep) -> tuple[AdminUser, AdminSession] | None:
    settings = _settings(request)
    token = request.cookies.get(session_cookie_name(settings))
    session = await load_session(request.app.state.redis, token, settings, time.time())
    if session is None:
        return None
    user = await db.get(AdminUser, session.user_id)
    # Removing someone from the allow-list ends their open sessions on their next request.
    if user is None or user.status != "active" or not settings.admin_email_allowed(user.email):
        await end_session(request.app.state.redis, token)
        return None
    return user, session


async def _pending(request: Request) -> PendingSignIn | None:
    token = request.cookies.get(pending_cookie_name(_settings(request)))
    return await load_pending(request.app.state.redis, token)


def _password_page(
    request: Request,
    *,
    status_code: int = 200,
    error: str | None = None,
    notice: str | None = None,
    email: str = "",
) -> Response:
    settings = _settings(request)
    csrf_token = new_csrf_token()
    context: dict[str, Any] = {
        "csrf_token": csrf_token,
        "error": error,
        "notice": notice,
        "email": email[:254],
        "lockout_minutes": settings.admin_lockout_minutes,
    }
    response = templates.TemplateResponse(request, "sign_in.html", context, status_code=status_code)
    set_cookie(response, csrf_cookie_name(settings), csrf_token, settings)
    return response


def _code_page(
    request: Request, pending: PendingSignIn, *, status_code: int = 200, error: str | None = None
) -> Response:
    context: dict[str, Any] = {
        "csrf_token": pending.csrf_token,
        "email_hint": pending.email_hint,
        "error": error,
        "lockout_minutes": _settings(request).admin_lockout_minutes,
        "minutes_to_finish": PENDING_SECONDS // 60,
    }
    return templates.TemplateResponse(
        request, "sign_in_code.html", context, status_code=status_code
    )


def _back_to_start(request: Request, *, notice: str | None, error: str | None = None) -> Response:
    """End the pending step and show the password page again."""
    response = _password_page(
        request, status_code=401 if error else 200, error=error, notice=notice
    )
    clear_cookie(response, pending_cookie_name(_settings(request)), _settings(request))
    return response


# ---- Step 1: email and password ----


@router.get("/login", response_class=HTMLResponse)
async def sign_in_form(
    request: Request, db: SessionDep, signed_out: bool = False, expired: bool = False
) -> Response:
    if await _signed_in(request, db) is not None:
        return RedirectResponse(OVERVIEW_URL, status_code=303)
    settings = _settings(request)
    pending_token = request.cookies.get(pending_cookie_name(settings))
    if pending_token:  # "Start over" from the code step lands here
        await end_pending(request.app.state.redis, pending_token)
    notice = None
    if signed_out:
        notice = "You're signed out. See you next time."
    elif expired:
        notice = "Your sign-in timed out before the code step. Please start again."
    response = _password_page(request, notice=notice)
    if pending_token:
        clear_cookie(response, pending_cookie_name(settings), settings)
    return response


@router.post("/login")
async def sign_in(
    request: Request,
    db: SessionDep,
    email: FormText = "",
    password: FormText = "",
    csrf_token: FormText = "",
) -> Response:
    settings = _settings(request)
    redis = request.app.state.redis
    if not tokens_match(request.cookies.get(csrf_cookie_name(settings)), csrf_token):
        log.warning("admin_csrf_rejected", path=request.url.path)
        return _password_page(request, status_code=400, error=FORM_EXPIRED, email=email)
    try:
        await check_ip_allowance(redis, _client_ip(request), settings)
        user = await check_password(db, settings, email=email, password=password, clock=_clock())
    except TooManyAttempts:
        return _password_page(
            request,
            status_code=429,
            error=TOO_MANY.format(minutes=settings.admin_lockout_minutes),
            email=email,
        )
    except SignInFailed as exc:
        log.warning("admin_sign_in_failed", step="password", reason=exc.reason)
        return _password_page(request, status_code=401, error=SIGN_IN_FAILED, email=email)

    token, _ = await start_pending(redis, user.id, email_hint(user.email))
    response = RedirectResponse(CODE_URL, status_code=303)
    set_cookie(response, pending_cookie_name(settings), token, settings, max_age=PENDING_SECONDS)
    clear_cookie(response, csrf_cookie_name(settings), settings)
    return response


# ---- Step 2: the two-factor code ----


@router.get("/login/code", response_class=HTMLResponse)
async def code_form(request: Request) -> Response:
    pending = await _pending(request)
    if pending is None:
        return RedirectResponse(f"{SIGN_IN_URL}?expired=1", status_code=303)
    return _code_page(request, pending)


@router.post("/login/code")
async def sign_in_code(
    request: Request, db: SessionDep, code: FormText = "", csrf_token: FormText = ""
) -> Response:
    settings = _settings(request)
    redis = request.app.state.redis
    pending_token = request.cookies.get(pending_cookie_name(settings))
    pending = await load_pending(redis, pending_token)
    if pending is None:
        return RedirectResponse(f"{SIGN_IN_URL}?expired=1", status_code=303)
    if not tokens_match(pending.csrf_token, csrf_token):
        log.warning("admin_csrf_rejected", path=request.url.path)
        return _code_page(request, pending, status_code=400, error=FORM_EXPIRED)
    try:
        await check_ip_allowance(redis, _client_ip(request), settings)
        user = await check_code(
            db,
            request.app.state.vault,
            settings,
            user_id=pending.user_id,
            code=code,
            clock=_clock(),
        )
    except TooManyAttempts:
        await end_pending(redis, pending_token)
        return _back_to_start(
            request,
            notice=None,
            error=TOO_MANY.format(minutes=settings.admin_lockout_minutes),
        )
    except SignInFailed as exc:
        log.warning("admin_sign_in_failed", step="code", reason=exc.reason)
        if exc.reason == "code":
            return _code_page(request, pending, status_code=401, error=CODE_FAILED)
        await end_pending(redis, pending_token)  # locked, disabled or not set up
        return _back_to_start(request, notice=None, error=SIGN_IN_FAILED)

    await end_pending(redis, pending_token)
    token, _ = await create_session(redis, user.id, settings, time.time())
    log.info("admin_signed_in", admin_id=user.id)
    response = RedirectResponse(OVERVIEW_URL, status_code=303)
    set_cookie(response, session_cookie_name(settings), token, settings)
    clear_cookie(response, pending_cookie_name(settings), settings)
    return response


# ---- Signed in ----


@router.get("", response_class=HTMLResponse)
async def overview(request: Request, db: SessionDep) -> Response:
    current = await _signed_in(request, db)
    if current is None:
        return RedirectResponse(SIGN_IN_URL, status_code=303)
    user, session = current
    settings = _settings(request)
    context: dict[str, Any] = {
        "user": user,
        "role_label": Role(user.role).value.capitalize(),
        "screens": screens_for(user.role),
        "csrf_token": session.csrf_token,
        "idle_minutes": settings.admin_session_idle_minutes,
    }
    return templates.TemplateResponse(request, "overview.html", context)


@router.post("/logout")
async def sign_out(request: Request, db: SessionDep, csrf_token: FormText = "") -> Response:
    settings = _settings(request)
    current = await _signed_in(request, db)
    if current is not None:
        user, session = current
        if not tokens_match(session.csrf_token, csrf_token):
            log.warning("admin_csrf_rejected", path=request.url.path)
            return RedirectResponse(OVERVIEW_URL, status_code=303)
        await end_session(
            request.app.state.redis, request.cookies.get(session_cookie_name(settings))
        )
        db.add(
            AuditEvent(actor=actor(user), action="admin.sign_out", target=actor(user), details={})
        )
        await db.commit()
        log.info("admin_signed_out", admin_id=user.id)
    response = RedirectResponse(f"{SIGN_IN_URL}?signed_out=1", status_code=303)
    clear_cookie(response, session_cookie_name(settings), settings)
    return response


# ---- Day / Night ----


@router.get("/theme/{mode}")
async def set_theme(
    request: Request, mode: Literal["light", "dark", "system"], back: str = SIGN_IN_URL
) -> Response:
    """Remember the Day/Night choice in a cookie; "system" follows the device again."""
    settings = _settings(request)
    response = RedirectResponse(
        back if back in THEME_RETURN_PATHS else SIGN_IN_URL, status_code=303
    )
    if mode == "system":
        clear_cookie(response, THEME_COOKIE, settings)
    else:
        set_cookie(response, THEME_COOKIE, mode, settings, max_age=365 * 24 * 3600)
    return response
