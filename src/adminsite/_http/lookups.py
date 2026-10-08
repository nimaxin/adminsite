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
from adminsite.views._picker import RESULT_LIMIT, Picker

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "action_lookup",
    "filter_lookup",
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
    picker = Picker(admin.views, admin.inspector, item, request)
    return await looked_up(admin, request, view, picker)


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
    picker = Picker(admin.views, admin.inspector, item, request)
    return await looked_up(admin, request, view, picker)


async def filter_lookup(admin: "Admin", request: Request) -> Response:
    """The records a relation filter offers, narrowed by what was typed.

    A filter only narrows the list, so seeing the list is enough, where a
    form's picker needs leave to create or edit. The records come through
    the linked model's own view, so its scope and its permissions apply.
    """
    view = find_view(admin, request)
    await view._ensure(Permission.VIEW, request=request)
    name = request.path_params["name"]
    for item in view._pages.list_filters(request):
        if item.name != name:
            continue
        picker = Picker.for_filter(admin.views, admin.inspector, view, item, request)
        if picker is not None:
            return await looked_up(admin, request, view, picker)
    raise HTTPException(status_code=404, detail=_("No such filter."))


async def looked_up(
    admin: "Admin", request: Request, view: ModelView[Any], picker: Picker
) -> Response:
    """The records a picker offers for what was typed, as a list to pick from."""
    item = picker.item
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
