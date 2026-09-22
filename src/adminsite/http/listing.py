from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request

from adminsite.backends.sqlalchemy.filters import SQLFilter, SQLFilterContext
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.filters import FilterOption, FilterValue, parse_filters
from adminsite.http.forms import rows_for_inputs
from adminsite.http.urls import PAGING_KEYS
from adminsite.query import QuerySpec, Sort
from adminsite.saved_views import SavedView, clean_query
from adminsite.views import ModelView

COLUMNS_PARAM = "cols"
COLUMNS_KEY = "adminsite_columns"


@dataclass
class FilterPanel:
    """One filter as the page needs it: the control, and what is chosen."""

    filter: SQLFilter
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


def read_list_request(request: Request, view: ModelView) -> ListRequest:
    """Read the search, the sort, the page and the filters from the URL."""
    params = request.query_params
    grouped: dict[str, list[str]] = {}
    for key, value in params.multi_items():
        grouped.setdefault(key, []).append(value)

    raw_sort = params.get("sort", "")
    return ListRequest(
        search=params.get("q", "").strip(),
        sort=(Sort.parse(raw_sort),) if raw_sort else (),
        page=read_page(params.get("page", "1")),
        values=parse_filters(view.get_filters(request), grouped),
        after=params.get("after", ""),
        before=params.get("before", ""),
        columns=read_columns(request, view),
    )


def read_columns(request: Request, view: ModelView) -> tuple[str, ...]:
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
    return view.pick_columns(picked, request)


def read_page(raw: str) -> int:
    """The page number asked for, which is 1 unless it is a sane number."""
    try:
        return max(int(raw), 1)
    except ValueError:
        return 1


async def build_panels(
    view: ModelView,
    session: SessionAdapter,
    spec: QuerySpec,
    request: Request,
) -> list[FilterPanel]:
    """Build each filter's control, with counts where it offers them."""
    chosen = {value.name: value for value in spec.filters}
    context = SQLFilterContext(session, view.repository, spec)

    panels = []
    for item in view.get_filters(request):
        panels.append(
            FilterPanel(
                filter=item,
                options=await item.options(context),
                value=chosen.get(item.name),
            )
        )
    return panels


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


def as_context(
    view: ModelView,
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
        "columns": read.columns,
        "column_choices": view.get_column_choices(request),
        "columns_changed": read.columns != view.get_list_display(request),
        "current_query": clean_query(request.url.query),
        "panels": panels,
        "chips": active_chips(panels),
        "search": read.search,
        "sort": sort_value(spec),
        "spec": spec,
        "export_params": export_params(request),
        "actions": view.get_actions(request),
        "action_rows": {
            item.name: rows_for_inputs(item.inputs)
            for item in view.get_actions(request)
        },
    }
