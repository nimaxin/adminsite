"""A person's own password: the form, and changing it through the auth provider."""

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import read_form
from adminsite._http.urls import Urls
from adminsite.exceptions import RefusedError
from adminsite.i18n import gettext as _
from adminsite.messages import add_message

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "BOXES",
    "change_password",
    "password_form",
]

# The boxes of the form, by the names it sends them under.
BOXES = ("current", "new", "repeat")


async def password_form(admin: "Admin", request: Request) -> Response:
    """The form for changing one's own password."""
    return await admin.render(
        "password.html", request, {"errors": {}, "on_password": True}
    )


async def change_password(admin: "Admin", request: Request) -> Response:
    """Change the password, or show the form again with what is wrong."""
    assert admin.auth is not None
    form = await read_form(request)
    current, new, repeat = (str(form.get(name, "")) for name in BOXES)
    errors: dict[str, str] = {}
    if new != repeat:
        errors["repeat"] = _("The two new passwords differ.")
    else:
        try:
            await admin.auth.change_password(request, current, new)
        except RefusedError as refused:
            errors[refused.field if refused.field in BOXES else "new"] = str(refused)
    if errors:
        return await admin.render(
            "password.html",
            request,
            {"errors": errors, "on_password": True},
            status_code=422,
        )
    add_message(request, _("Password changed."))
    return RedirectResponse(Urls(request).index(), status_code=303)
