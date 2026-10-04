"""What a picker offers as someone types, for a field or an action."""

from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.form_rows import Choice
from adminsite._http.forms import title_for
from adminsite._http.requests import field_or_404, find_view
from adminsite.fields import RelationField
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission
from adminsite.views import ModelView
from adminsite.views.picker import RESULT_LIMIT, Picker

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "action_lookup",
    "looked_up",
    "lookup",
]


async def lookup(admin: "Admin", request: Request) -> Response:
    """The records a relation field offers, narrowed by what was typed.

    A picker only appears on a form, so this needs the permission that
    opens one. The records themselves come through the target's own view,
    so its scope and its permissions apply here as on any other page.
    """
    view = find_view(admin, request)
    if not await view.allows(Permission.CREATE, request=request, record=None):
        await view._ensure(Permission.EDIT, request=request)

    path = request.path_params["path"]
    item = field_or_404(view, path)
    if not isinstance(item, RelationField):
        raise HTTPException(
            status_code=404, detail=_("{path} is not a link.", path=repr(path))
        )
    return await looked_up(admin, request, view, item)


async def action_lookup(admin: "Admin", request: Request) -> Response:
    """The records a link in an action's dialog offers, narrowed by what was typed.

    It needs what running the action needs, and the records come through
    the target's own view, as a form's link does.
    """
    view = find_view(admin, request)
    found = view._actions.find(request.path_params["name"], request)
    if found is None:
        raise HTTPException(status_code=404, detail=_("No such action."))
    await view._ensure(found.permission, request=request)

    name = request.path_params["input"]
    item = next((one for one in found.inputs if one.name == name), None)
    if not isinstance(item, RelationField):
        raise HTTPException(
            status_code=404, detail=_("{path} is not a link.", path=repr(name))
        )
    return await looked_up(admin, request, view, item)


async def looked_up(
    admin: "Admin", request: Request, view: ModelView[Any], item: RelationField
) -> Response:
    """The records a link offers for what was typed, as a list to pick from."""
    picker = Picker(admin.views, admin.inspector, item, request)
    async with admin.database.session() as session:
        page = await picker.page(
            session,
            search=request.query_params.get("q", "").strip(),
            limit=RESULT_LIMIT,
        )
        choices = [
            Choice(picker.key_of(record), title_for(admin, item, record))
            for record in page.rows
        ]

    # The page reads one record past the limit, so it already knows whether
    # more match, without counting them.
    return await admin.render(
        "_lookup.html",
        request,
        {"view": view, "choices": choices, "more": page.has_next},
    )
