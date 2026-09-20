from typing import TYPE_CHECKING

from sqlalchemy import func, select
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite.http.listing import (
    as_context,
    build_panels,
    read_list_request,
    wants_partial,
)
from adminsite.security import Action
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin


async def index(admin: "Admin", request: Request) -> Response:
    """The front page, showing each model and how many records it has."""
    counts = []
    async with admin.database.session() as session:
        for view in admin.views:
            if not await view.allows(Action.VIEW, request=request):
                continue
            total = await session.scalar(
                select(func.count()).select_from(
                    view.repository.base_statement(view.scope_for(request)).subquery()
                )
            )
            counts.append((view, int(total or 0)))

    return await admin.render("index.html", request, {"counts": counts, "view": None})


async def list_records(admin: "Admin", request: Request) -> Response:
    """One page of records, with the search, filters and sort applied."""
    view = find_view(admin, request)
    read = read_list_request(request, view)

    spec = view.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        page=read.page,
    )
    async with admin.database.session() as session:
        page = await view.fetch_page(session, spec, request=request)
        panels = await build_panels(view, session, spec, request)

    context = as_context(view, request, spec, page, panels, read)
    context["can_create"] = await view.allows(Action.CREATE, request=request)

    template = "_table.html" if wants_partial(request) else "list.html"
    return await admin.render(template, request, context)


def find_view(admin: "Admin", request: Request) -> ModelView:
    """The view the URL names, or a 404."""
    name = request.path_params.get("view", "")
    view = admin.views.find(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No page at {name!r}.")
    return view
