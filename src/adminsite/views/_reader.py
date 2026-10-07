"""What a view reads: its pages of records, one record, and what goes with them."""

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from starlette.requests import Request

from adminsite._orm.repository import Scope, SQLAlchemyRepository
from adminsite._text import template_names
from adminsite.database import SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.fields import ComputedField, RelationField
from adminsite.fields.computed import LOADED
from adminsite.filters import FilterOption, FilterValue
from adminsite.filters.sql import SQLFilter, SQLFilterContext
from adminsite.permissions import Permission
from adminsite.query import CountMode, Page, Pagination, QuerySpec, Sort
from adminsite.schema import ModelSchema
from adminsite.views._fields import ViewFields
from adminsite.views._linked_names import mark_unseen
from adminsite.views._pages import PageFields
from adminsite.views._settings import SettingsReader

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = [
    "Reader",
]

# The model of the view this part belongs to.
M = TypeVar("M")


class Reader(Generic[M]):
    """What a view reads, always within its scope and after its permission check."""

    def __init__(
        self,
        view: "ModelView[M]",
        settings: SettingsReader,
        fields: ViewFields[M],
        pages: PageFields[M],
        repository: SQLAlchemyRepository[M],
        schema: ModelSchema,
    ) -> None:
        self._view = view
        self._model: type[M] = view.model
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
        page = await self._repository.list(
            session, spec, self._view._scope_for(request)
        )
        await self._hide_unseen(session, page.rows, spec.paths, request)
        return page

    async def fetch_record(
        self,
        session: SessionAdapter,
        key: Any,
        *,
        paths: Sequence[str] = (),
        request: Request,
    ) -> M | None:
        """Load one record, or nothing if it is missing or out of scope."""
        await self._view._ensure(Permission.VIEW, request=request)
        record = await self._repository.get(
            session, key, tuple(paths), self._view._scope_for(request)
        )
        if record is not None:
            await self._hide_unseen(session, [record], paths, request)
        return record

    def narrows(self, request: Request) -> bool:
        """Whether scope_query leaves out any record for this user.

        One that hands the statement back as it came leaves out nothing, such
        as for a superuser.
        """
        if not self._view._scoped:
            return False
        statement = self._repository.base_statement()
        return self._view.scope_query(statement, request=request) is not statement

    async def readable_history(
        self, session: SessionAdapter, keys: Collection[str], *, request: Request
    ) -> set[str]:
        """The keys among these whose history this user may read.

        As on the record's History tab: the scope holds the record, and
        `allows` lets this user read its history. A key that names no record,
        such as a deleted one's, counts only where the scope leaves out
        nothing, as there is no record left to check it against.
        """
        await self._view._ensure(Permission.VIEW, request=request)
        await self._view._ensure(Permission.HISTORY, request=request)
        narrows = self.narrows(request)
        if not narrows and not self._view._custom_allows:
            return set(keys)
        records = await self._repository.get_many(
            session, keys, self._view._scope_for(request)
        )
        found = {self._fields.identity_of(record): record for record in records}
        gone: set[str] = set() if narrows else {key for key in keys if key not in found}
        return gone | {
            key
            for key, record in found.items()
            if await self._view.allows(
                Permission.HISTORY, request=request, record=record
            )
        }

    async def _hide_unseen(
        self,
        session: SessionAdapter,
        records: Sequence[Any],
        paths: Sequence[str],
        request: Request,
    ) -> None:
        """Mark each linked record that its own view's scope keeps from this user.

        Its name and its values then read as hidden wherever this view would
        show them, as that view's own pages never show the record. Only a
        view whose scope_query narrows anything is asked: once for each
        link, with the keys already loaded. Child rows an inline loaded get
        the same from their own view.
        """
        views = self._view._views
        if not records or views is None:
            return
        for name in dict.fromkeys(path.split(".", 1)[0] for path in paths):
            relation = self._schema.relations.get(name)
            if relation is None:
                continue
            # Only what the read loaded: anything else would start a lazy load.
            linked = [
                one
                for record in records
                if name in vars(record)
                for one in _as_list(vars(record)[name])
            ]
            # Kept from the user only where no view that could open it holds
            # it, as a link opens whichever of the model's views does.
            targets = self._linked_views(name, relation.target)
            if linked and targets and all(target._scoped for target in targets):
                seen: set[str] = set()
                for target in targets:
                    seen |= await target._repository.visible_keys(
                        session, linked, target._scope_for(request)
                    )
                for one in linked:
                    if targets[0]._fields.identity_of(one) not in seen:
                        mark_unseen(one)
            if name in self._view._inline_views and linked:
                child = self._view._inline_views[name]
                await child._reader._hide_unseen(
                    session, linked, child._settings.candidates, request
                )

    def _linked_views(self, name: str, model: type[Any]) -> "list[ModelView[Any]]":
        """The views that could open a linked record: the one the link names, or all."""
        views = self._view._views
        if views is None:
            return []
        try:
            item = self._fields.field_for(name)
        except AdminSiteError:
            item = None
        if isinstance(item, RelationField) and item.view is not None:
            named = views.for_relation(item)
            return [named] if named is not None else []
        return views.all_for_model(model)

    async def filter_options(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Request
    ) -> list[tuple[SQLFilter[Any], Sequence[FilterOption]]]:
        """Each filter beside the list, with the choices it offers.

        Counted within the scope, so a count never gives away how many
        records the user may not see, and only where the filter's
        `show_counts`, or else the view's count mode, asks for it.
        """
        await self._view._ensure(Permission.VIEW, request=request)
        context = SQLFilterContext(
            session, self._repository, spec, self._view._scope_for(request)
        )
        return [
            (item, await item.options(replace(context, counts=self._counted(item))))
            for item in self._pages.list_filters(request)
        ]

    def _counted(self, item: SQLFilter[Any]) -> bool:
        """Whether a filter's options are counted: as it says, or as the view counts."""
        if item.show_counts is None:
            return self._view.count_mode is CountMode.EXACT
        return item.show_counts

    async def fetch_related(
        self,
        session: SessionAdapter,
        record: M,
        path: str,
        *,
        limit: int,
        request: Request,
    ) -> "tuple[list[tuple[Any, ModelView[Any] | None]], int]":
        """The first records a to-many link of this record holds, and the total.

        Read as the list reads a link: through the view it names, or else
        through every view of the linked model, so a record any of them
        holds is named and counted, and one that none holds is left out.
        Each record comes with the view that opens it, or None. A model no
        view shows is read directly.
        """
        await self._view._ensure(Permission.VIEW_DETAIL, request=request, record=record)
        targets = self._linked_views(path, self._schema.relations[path].target)
        records, total = await self._repository.related(
            session,
            record,
            path,
            limit=limit,
            scope=self._held_by_any(targets, request),
        )
        openers = await self._views_that_open(session, targets, records, request)
        return list(zip(records, openers, strict=True)), total

    def _held_by_any(
        self, targets: "Sequence[ModelView[Any]]", request: Request
    ) -> Scope | None:
        """A scope that keeps what any of these views holds, or None for everything."""
        if not targets or not all(target._scoped for target in targets):
            return None
        if len(targets) == 1:
            return targets[0]._scope_for(request)
        return targets[0]._repository.within_any(
            [target._scope_for(request) for target in targets]
        )

    async def _views_that_open(
        self,
        session: SessionAdapter,
        targets: "Sequence[ModelView[Any]]",
        records: Sequence[Any],
        request: Request,
    ) -> "list[ModelView[Any] | None]":
        """The view each linked record opens in, or None where none will.

        The first of the views that holds it and lets this user open it.
        Each view's scope is asked once, for the records still without one.
        """
        found: list[ModelView[Any] | None] = [None] * len(records)
        for target in targets:
            waiting = [index for index, opener in enumerate(found) if opener is None]
            if not waiting:
                break
            # The records were read through a lone view's scope, so it holds them.
            if target._scoped and len(targets) > 1:
                held = await target._repository.visible_keys(
                    session,
                    [records[index] for index in waiting],
                    target._scope_for(request),
                )
                waiting = [
                    index
                    for index in waiting
                    if target._fields.identity_of(records[index]) in held
                ]
            for index in waiting:
                if await target.allows(
                    Permission.VIEW_DETAIL, request=request, record=records[index]
                ):
                    found[index] = target
        return found

    async def load_values(
        self,
        session: SessionAdapter,
        records: Sequence[M],
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


def _as_list(value: Any) -> list[Any]:
    """What a link holds, as a list: its records, or its one record, or none."""
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [one for one in value if one is not None]
    return [value]
