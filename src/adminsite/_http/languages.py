"""Switching the language the admin speaks."""

from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import read_form
from adminsite._http.urls import Urls

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "choose_language",
]


async def choose_language(admin: "Admin", request: Request) -> Response:
    """Switch the admin to another language, and go back where we were."""
    form = await read_form(request)
    wanted = str(form.get("language", ""))
    back = str(form.get("next", "")) or Urls(request).index()
    # Only a path inside this site, so the form cannot send anyone elsewhere.
    if not back.startswith("/") or back.startswith("//"):
        back = Urls(request).index()
    response = RedirectResponse(back, status_code=303)
    if wanted in admin.languages:
        response.set_cookie(
            "adminsite_language",
            wanted,
            max_age=365 * 24 * 3600,
            path=Urls(request).index(),
            samesite="lax",
        )
    return response
