"""Which fields each page of a view shows, to whom, and which it locks."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.filters import SQLFilter
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.exceptions import AdminSiteError
from adminsite.query import Sort
from adminsite.schema import ModelSchema, RelationDirection
from adminsite.security import RequestAction
from adminsite.views.inline import Inline
from adminsite.views.settings import SettingsReader
from adminsite.views.view_fields import ViewFields

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["PageFields"]

# The pages an inline's rows are on: a new row's form, an existing one's, and
# the parent's record page.
_ROW_PAGES = (RequestAction.CREATE, RequestAction.EDIT, RequestAction.DETAIL)


class PageFields:
    """Which fields each page of a view shows this user, and which it locks.

    Each answer is for one request, since `can_access_field` and the view's
    `get_` methods may answer differently for each user.
    """

    def __init__(
        self,
        view: "ModelView[Any]",
        settings: SettingsReader,
        fields: ViewFields,
        inspector: SQLAlchemyInspector,
        schema: ModelSchema,
        inline_views: Mapping[str, "ModelView[Any]"],
    ) -> None:
        self._view = view
        self._model: type[Any] = view.model
        self._settings = settings
        self._fields = fields
        self._inspector = inspector
        self._schema = schema
        self._inline_views = inline_views

    def accessible(
        self, request: Any, paths: Sequence[str], action: RequestAction
    ) -> tuple[str, ...]:
        """The paths among these whose fields this user sees on this page."""
        return tuple(
            path
            for path in paths
            if self._view.can_access_field(
                request, self._fields.field_for(path), action
            )
        )

    def can_access_path(self, request: Any, path: str, action: RequestAction) -> bool:
        """Whether this user sees what a path holds on this page.

        A path that names no field has nothing to ask about: a filter of
        your own, say, or a field gone from the model since the audit log
        wrote it down.
        """
        try:
            field = self._fields.field_for(path)
        except AdminSiteError:
            return True
        return self._view.can_access_field(request, field, action)

    def list_fields(self, request: Any = None) -> tuple[str, ...]:
        """The columns the list shows, for this user."""
        shown = [
            path
            for path in self.listed()
            if not self._fields.field_for(path).hidden_in_list
        ]
        return self.accessible(request, shown, RequestAction.LIST)

    def column_choices(self, request: Any = None) -> tuple[str, ...]:
        """The columns the picker offers: the list's own, then the hidden ones."""
        hidden = [
            path
            for path in self.listed()
            if self._fields.field_for(path).hidden_in_list
        ]
        return self.list_fields(request) + self.accessible(
            request, hidden, RequestAction.LIST
        )

    def pick_columns(
        self, picked: Sequence[str], request: Any = None
    ) -> tuple[str, ...]:
        """The columns to show for what someone picked.

        Only columns on offer count, so a column hidden from this user cannot
        be brought back by editing the URL. Picking none gives the default.
        """
        wanted = set(picked)
        chosen = tuple(path for path in self.column_choices(request) if path in wanted)
        return chosen or self.list_fields(request)

    def page_sizes(self, request: Any = None) -> tuple[int, ...]:
        """The page sizes on offer, the view's own size among them."""
        if not self._view.page_size_options:
            return ()
        return tuple(sorted({*self._view.page_size_options, self._view.page_size}))

    def pick_page_size(self, wanted: int | None, request: Any = None) -> int:
        """The rows per page for what someone picked.

        Only a size on offer counts, so nobody can ask for a million rows
        by editing the URL.
        """
        offered = self.page_sizes(request)
        if wanted in offered:
            return int(wanted or self._view.page_size)
        return self._view.page_size

    def search_paths(self, request: Any) -> tuple[str, ...]:
        """The paths the search box looks in, checked like the setting.

        A field this user cannot see on the list is not searched, or a
        search would find records by what it holds.
        """
        named = self._view.get_searchable_fields(request)
        if named is self._view.searchable_fields:
            paths = self._settings.searchable_fields
        else:
            paths = self._settings.paths(
                "get_searchable_fields", named, takes="columns"
            )
        return self.accessible(request, paths, RequestAction.LIST)

    def list_filters(self, request: Any) -> tuple[SQLFilter[Any], ...]:
        """The filters offered beside the list, each built and checked.

        A filter on a field this user cannot see on the list is left out,
        since its choices, their counts and the rows it leaves would all
        give the field's values away.
        """
        named = self._view.get_list_filters(request)
        if named is self._view.list_filters:
            built = self._settings.list_filters
        else:
            built = self._settings.filters("get_list_filters", named)
        return tuple(
            item
            for item in built
            if self.can_access_path(request, item.path, RequestAction.LIST)
        )

    def default_sort(self, request: Any) -> tuple[Sort, ...]:
        """The order the list starts in, checked like the setting.

        A sort by a field this user cannot see on the list is dropped, so
        the rows never stand in the order of a value kept from them.
        """
        named = self._view.get_fields_default_sort(request)
        if named is self._view.fields_default_sort:
            sorts = self._settings.fields_default_sort
        else:
            sorts = self._settings.sorts("get_fields_default_sort", named)
        return tuple(
            sort
            for sort in sorts
            if self.can_access_path(request, sort.path, RequestAction.LIST)
        )

    def sortable(self, path: str) -> bool:
        """Whether a list can be sorted by this column.

        Left to the default, a relationship and a path through one holding
        many records are not, as no query can sort by them.
        """
        if self._settings.sortable_fields is not None:
            return path in self._settings.sortable_fields
        if not self._fields.field_for(path).stored:
            return False
        resolved = self._inspector.resolve(self._model, path)
        return resolved.field is not None and not resolved.crosses_collection

    def form_fields(
        self,
        request: Any = None,
        record: Any = None,
        *,
        page: RequestAction | None = None,
    ) -> tuple[str, ...]:
        """The fields the form shows, in order: a new record's, or `record`'s.

        `page` names the form instead, such as the edit form before its
        record is loaded. A column of a related model and a computed field
        are shown, never edited, so they stay off forms, as does a key
        nobody types in.
        """
        if page is None:
            page = RequestAction.CREATE if record is None else RequestAction.EDIT
        placed = [
            path
            for path in self._settings.candidates
            if self.editable(path) and not self.excluded_from(page, path)
        ]
        return self.accessible(request, placed, page)

    def detail_fields(self, request: Any = None, record: Any = None) -> tuple[str, ...]:
        """What the record page shows, for this user.

        A form-only field, such as a password to set, has nothing to show.
        """
        shown = [
            path
            for path in self._settings.candidates
            if not self._fields.field_for(path).form_only
            and not self.excluded_from(RequestAction.DETAIL, path)
        ]
        return self.accessible(request, shown, RequestAction.DETAIL)

    def exported(self, paths: Sequence[str], request: Any = None) -> tuple[str, ...]:
        """The columns of a list that go into its export."""
        return self.accessible(
            request,
            [
                path
                for path in paths
                if not self.excluded_from(RequestAction.EXPORT, path)
            ],
            RequestAction.EXPORT,
        )

    def readonly_paths(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The paths shown but not editable, named for the record or by themselves."""
        named = self._settings.paths(
            "get_readonly_fields",
            self._view.get_readonly_fields(request, record),
            takes="fields",
        )
        return named + tuple(
            path
            for path in self.form_fields(request, record)
            if path not in named and self.locked(path, saved=record is not None)
        )

    def locked(self, path: str, *, saved: bool) -> bool:
        """Whether a field is never editable, or not once its record is saved.

        A field with read_only=True is never editable. A key is fixed once
        the record exists: its URL, its history and the rows pointing at it
        depend on it. So is a link made only of key columns, such as the
        customer of a profile keyed by its customer.
        """
        if self._fields.field_for(path).read_only:
            return True
        if not saved:
            return False
        keys = set(self._schema.primary_key)
        relation = self._schema.relations.get(path)
        if relation is None:
            return path in keys
        return (
            relation.direction is RelationDirection.MANY_TO_ONE
            and bool(relation.local_columns)
            and set(relation.local_columns) <= keys
        )

    def writable_paths(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The fields a form reads back: a new record's, or `record`'s."""
        readonly = set(self.readonly_paths(request, record))
        return tuple(
            path for path in self.form_fields(request, record) if path not in readonly
        )

    def inline_readonly(
        self, inline: Inline, request: Any = None, record: Any = None
    ) -> set[str]:
        """The child's paths this view locks, named from here as items.unit_price."""
        prefix = f"{inline.name}."
        return {
            path.removeprefix(prefix)
            for path in self.readonly_paths(request, record)
            if path.startswith(prefix)
        }

    def load_paths(self, request: Any = None, record: Any = None) -> tuple[str, ...]:
        """Everything a record page shows, so it can be loaded in one go."""
        paths = list(self.form_fields(request, record, page=RequestAction.EDIT))
        for path in self.detail_fields(request, record):
            if path not in paths:
                paths.append(path)
        paths = self.loadable(paths)
        # Every inline the view has, since get_inlines may answer differently
        # once it is given the record, which is not loaded yet.
        for name, child in self._inline_views.items():
            paths.append(name)
            # Every link a row reads, on the form or the record page, a Link
            # to a column of a linked record among them.
            shown = [
                path
                for path in child._settings.candidates
                if not all(
                    child._pages.excluded_from(page, path) for page in _ROW_PAGES
                )
            ]
            for path in child._pages.loadable(shown):
                if path.split(".", 1)[0] in child._schema.relations:
                    paths.append(f"{name}.{path}")
        return tuple(paths)

    def loadable(self, paths: Sequence[str]) -> list[str]:
        """The paths a query can load, with what computed fields read added.

        A computed field is worked out in Python, so it is not loaded, but
        whatever it reads is, or a list of 25 would cost 25 queries.
        """
        wanted: list[str] = []
        for path in paths:
            item = self._fields.field_for(path)
            if item.stored:
                wanted.append(path)
                continue
            for needed in self._fields.needs_of(item):
                if needed not in wanted:
                    wanted.append(needed)
        return wanted

    def readable_paths(self, request: Any = None) -> tuple[str, ...]:
        """Every path this user may read on some page of the view.

        The create form shows no record's values, so only the edit form counts.
        """
        paths = list(self.column_choices(request))
        for path in (
            *self.detail_fields(request),
            *self.form_fields(request, page=RequestAction.EDIT),
        ):
            if path not in paths:
                paths.append(path)
        return tuple(paths)

    def listed(self) -> tuple[str, ...]:
        """The columns the list can show, hidden or not."""
        return tuple(
            path
            for path in self._settings.candidates
            if not self._fields.field_for(path).form_only
            and not self.excluded_from(RequestAction.LIST, path)
        )

    def editable(self, path: str) -> bool:
        """Whether a path can be an input in a form."""
        if "." in path or path in self._settings.filled_keys:
            return False
        item = self._fields.field_for(path)
        return item.stored or item.form_only

    def excluded_from(self, page: RequestAction, path: str) -> bool:
        """Whether the field is left off a page, by its flag or the view's list."""
        if path in self._settings.excluded[page]:
            return True
        item = self._fields.field_for(path)
        return {
            RequestAction.LIST: item.exclude_from_list,
            RequestAction.DETAIL: item.exclude_from_detail,
            RequestAction.CREATE: item.exclude_from_create,
            RequestAction.EDIT: item.exclude_from_edit,
            RequestAction.EXPORT: item.exclude_from_export,
        }[page]
