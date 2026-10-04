"""The view, record, field and key a URL names, and a form read safely."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request

from adminsite._http.csrf import FIELD_NAME, TOKEN_HEADER, is_valid
from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "field_or_404",
    "find_view",
    "key_of",
    "load_or_404",
    "read_form",
    "read_key",
]


def find_view(admin: "Admin", request: Request) -> ModelView[Any]:
    """The view the URL names, or a 404."""
    name = request.path_params.get("view", "")
    view = admin.views.find(name)
    if view is None:
        raise HTTPException(
            status_code=404, detail=_("No page at {name}.", name=repr(name))
        )
    return view


async def load_or_404(
    admin: "Admin",
    view: ModelView[Any],
    request: Request,
    *,
    paths: Sequence[str] | None = None,
) -> Any:
    """Load the record the URL names, or raise a 404."""
    async with admin.database.session() as session:
        record = await view._reader.fetch_record(
            session,
            read_key(request),
            paths=view._pages.load_paths(request) if paths is None else paths,
            request=request,
        )
    if record is None:
        raise HTTPException(status_code=404, detail=_("No such record."))
    return record


def field_or_404(view: ModelView[Any], path: str) -> Any:
    """The field a path in the URL names, or a 404 when it names none."""
    try:
        return view._fields.field_for(path)
    except AdminSiteError:
        raise HTTPException(
            status_code=404, detail=_("No field at {path}.", path=repr(path))
        ) from None


def read_key(request: Request) -> Any:
    """The primary key out of the URL, as one value or a tuple."""
    return key_of(request.path_params["key"])


def key_of(raw: str) -> Any:
    """A key written as text, as one value or a tuple for a composite key."""
    parts = raw.split(",")
    return tuple(parts) if len(parts) > 1 else raw


async def read_form(request: Request) -> dict[str, Any]:
    """Read a submitted form, after checking it came from the admin."""
    form = await request.form()
    data: dict[str, Any] = {}
    for key in form:
        values = form.getlist(key)
        data[key] = values if len(values) > 1 else values[0]

    submitted = data.pop(FIELD_NAME, None) or request.headers.get(TOKEN_HEADER)
    token = submitted if isinstance(submitted, str) else None
    if not is_valid(request, token):
        raise HTTPException(status_code=403, detail=_("This form has expired."))
    return data
