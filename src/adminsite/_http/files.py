"""A file kept by one of a view's file fields."""

from typing import TYPE_CHECKING

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.requests import field_or_404, find_view
from adminsite.fields import FileField
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "stored_file",
]


async def stored_file(admin: "Admin", request: Request) -> Response:
    """A file kept by one of a view's file fields, for whoever may open the view."""
    view = find_view(admin, request)
    await view._ensure(Permission.VIEW, request=request)
    item = field_or_404(view, request.path_params["path"])
    if not isinstance(item, FileField):
        raise HTTPException(status_code=404, detail=_("No such file."))
    key = request.path_params["key"]
    return await item.storage.response(key, item.content_type(key))
