from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.requests import find_view, read_form
from adminsite._http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.messages import add_message
from adminsite.permissions import Permission
from adminsite.saved_views import SavedView, SavedViews, clean_query
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin


if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "NAME_LIMIT",
    "delete_list_view",
    "delete_view",
    "owner_of",
    "save_list_view",
    "save_view",
    "saved_for",
]

NAME_LIMIT = 100


def owner_of(admin: "Admin", request: Request) -> str | None:
    """Who a saved view belongs to: the signed in user, or no one."""
    user = request.scope.get("user_record")
    if user is None or admin.auth is None:
        return None
    return admin.auth.identity(user)


async def saved_for(
    admin: "Admin", view: ModelView[Any], request: Request
) -> list[SavedView]:
    """The saved views this person sees on a list."""
    if admin.saved_views is None:
        return []
    return await admin.saved_views.visible_to(view.name, owner_of(admin, request))


def _store_of(admin: "Admin") -> SavedViews:
    if admin.saved_views is None:
        raise HTTPException(status_code=404, detail=_("Saved views are switched off."))
    return admin.saved_views


async def save_view(
    admin: "Admin", request: Request, view: ModelView[Any], form: dict[str, object]
) -> Response:
    """Keep the list as it is now under a name."""
    store = _store_of(admin)
    await view._ensure(Permission.VIEW, request=request)

    name = str(form.get("name", "")).strip()[:NAME_LIMIT]
    query = clean_query(str(form.get("query", "")))
    back = Urls(request).list(view)
    if not name:
        add_message(request, _("Give the view a name."), kind="error")
        return RedirectResponse(f"{back}?{query}" if query else back, 303)

    await store.save(
        SavedView(
            view=view.name,
            name=name,
            query=query,
            owner=owner_of(admin, request),
            shared=form.get("shared") in ("on", "1", "true"),
        )
    )
    add_message(request, _("Saved as {name}.", name=name))
    return RedirectResponse(f"{back}?{query}" if query else back, 303)


async def delete_view(
    admin: "Admin", request: Request, view: ModelView[Any]
) -> Response:
    """Remove one of the person's own saved views."""
    store = _store_of(admin)
    try:
        key = int(request.path_params["saved"])
    except ValueError:
        raise HTTPException(status_code=404, detail=_("No such view.")) from None

    if await store.delete(key, owner_of(admin, request)):
        add_message(request, _("View removed."))
    else:
        add_message(
            request, _("Only the person who saved a view can remove it."), "error"
        )
    return RedirectResponse(Urls(request).list(view), 303)


async def save_list_view(admin: "Admin", request: Request) -> Response:
    """Keep the current search, filters, sort and columns under a name."""
    view = find_view(admin, request)
    form = await read_form(request)
    return await save_view(admin, request, view, form)


async def delete_list_view(admin: "Admin", request: Request) -> Response:
    """Remove a saved view."""
    view = find_view(admin, request)
    await read_form(request)
    return await delete_view(admin, request, view)
