from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.forms import rows_for_actions
from adminsite._http.requests import find_view
from adminsite._http.saved_views import owner_of, saved_for
from adminsite._http.urls import PAGING_KEYS
from adminsite.filters.base import (
    Filter,
    FilterOption,
    FilterValue,
    parse_filters,
)
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission
from adminsite.query import QuerySpec, Sort
from adminsite.saved_views import SavedView, clean_query
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.database import SessionAdapter


if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.database import SessionAdapter

__all__ = [
    "COLUMNS_KEY",
    "COLUMNS_PARAM",
    "SIZE_KEY",
    "SIZE_PARAM",
    "FilterPanel",
    "ListRequest",
    "active_chips",
    "active_view",
    "as_context",
    "build_panels",
    "export_params",
    "list_records",
    "read_columns",
    "read_list_request",
    "read_page",
    "read_page_size",
    "read_sort",
    "rows_context",
    "sort_value",
    "total_text",
    "wants_partial",
]

COLUMNS_PARAM = "cols"
COLUMNS_KEY = "adminsite_columns"
SIZE_PARAM = "size"
SIZE_KEY = "adminsite_page_size"


@dataclass
class FilterPanel:
    """One filter as the page needs it: the control, and what is chosen."""

    filter: Filter
    options: Sequence[FilterOption] = ()
    value: FilterValue | None = None

    @property
    def active(self) -> bool:
        """Whether this filter is narrowing the list right now."""
        return self.value is not None

    @property
    def chosen(self) -> tuple[str, ...]:
        """The values picked, for ticking the right boxes."""
        return self.value.values if self.value else ()

    @property
    def chip(self) -> str:
        """The text of the chip shown above the list."""
        if self.value is None:
            return ""
        return self.filter.describe(self.value, self.options)


@dataclass
class ListRequest:
    """Everything the list page reads out of the query string."""

    search: str = ""
    sort: tuple[Sort, ...] = ()
    page: int = 1
    values: tuple[FilterValue, ...] = field(default_factory=tuple)
    after: str = ""
    before: str = ""
    columns: tuple[str, ...] = ()
    size: int = 0


def read_list_request(request: Request, view: ModelView[Any]) -> ListRequest:
    """Read the search, the sort, the page and the filters from the URL."""
    params = request.query_params
    grouped: dict[str, list[str]] = {}
    for key, value in params.multi_items():
        grouped.setdefault(key, []).append(value)

    return ListRequest(
        search=params.get("q", "").strip(),
        sort=read_sort(params.get("sort", ""), view, request),
        page=read_page(params.get("page", "1")),
        values=parse_filters(view._pages.list_filters(request), grouped),
        after=params.get("after", ""),
        before=params.get("before", ""),
        columns=read_columns(request, view),
        size=read_page_size(request, view),
    )


def read_sort(raw: str, view: ModelView[Any], request: Request) -> tuple[Sort, ...]:
    """The sort asked for in the URL, if it is one the user may ask for.

    Only a column this user can read somewhere on the view counts. Sorting
    by any other column would put the rows in the order of a value that is
    kept from them, which reads it back a page at a time.
    """
    if not raw:
        return ()
    sort = Sort.parse(raw)
    if sort.path in view._pages.readable_paths(request) and view._pages.sortable(
        sort.path
    ):
        return (sort,)
    return ()


def read_page_size(request: Request, view: ModelView[Any]) -> int:
    """The rows per page: the one picked in the URL, or from last time.

    A pick is remembered in the session, when there is one, so a list keeps
    the size someone chose for it on the next visit.
    """
    session = request.scope.get("session")
    remembered: dict[str, int] = (
        session.get(SIZE_KEY, {}) if session is not None else {}
    )
    picked: int | None = None
    if SIZE_PARAM in request.query_params:
        try:
            picked = int(request.query_params[SIZE_PARAM])
        except ValueError:
            picked = None
        if session is not None and picked in view._pages.page_sizes(request):
            session[SIZE_KEY] = {**remembered, view.name: picked}
    else:
        picked = remembered.get(view.name)
    return view._pages.pick_page_size(picked, request)


def read_columns(request: Request, view: ModelView[Any]) -> tuple[str, ...]:
    """The columns picked for this list, from the URL or from last time.

    A pick made in the URL is remembered in the session, when there is one,
    so the list keeps those columns on the next visit. An empty pick goes
    back to the default.
    """
    session = request.scope.get("session")
    remembered: dict[str, list[str]] = (
        session.get(COLUMNS_KEY, {}) if session is not None else {}
    )
    if COLUMNS_PARAM in request.query_params:
        picked = [
            path
            for value in request.query_params.getlist(COLUMNS_PARAM)
            for path in value.split(",")
            if path
        ]
        if session is not None:
            if picked:
                remembered = {**remembered, view.name: picked}
            else:
                remembered = {
                    name: paths
                    for name, paths in remembered.items()
                    if name != view.name
                }
            session[COLUMNS_KEY] = remembered
    else:
        picked = remembered.get(view.name, [])
    return view._pages.pick_columns(picked, request)


def read_page(raw: str) -> int:
    """The page number asked for, which is 1 unless it is a sane number."""
    try:
        return max(int(raw), 1)
    except ValueError:
        return 1


async def build_panels(
    view: ModelView[Any],
    session: "SessionAdapter",
    spec: QuerySpec,
    request: Request,
) -> list[FilterPanel]:
    """Build each filter's control, with counts where it offers them."""
    chosen = {value.name: value for value in spec.filters}
    offered = await view._reader.filter_options(session, spec, request=request)
    return [
        FilterPanel(filter=item, options=options, value=chosen.get(item.name))
        for item, options in offered
    ]


def wants_partial(request: Request) -> bool:
    """Whether this came from HTMX and only needs the table back."""
    return request.headers.get("hx-request") == "true"


def sort_value(spec: QuerySpec) -> str:
    """The sort as it appears in the URL, for keeping it across links."""
    return str(spec.sort[0]) if spec.sort else ""


def active_chips(panels: Sequence[FilterPanel]) -> list[FilterPanel]:
    """The filters currently narrowing the list."""
    return [panel for panel in panels if panel.active]


def active_view(saved: Sequence[SavedView], query: str) -> SavedView | None:
    """The saved view the list is showing right now, if any."""
    current = clean_query(query)
    return next((item for item in saved if item.query == current), None)


def export_params(request: Request) -> dict[str, Any]:
    """The current search and filters, for a link that keeps them."""
    params: dict[str, Any] = {}
    for key, value in request.query_params.multi_items():
        if key in PAGING_KEYS:
            continue
        current = params.get(key)
        if current is None:
            params[key] = value
        elif isinstance(current, list):
            current.append(value)
        else:
            params[key] = [current, value]
    return params


def total_text(page: Any) -> str:
    """How many records match, as the list says it: exact, about, or more."""
    if page.total is None:
        return ""
    number = f"{page.total:,}"
    if page.at_least:
        return _("more than {count}", count=number)
    if page.estimated:
        return _("about {count}", count=number)
    return number


def as_context(
    view: ModelView[Any],
    request: Request,
    spec: QuerySpec,
    page: Any,
    panels: Sequence[FilterPanel],
    read: ListRequest,
) -> dict[str, Any]:
    """The values every list template needs."""
    return {
        "view": view,
        "page": page,
        "page_number": read.page,
        "total_text": total_text(page),
        "columns": read.columns,
        "page_size": read.size,
        "page_sizes": view._pages.page_sizes(request),
        "column_choices": view._pages.column_choices(request),
        "columns_changed": read.columns != view._pages.list_fields(request),
        "current_query": clean_query(request.url.query),
        "panels": panels,
        "chips": active_chips(panels),
        "search": read.search,
        "sort": sort_value(spec),
        "spec": spec,
        "export_params": export_params(request),
        "actions": view._actions.on("selection", request),
        "record_actions": view._actions.on("record", request),
        "view_actions": view._actions.on("view", request),
    }


async def rows_context(
    view: ModelView[Any],
    request: Request,
    records: Sequence[Any],
    columns: Sequence[str],
) -> dict[str, Any]:
    """What drawing these rows of the list needs, for this user.

    Whether a row opens the record or its form, the actions on the ticked
    rows and on each record this user may run, and the cells of each row
    whose values change in place.
    """
    can_edit = await view.allows(Permission.EDIT, request=request, record=None)
    selection = [
        item
        for item in view._actions.on("selection", request)
        if await view.allows(item.permission, request=request, record=None)
    ]
    record_actions = [
        item
        for item in view._actions.on("record", request)
        if await view.allows(item.permission, request=request, record=None)
    ]
    # A record action can be refused for one record and allowed for the next.
    row_actions = {
        view._fields.identity_of(record): [
            item
            for item in record_actions
            if await view.allows(item.permission, request=request, record=record)
        ]
        for record in records
    }
    # So is a change: only a record this user may change offers a cell.
    editing = can_edit and bool(view._settings.inline_editable_fields)
    editable = {
        view._fields.identity_of(record): view._pages.editable_in_list(
            request, record, columns
        )
        for record in records
        if editing
        and await view.allows(Permission.EDIT, request=request, record=record)
    }
    return {
        "can_detail": await view.allows(
            Permission.VIEW_DETAIL, request=request, record=None
        ),
        "can_edit": can_edit,
        "actions": selection,
        "record_actions": record_actions,
        "row_actions": row_actions,
        "editable": editable,
    }


async def list_records(admin: "Admin", request: Request) -> Response:
    """One page of records, with the search, filters and sort applied."""
    view = find_view(admin, request)
    read = read_list_request(request, view)

    spec = view._reader.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        page=read.page,
        after=read.after,
        before=read.before,
        paths=read.columns,
        size=read.size,
    )
    async with admin.database.session() as session:
        page = await view._reader.fetch_page(session, spec, request=request)
        await view._reader.load_values(
            session,
            list(page),
            read.columns or view._pages.list_fields(request),
            request=request,
        )
        panels = await build_panels(view, session, spec, request)

    context = as_context(view, request, spec, page, panels, read)
    context["can_create"] = await view.allows(
        Permission.CREATE, request=request, record=None
    )
    context["can_export"] = await view.allows(
        Permission.EXPORT, request=request, record=None
    )
    context["can_import"] = await view.allows(
        Permission.IMPORT, request=request, record=None
    )
    context.update(await rows_context(view, request, list(page), read.columns))
    # Offer only the actions this user may run.
    context["view_actions"] = [
        item
        for item in context["view_actions"]
        if await view.allows(item.permission, request=request, record=None)
    ]
    context["action_rows"] = await rows_for_actions(
        admin,
        view,
        [*context["actions"], *context["view_actions"], *context["record_actions"]],
        request,
    )
    context["single_actions"] = [*context["record_actions"], *context["view_actions"]]
    context["saving_views"] = admin.saved_views is not None
    context["saved_views"] = await saved_for(admin, view, request)
    context["view_owner"] = owner_of(admin, request)
    context["active_view"] = active_view(context["saved_views"], request.url.query)

    template = "_table.html" if wants_partial(request) else "list.html"
    return await admin.render(template, request, context)
