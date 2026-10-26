"""Admin console sign-in (SECURITY.md §4.1): the password step, the two-factor code step,
sessions, lockout, CSRF, roles, the Day/Night switch and log hygiene."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app.admin.auth import TOTP_SECRET_PURPOSE, email_hint
from app.core import totp
from app.core.ids import utcnow
from app.core.passwords import hash_password
from app.models import AdminUser, AuditEvent
from tests.conftest import Hub

PASSWORD = "correct horse battery staple"
EMAIL = "owner@lazula.example"
SIGN_IN_FAILED = "We couldn&#39;t sign you in with those details"
CODE_FAILED = "That code didn&#39;t match"

pytestmark = pytest.mark.security


@dataclass
class Admin:
    id: int
    email: str
    secret: str


async def make_admin(
    hub: Hub,
    *,
    email: str = EMAIL,
    role: str = "owner",
    name: str = "Ada Owner",
    two_factor: bool = True,
) -> Admin:
    secret = pyotp.random_base32()
    async with hub.asgi.state.sessionmaker() as session:
        user = AdminUser(email=email, name=name, role=role, password_hash=hash_password(PASSWORD))
        session.add(user)
        await session.flush()
        if two_factor:
            user.totp_secret_enc = hub.asgi.state.vault.encrypt(
                secret, TOTP_SECRET_PURPOSE.format(user_id=user.id)
            )
        await session.commit()
        return Admin(user.id, email, secret)


def code_for(admin: Admin, steps_from_now: int = 0) -> str:
    return pyotp.TOTP(admin.secret).at(int(time.time()) + 30 * steps_from_now)


def wrong_code(admin: Admin) -> str:
    valid = {code_for(admin, s) for s in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def form_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "page has no CSRF token"
    return match.group(1)


async def password_step(
    hub: Hub, *, email: str = EMAIL, password: str = PASSWORD, csrf: str | None = None
) -> httpx.Response:
    if csrf is None:
        csrf = form_token((await hub.client.get("/admin/login")).text)
    return await hub.client.post(
        "/admin/login", data={"email": email, "password": password, "csrf_token": csrf}
    )


async def code_step(hub: Hub, code: str, *, csrf: str | None = None) -> httpx.Response:
    if csrf is None:
        csrf = form_token((await hub.client.get("/admin/login/code")).text)
    return await hub.client.post("/admin/login/code", data={"code": code, "csrf_token": csrf})


async def sign_in(
    hub: Hub, *, code: str, email: str = EMAIL, password: str = PASSWORD
) -> httpx.Response:
    """Both steps. Returns the password step's response if it fails, else the code step's."""
    first = await password_step(hub, email=email, password=password)
    if first.status_code != 303:
        return first
    return await code_step(hub, code)


async def user_row(hub: Hub, admin_id: int) -> AdminUser:
    async with hub.asgi.state.sessionmaker() as session:
        user = await session.get(AdminUser, admin_id)
        assert user is not None
        return user


async def audit_actions(hub: Hub) -> list[str]:
    async with hub.asgi.state.sessionmaker() as session:
        return list((await session.scalars(select(AuditEvent.action))).all())


# ---- Page and headers ----


async def test_sign_in_page_has_strict_security_headers(hub: Hub) -> None:
    page = await hub.client.get("/admin/login")
    assert page.status_code == 200
    assert "Welcome back" in page.text and "Protected with two-step sign-in" in page.text
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "script-src" not in csp  # no scripts allowed at all
    assert "<script" not in page.text and " style=" not in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"
    cookie = page.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


async def test_api_routes_keep_their_own_headers(hub: Hub) -> None:
    response = await hub.client.get("/health")
    assert "content-security-policy" not in response.headers


async def test_static_files_are_served_and_cacheable(hub: Hub) -> None:
    css = await hub.client.get("/admin/static/admin.css")
    assert css.status_code == 200
    assert "content-security-policy" in css.headers
    assert css.headers.get("cache-control") != "no-store"


async def test_fonts_are_self_hosted_with_the_right_type(hub: Hub) -> None:
    page = (await hub.client.get("/admin/login")).text
    assert "fonts.googleapis.com" not in page and "fonts.gstatic.com" not in page
    for name in ("plus-jakarta-sans", "inter", "jetbrains-mono"):
        font = await hub.client.get(f"/admin/static/fonts/{name}-latin-variable.woff2")
        assert font.status_code == 200
        assert font.headers["content-type"] == "font/woff2"
        assert font.content[:4] == b"wOF2"


async def test_overview_requires_sign_in(hub: Hub) -> None:
    response = await hub.client.get("/admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


# ---- The two steps ----


async def test_owner_signs_in_with_password_then_code(hub: Hub) -> None:
    admin = await make_admin(hub)
    first = await password_step(hub)
    assert first.status_code == 303
    assert first.headers["location"] == "/admin/login/code"
    pending_cookie = next(
        c for c in first.headers.get_list("set-cookie") if c.startswith("lazula_pending=")
    ).lower()
    assert "httponly" in pending_cookie and "samesite=strict" in pending_cookie
    assert "max-age=300" in pending_cookie

    assert (await hub.client.get("/admin")).status_code == 303  # a password alone is not enough
    code_page = await hub.client.get("/admin/login/code")
    assert "Check your authenticator" in code_page.text
    assert "o•••@lazula.example" in code_page.text
    assert EMAIL not in code_page.text

    second = await code_step(hub, code_for(admin))
    assert second.status_code == 303
    assert second.headers["location"] == "/admin"
    session_cookie = next(
        c for c in second.headers.get_list("set-cookie") if c.startswith("lazula_admin=")
    ).lower()
    assert "httponly" in session_cookie and "samesite=strict" in session_cookie

    overview = await hub.client.get("/admin")
    assert overview.status_code == 200
    assert "Welcome, Ada" in overview.text
    assert "Admin users" in overview.text  # owner-only screen
    assert "admin.sign_in" in await audit_actions(hub)
    assert (await user_row(hub, admin.id)).last_sign_in_at is not None
    assert not await hub.asgi.state.redis.keys("admin_pending:*")


async def test_signed_in_admin_skips_the_sign_in_page(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    response = await hub.client.get("/admin/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


async def test_password_step_failures_look_the_same(hub: Hub) -> None:
    await make_admin(hub)
    await make_admin(hub, email="nofactor@lazula.example", two_factor=False)
    wrong_password = await password_step(hub, password="not the password at all")
    unknown_email = await password_step(hub, email="nobody@lazula.example")
    no_two_factor = await password_step(hub, email="nofactor@lazula.example")
    for response in (wrong_password, unknown_email, no_two_factor):
        assert response.status_code == 401
        assert SIGN_IN_FAILED in response.text
        assert PASSWORD not in response.text
    assert "nobody@lazula.example" in unknown_email.text  # email is kept so people can retry


async def test_wrong_code_can_be_retried(hub: Hub) -> None:
    admin = await make_admin(hub)
    await password_step(hub)
    wrong = await code_step(hub, wrong_code(admin))
    assert wrong.status_code == 401
    assert CODE_FAILED in wrong.text
    assert (await code_step(hub, code_for(admin))).status_code == 303
    assert (await hub.client.get("/admin")).status_code == 200


async def test_a_code_cannot_be_used_twice(hub: Hub) -> None:
    admin = await make_admin(hub)
    code = code_for(admin)
    assert (await sign_in(hub, code=code)).status_code == 303
    overview = await hub.client.get("/admin")
    await hub.client.post("/admin/logout", data={"csrf_token": form_token(overview.text)})
    replay = await sign_in(hub, code=code)
    assert replay.status_code == 401
    assert CODE_FAILED in replay.text


def test_codes_allow_one_step_of_clock_drift_only() -> None:
    secret = pyotp.random_base32()
    now = time.time()
    step = int(now // 30)
    generator = pyotp.TOTP(secret)
    assert totp.matching_step(secret, generator.at((step - 1) * 30), now, None) == step - 1
    assert totp.matching_step(secret, generator.at((step + 1) * 30), now, None) == step + 1
    two_steps_old = generator.at((step - 2) * 30)
    if two_steps_old not in {generator.at(s * 30) for s in (step - 1, step, step + 1)}:
        assert totp.matching_step(secret, two_steps_old, now, None) is None
    assert totp.matching_step(secret, generator.at(step * 30), now, step) is None  # already used
    assert totp.matching_step(secret, "12345a", now, None) is None
    assert totp.matching_step(secret, "", now, None) is None


def test_email_hint_hides_most_of_the_address() -> None:
    assert email_hint("ada@lazula.example") == "a•••@lazula.example"
    assert email_hint("not-an-email") == "your account"


async def test_code_page_without_a_password_step_goes_back(hub: Hub) -> None:
    response = await hub.client.get("/admin/login/code")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?expired=1"
    page = await hub.client.get(response.headers["location"])
    assert "Your sign-in timed out" in page.text


async def test_code_step_times_out(hub: Hub) -> None:
    admin = await make_admin(hub)
    await password_step(hub)
    csrf = form_token((await hub.client.get("/admin/login/code")).text)
    for key in await hub.asgi.state.redis.keys("admin_pending:*"):
        await hub.asgi.state.redis.delete(key)  # what the 5-minute expiry does
    response = await code_step(hub, code_for(admin), csrf=csrf)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?expired=1"
    assert (await hub.client.get("/admin")).status_code == 303


async def test_start_over_ends_the_pending_step(hub: Hub) -> None:
    await make_admin(hub)
    await password_step(hub)
    assert await hub.asgi.state.redis.keys("admin_pending:*")
    page = await hub.client.get("/admin/login")
    assert page.status_code == 200
    assert not await hub.asgi.state.redis.keys("admin_pending:*")


async def test_pending_store_never_holds_the_cookie_value(hub: Hub) -> None:
    await make_admin(hub)
    await password_step(hub)
    token = hub.client.cookies["lazula_pending"]
    [key] = await hub.asgi.state.redis.keys("admin_pending:*")
    assert token not in key
    assert token not in await hub.asgi.state.redis.get(key)


# ---- Lockout and limits ----


async def test_account_locks_after_repeated_wrong_passwords(hub: Hub) -> None:
    admin = await make_admin(hub)
    for _ in range(5):
        response = await password_step(hub, password="wrong password here")
        assert response.status_code == 401
    locked = await password_step(hub)  # right password, but locked
    assert locked.status_code == 401
    assert SIGN_IN_FAILED in locked.text
    user = await user_row(hub, admin.id)
    assert user.locked_until is not None and user.locked_until > utcnow()

    async with hub.asgi.state.sessionmaker() as session:
        row = await session.get(AdminUser, admin.id)
        assert row is not None
        row.locked_until = utcnow() - timedelta(seconds=1)
        await session.commit()
    assert (await sign_in(hub, code=code_for(admin))).status_code == 303
    assert (await user_row(hub, admin.id)).failed_sign_ins == 0


async def test_wrong_codes_count_towards_the_lockout(hub: Hub) -> None:
    admin = await make_admin(hub)
    await password_step(hub)
    for _ in range(5):
        assert (await code_step(hub, wrong_code(admin))).status_code == 401
    # Now locked: the next try sends the person back to the start with the generic message.
    response = await code_step(hub, code_for(admin))
    assert response.status_code == 401
    assert SIGN_IN_FAILED in response.text and "Welcome back" in response.text
    assert not await hub.asgi.state.redis.keys("admin_pending:*")
    assert (await hub.client.get("/admin")).status_code == 303
    assert "admin.sign_in_failed" in await audit_actions(hub)


async def test_too_many_attempts_from_one_address(hub: Hub) -> None:
    hub.asgi.state.settings.admin_sign_in_attempts_per_ip = 5
    for _ in range(5):
        response = await password_step(hub, email="nobody@lazula.example")
        assert response.status_code == 401
    blocked = await password_step(hub, email="nobody@lazula.example")
    assert blocked.status_code == 429
    assert "Too many attempts from this network" in blocked.text


async def test_password_step_needs_a_matching_csrf_token(hub: Hub) -> None:
    await make_admin(hub)
    await hub.client.get("/admin/login")  # sets the CSRF cookie
    missing = await hub.client.post("/admin/login", data={"email": EMAIL, "password": PASSWORD})
    forged = await password_step(hub, csrf="forged-token")
    for response in (missing, forged):
        assert response.status_code == 400
        assert "This page was open for a while" in response.text
    assert not await hub.asgi.state.redis.keys("admin_pending:*")


async def test_code_step_needs_a_matching_csrf_token(hub: Hub) -> None:
    admin = await make_admin(hub)
    await password_step(hub)
    forged = await code_step(hub, code_for(admin), csrf="forged-token")
    assert forged.status_code == 400
    assert (await hub.client.get("/admin")).status_code == 303
    assert (await code_step(hub, code_for(admin))).status_code == 303  # still pending


# ---- Sessions ----


async def test_sign_out_ends_the_session(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    overview = await hub.client.get("/admin")
    response = await hub.client.post(
        "/admin/logout", data={"csrf_token": form_token(overview.text)}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?signed_out=1"
    assert (await hub.client.get("/admin")).status_code == 303
    assert "admin.sign_out" in await audit_actions(hub)
    assert not await hub.asgi.state.redis.keys("admin_session:*")


async def test_sign_out_without_csrf_token_is_ignored(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    response = await hub.client.post("/admin/logout", data={})
    assert response.headers["location"] == "/admin"
    assert (await hub.client.get("/admin")).status_code == 200


async def test_idle_timer_resets_on_activity(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    redis = hub.asgi.state.redis
    [key] = await redis.keys("admin_session:*")
    await redis.expire(key, 10)
    await hub.client.get("/admin")
    assert await redis.ttl(key) > 30 * 60 - 5


async def test_session_ends_after_the_absolute_limit(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    redis = hub.asgi.state.redis
    [key] = await redis.keys("admin_session:*")
    data = json.loads(await redis.get(key))
    data["created_at"] = time.time() - 13 * 3600
    await redis.set(key, json.dumps(data), ex=600)
    assert (await hub.client.get("/admin")).status_code == 303
    assert not await redis.keys("admin_session:*")


async def test_session_store_never_holds_the_cookie_value(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    token = hub.client.cookies["lazula_admin"]
    [key] = await hub.asgi.state.redis.keys("admin_session:*")
    assert token not in key
    assert token not in await hub.asgi.state.redis.get(key)


async def test_disabled_admin_is_signed_out_and_cannot_sign_in(hub: Hub) -> None:
    admin = await make_admin(hub)
    await sign_in(hub, code=code_for(admin))
    async with hub.asgi.state.sessionmaker() as session:
        row = await session.get(AdminUser, admin.id)
        assert row is not None
        row.status = "disabled"
        await session.commit()
    assert (await hub.client.get("/admin")).status_code == 303
    assert (await password_step(hub)).status_code == 401


async def test_viewer_sees_only_viewer_screens(hub: Hub) -> None:
    admin = await make_admin(hub, email="viewer@lazula.example", role="viewer", name="Val Viewer")
    await sign_in(hub, code=code_for(admin), email=admin.email)
    page = (await hub.client.get("/admin")).text
    assert "Viewer" in page and "Usage and cost" in page
    for hidden in ("Admin users", "Request log", "Apps and secrets", "Audit log"):
        assert hidden not in page


# ---- Day / Night ----


async def test_day_night_switch_is_remembered(hub: Hub) -> None:
    assert 'data-theme="system"' in (await hub.client.get("/admin/login")).text
    response = await hub.client.get("/admin/theme/dark", params={"back": "/admin/login"})
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert 'data-theme="dark"' in (await hub.client.get("/admin/login")).text
    await hub.client.get("/admin/theme/system")
    assert 'data-theme="system"' in (await hub.client.get("/admin/login")).text


async def test_day_night_switch_never_redirects_off_site(hub: Hub) -> None:
    for back in ("https://evil.example/", "//evil.example", "/admin/../docs"):
        response = await hub.client.get("/admin/theme/light", params={"back": back})
        assert response.headers["location"] == "/admin/login"
    assert (await hub.client.get("/admin/theme/purple")).status_code == 422


# ---- Storage and logs ----


async def test_credentials_are_stored_safely(hub: Hub) -> None:
    admin = await make_admin(hub)
    user = await user_row(hub, admin.id)
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert user.totp_secret_enc is not None and admin.secret not in user.totp_secret_enc


async def test_passwords_codes_and_emails_never_reach_the_logs(
    hub: Hub, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = await make_admin(hub)
    bad_code = wrong_code(admin)
    good_code = code_for(admin)
    await password_step(hub, password="a wrong password value")
    await password_step(hub)
    await code_step(hub, bad_code)
    await code_step(hub, good_code)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "admin_sign_in_failed" in output and "admin_signed_in" in output  # logging is on
    for secret in (PASSWORD, "a wrong password value", bad_code, good_code, EMAIL, admin.secret):
        assert secret not in output
