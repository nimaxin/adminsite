"""What a view reads: its pages of records, one record, and what goes with them."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from adminsite.backends.sqlalchemy.filters import SQLFilter, SQLFilterContext
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.fields import ComputedField, RelationField
from adminsite.fields.computed import LOADED
from adminsite.filters import FilterOption, FilterValue
from adminsite.query import Page, Pagination, QuerySpec, Sort
from adminsite.schema import ModelSchema
from adminsite.security import Permission
from adminsite.text import template_names
from adminsite.views.pages import PageFields
from adminsite.views.settings import SettingsReader
from adminsite.views.view_fields import ViewFields

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["Reader"]


class Reader:
    """What a view reads, always within its scope and after its permission check."""

    def __init__(
        self,
        view: "ModelView[Any]",
        settings: SettingsReader,
        fields: ViewFields,
        pages: PageFields,
        repository: SQLAlchemyRepository[Any],
        schema: ModelSchema,
    ) -> None:
        self._view = view
        self._model: type[Any] = view.model
        self._settings = settings
        self._fields = fields
        self._pages = pages
        self._repository = repository
        self._schema = schema

    def build_spec(
        self,
        *,
        request: Request,
        search: str = "",
        filters: Sequence[FilterValue] = (),
        sort: Sequence[Sort] = (),
        page: int = 1,
        paths: Sequence[str] = (),
        after: str = "",
        before: str = "",
        size: int | None = None,
    ) -> QuerySpec:
        """Describe the read this view wants, page by page."""
        wanted = tuple(
            self._pages.loadable(tuple(paths) or self._pages.list_fields(request))
        )
        spec = QuerySpec(
            paths=wanted,
            defer=self._deferred(request, wanted),
            search=search,
            search_paths=self._pages.search_paths(request),
            search_condition=self._view.search_condition(
                search.strip(), request=request
            )
            if search.strip()
            else None,
            filters=tuple(filters),
            sort=tuple(sort) or self._pages.default_sort(request),
            limit=size or self._view.page_size,
            count=self._view.count_mode,
            keyset=self._view.pagination is Pagination.KEYSET,
            after=after,
            before=before,
        )
        return spec.page(page)

    def _deferred(self, request: Request, loaded: Sequence[str]) -> tuple[str, ...]:
        """The columns to leave out of this query.

        A column the page reads is never left out, whatever the view says,
        since reading it afterwards would cost a query for every row. That
        covers the columns on show, the key, and the ones the record's name
        is built from.
        """
        keep = set(loaded) | set(self._schema.primary_key) | self._named_in_title()
        named = self._view.get_deferred_fields(request)
        paths = (
            self._settings.deferred_fields
            if named is self._view.deferred_fields
            else self._settings.paths("get_deferred_fields", named, takes="own columns")
        )
        return tuple(path for path in paths if path not in keep)

    def _named_in_title(self) -> set[str]:
        """The columns `record_title` reads, which every row needs."""
        return set(template_names(self._settings.record_title))

    async def fetch_page(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Request
    ) -> Page:
        """Read one page, within the scope and after a permission check."""
        await self._view._ensure(Permission.VIEW, request=request)
        return await self._repository.list(
            session, spec, self._view._scope_for(request)
        )

    async def fetch_record(
        self,
        session: SessionAdapter,
        key: Any,
        *,
        paths: Sequence[str] = (),
        request: Request,
    ) -> Any | None:
        """Load one record, or nothing if it is missing or out of scope."""
        await self._view._ensure(Permission.VIEW, request=request)
        return await self._repository.get(
            session, key, tuple(paths), self._view._scope_for(request)
        )

    async def filter_options(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Request
    ) -> list[tuple[SQLFilter[Any], Sequence[FilterOption]]]:
        """Each filter beside the list, with the choices it offers.

        Counted within the scope, so a count never gives away how many
        records the user may not see.
        """
        await self._view._ensure(Permission.VIEW, request=request)
        context = SQLFilterContext(
            session, self._repository, spec, self._view._scope_for(request)
        )
        return [
            (item, await item.options(context))
            for item in self._pages.list_filters(request)
        ]

    async def fetch_related(
        self,
        session: SessionAdapter,
        record: Any,
        path: str,
        *,
        limit: int,
        request: Request,
    ) -> tuple[Sequence[Any], int]:
        """The first records a to-many link of this record holds, and the total.

        Read through the linked model's own view, as a picker is, so its
        `scope_query` leaves out records, and their count, that it hides
        from this user. A model no view shows is read directly.
        """
        await self._view._ensure(Permission.VIEW_DETAIL, request=request, record=record)
        item = self._fields.field_for(path)
        target = (
            self._view._views.for_relation(item)
            if self._view._views is not None and isinstance(item, RelationField)
            else None
        )
        return await self._repository.related(
            session,
            record,
            path,
            limit=limit,
            scope=target._scope_for(request) if target is not None else None,
        )

    async def load_values(
        self,
        session: SessionAdapter,
        records: Sequence[Any],
        paths: Sequence[str],
        *,
        request: Request,
    ) -> None:
        """Run the loaders of the computed fields among `paths`, once for all.

        Each value waits on its record for the rest of the request, where
        the list, the record page, the export and the API read it.
        """
        if not records:
            return
        for path in dict.fromkeys(paths):
            if "." in path:
                continue
            try:
                item = self._fields.field_for(path)
            except AdminSiteError:
                continue
            if not isinstance(item, ComputedField) or item.load is None:
                continue
            found = await item.load(session, records)
            for record in records:
                waiting = vars(record).setdefault(LOADED, {})
                waiting[item.name] = found.get(
                    self._fields.key_value(record), item.default
                )
