from typing import TYPE_CHECKING

from sqlalchemy import func, select
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite.query import CountMode
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
    """One page of records."""
    view = find_view(admin, request)
    page_number = read_page(request)

    spec = view.build_spec(request=request, page=page_number)
    async with admin.database.session() as session:
        page = await view.fetch_page(session, spec, request=request)

    return await admin.render(
        "list.html",
        request,
        {
            "view": view,
            "page": page,
            "page_number": page_number,
            "columns": view.get_list_display(request),
            "can_create": await view.allows(Action.CREATE, request=request),
            "count_mode": view.count_mode is CountMode.EXACT,
        },
    )


def find_view(admin: "Admin", request: Request) -> ModelView:
    """The view the URL names, or a 404."""
    name = request.path_params.get("view", "")
    view = admin.views.find(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No page at {name!r}.")
    return view


def read_page(request: Request) -> int:
    """The page number asked for, which is 1 unless it is a sane number."""
    raw = request.query_params.get("page", "1")
    try:
        number = int(raw)
    except ValueError:
        return 1
    return max(number, 1)
