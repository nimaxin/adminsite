"""The sign in page, signing in and out, and writing both down."""

from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import read_form
from adminsite._http.urls import Urls
from adminsite.audit import AuditEntry, AuditEvent, actor_of
from adminsite.audit._actor import NAME_LIMIT
from adminsite.audit.store import record_or_warn
from adminsite.exceptions import SignInRefusedError
from adminsite.i18n import gettext as _

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "login",
    "login_form",
    "logout",
    "note_sign_in",
]


async def login_form(admin: "Admin", request: Request) -> Response:
    """The sign in page."""
    values = await admin.auth.sign_in_values(request) if admin.auth else {}
    return await admin.render("login.html", request, {"error": "", "values": values})


async def login(admin: "Admin", request: Request) -> Response:
    """Check the details and let the user in."""
    if admin.auth is None:
        raise HTTPException(status_code=404, detail=_("Signing in is not set up."))

    submitted = await read_form(request)
    username = str(submitted.get("username", ""))
    try:
        user = await admin.auth.sign_in(
            request, username, str(submitted.get("password", ""))
        )
    except SignInRefusedError as refused:
        await note_sign_in(
            admin,
            request,
            AuditEvent.SIGN_IN_FAILED,
            user=refused.user,
            tried=username,
            reason=refused.reason,
        )
        user = None
    else:
        if user is None:
            await note_sign_in(
                admin,
                request,
                AuditEvent.SIGN_IN_FAILED,
                tried=username,
                reason=_("The details did not match."),
            )
    if user is None:
        message = await admin.auth.sign_in_failed(request, username)
        # The username typed stays, so a mistyped password costs only that.
        values = {**await admin.auth.sign_in_values(request), "username": username}
        return await admin.render(
            "login.html",
            request,
            {"error": message, "values": values},
            status_code=401,
        )
    await note_sign_in(admin, request, AuditEvent.SIGNED_IN, user=user)
    return RedirectResponse(Urls(request).index(), status_code=303)


async def logout(admin: "Admin", request: Request) -> Response:
    """Sign the user out."""
    await read_form(request)
    if admin.auth is not None:
        user = await admin.auth.current_user(request)
        await admin.auth.sign_out(request)
        if user is not None:
            await note_sign_in(admin, request, AuditEvent.SIGNED_OUT, user=user)
    return RedirectResponse(Urls(request).login(), status_code=303)


async def note_sign_in(
    admin: "Admin",
    request: Request,
    event: AuditEvent,
    *,
    user: Any = None,
    tried: str = "",
    reason: str | None = None,
) -> None:
    """Write down a sign in, a failed one or a sign out, if auditing is on.

    It happens to no record, so the entry has no view. A failed attempt
    without an account behind it is filed under the name that was tried.
    """
    if admin.audit is None or admin.auth is None:
        return
    actor = actor_of(request)
    if user is not None:
        actor["user"] = str(user)[:NAME_LIMIT]
        actor["user_key"] = admin.auth.identity(user)
    else:
        actor["user"] = tried[:NAME_LIMIT] or None
    await record_or_warn(
        admin.audit,
        [AuditEntry(view="", record_key="", event=event, error=reason, **actor)],
    )
