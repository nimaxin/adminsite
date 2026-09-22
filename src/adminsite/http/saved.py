from typing import TYPE_CHECKING

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite.http.templating import add_message
from adminsite.http.urls import Urls
from adminsite.saved_views import SavedView, SavedViews, clean_query
from adminsite.security import Permission
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

NAME_LIMIT = 100


def owner_of(admin: "Admin", request: Request) -> str | None:
    """Who a saved view belongs to: the signed in user, or no one."""
    user = request.scope.get("user_record")
    if user is None or admin.auth is None:
        return None
    return admin.auth.identity(user)


async def saved_for(
    admin: "Admin", view: ModelView, request: Request
) -> list[SavedView]:
    """The saved views this person sees on a list."""
    if admin.saved_views is None:
        return []
    return await admin.saved_views.visible_to(view.name, owner_of(admin, request))


def _store_of(admin: "Admin") -> SavedViews:
    if admin.saved_views is None:
        raise HTTPException(status_code=404, detail="Saved views are switched off.")
    return admin.saved_views


async def save_view(
    admin: "Admin", request: Request, view: ModelView, form: dict[str, object]
) -> Response:
    """Keep the list as it is now under a name."""
    store = _store_of(admin)
    await view.ensure(Permission.VIEW, request=request)

    name = str(form.get("name", "")).strip()[:NAME_LIMIT]
    query = clean_query(str(form.get("query", "")))
    back = Urls(request).list(view)
    if not name:
        add_message(request, "Give the view a name.", kind="error")
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
    add_message(request, f"Saved as {name}.")
    return RedirectResponse(f"{back}?{query}" if query else back, 303)


async def delete_view(admin: "Admin", request: Request, view: ModelView) -> Response:
    """Remove one of the person's own saved views."""
    store = _store_of(admin)
    try:
        key = int(request.path_params["saved"])
    except ValueError:
        raise HTTPException(status_code=404, detail="No such view.") from None

    if await store.delete(key, owner_of(admin, request)):
        add_message(request, "View removed.")
    else:
        add_message(request, "Only the person who saved a view can remove it.", "error")
    return RedirectResponse(Urls(request).list(view), 303)
