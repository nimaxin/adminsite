"""Switching the time zone the admin shows times in."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import own_cookie, read_form
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
    "TimezoneChoice",
    "choose_timezone",
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


@dataclass(frozen=True, slots=True)
class TimezoneChoice:
    """A time zone the menu offers, named by its city."""

    value: str
    """What choosing it posts: the zone's name, or nothing for the browser's."""
    city: str
    offset: str
    """How far it is from UTC now, such as UTC+03:30; empty for UTC itself."""
    shown: bool
    """Whether times are shown in it now."""


def timezone_menu(admin: "Admin", request: Request) -> list[TimezoneChoice]:
    """The time zones the account menu offers, empty where it offers no other.

    The browser's own zone comes first where the admin does not list it, so
    choosing it follows the browser again.
    """
    if len(admin.timezones) < 2:
        return []
    shown = request.scope.get("adminsite_timezone", admin.timezone)
    choices = [_choice(name, name, shown) for name in admin.timezones]
    browser = own_cookie(request, BROWSER_TIMEZONE_COOKIE)
    if find_timezone(browser) is not None and browser not in admin.timezones:
        choices.insert(0, _choice("", browser, shown))
    return choices


def _choice(value: str, name: str, shown: str) -> TimezoneChoice:
    found = find_timezone(name)
    return TimezoneChoice(
        value=value,
        city=name.rpartition("/")[2].replace("_", " "),
        offset="" if found is None or name == "UTC" else offset_text(found),
        shown=name == shown,
    )
