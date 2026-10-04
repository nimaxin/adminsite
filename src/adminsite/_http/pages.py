"""A page of the project's own."""

from typing import TYPE_CHECKING

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.requests import read_form
from adminsite.exceptions import PermissionDeniedError
from adminsite.i18n import gettext as _

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "custom_page",
]


async def custom_page(admin: "Admin", request: Request) -> Response:
    """A page of the project's own, shown or sent a form."""
    name = request.path_params["page"]
    page = admin.pages.get(name)
    if page is None:
        raise HTTPException(
            status_code=404, detail=_("No page at {name}.", name=repr(name))
        )
    if not await page.allows(request):
        raise PermissionDeniedError("open", page.label)
    if request.method == "POST":
        return await page.post(request, await read_form(request))
    return await page.get(request)
