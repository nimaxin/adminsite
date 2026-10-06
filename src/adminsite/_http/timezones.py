"""Switching the time zone the admin shows times in."""

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import read_form
from adminsite._http.urls import Urls
from adminsite.timezones import (
    BROWSER_TIMEZONE_COOKIE,
    TIMEZONE_COOKIE,
    find_timezone,
    offset_text,
)

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "choose_timezone",
    "timezone_label",
    "timezone_menu",
]


async def choose_timezone(admin: "Admin", request: Request) -> Response:
    """Show times in another time zone, and go back where we were."""
    form = await read_form(request)
    wanted = str(form.get("timezone", ""))
    response = RedirectResponse(
        Urls(request).back(str(form.get("next", ""))), status_code=303
    )
    path = Urls(request).index()
    if wanted in admin.timezones:
        response.set_cookie(
            TIMEZONE_COOKIE,
            wanted,
            max_age=365 * 24 * 3600,
            path=path,
            samesite="lax",
        )
    elif not wanted:
        # Times follow the browser's own time zone again.
        response.delete_cookie(TIMEZONE_COOKIE, path=path, samesite="lax")
    return response


def timezone_menu(admin: "Admin", request: Request) -> list[tuple[str, str, bool]]:
    """The menu's time zones: each one's value, its name and whether it is shown.

    Empty where the admin offers no other zone. The browser's own zone comes
    first where the admin does not list it, so choosing it follows the
    browser again.
    """
    if len(admin.timezones) < 2:
        return []
    shown = request.scope.get("adminsite_timezone", admin.timezone)
    entries = [(name, timezone_label(name), name == shown) for name in admin.timezones]
    browser = request.cookies.get(BROWSER_TIMEZONE_COOKIE, "")
    if find_timezone(browser) is not None and browser not in admin.timezones:
        entries.insert(0, ("", timezone_label(browser), browser == shown))
    return entries


def timezone_label(name: str) -> str:
    """A time zone as the menu names it: its city and how far it is from UTC."""
    found = find_timezone(name)
    if found is None or name == "UTC":
        return name
    city = name.rpartition("/")[2].replace("_", " ")
    return f"{city} ({offset_text(found)})"
