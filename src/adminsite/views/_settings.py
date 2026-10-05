"""A view's settings, read into the paths the rest of adminsite works with."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from adminsite._orm.repository import SQLAlchemyRepository
from adminsite.columns import (
    ColumnReference,
    Descending,
    describe,
    is_column,
    path_of,
    sort_of,
)
from adminsite.exceptions import AdminSiteError
from adminsite.fields import BaseField, ComputedField, Field
from adminsite.filters.sql import SQLFilter, filter_for
from adminsite.inspector import SQLAlchemyInspector
from adminsite.permissions import RequestAction
from adminsite.query import Sort
from adminsite.schema import ModelSchema, RelationDirection
from adminsite.views._checks import (
    Takes,
    check_excluded,
    check_path,
    check_placed,
    check_title,
)
from adminsite.views.layout import Placed, read_layout

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = [
    "SettingsReader",
    "default_paths",
]

T = TypeVar("T")


class SettingsReader:
    """A view's settings as the paths they name, each checked.

    They are read when the view is built, so a mistake stops the admin
    starting. An answer from one of the view's `get_` methods goes
    through the same checks when a page asks for it.
    """

    def __init__(
        self,
        view: "ModelView[Any]",
        schema: ModelSchema,
        inspector: SQLAlchemyInspector,
    ) -> None:
        self._name = type(view).__name__
        self._model: type[Any] = view.model
        self._inspector = inspector
        # The fields `fields` gives, by path: one with options, or one that
        # is no column. A field is completed from its column when first
        # asked for.
        self.overrides: dict[str, BaseField] = {}
        # The paths `fields` places, in order.
        self.placed = self._read_fields(view.fields)
        exclusions: dict[RequestAction, Sequence[ColumnReference]] = {
            RequestAction.LIST: view.exclude_fields_from_list,
            RequestAction.DETAIL: view.exclude_fields_from_detail,
            RequestAction.CREATE: view.exclude_fields_from_create,
            RequestAction.EDIT: view.exclude_fields_from_edit,
            RequestAction.EXPORT: view.exclude_fields_from_export,
        }
        self.excluded = {
            page: self.paths(f"exclude_fields_from_{page}", entries, takes="fields")
            for page, entries in exclusions.items()
        }
        # The fields a page picks from: the view's, or every column.
        self.candidates = self.placed or default_paths(schema)
        check_excluded(self._name, self.excluded, self.candidates, schema)
        self.searchable_fields = self.paths(
            "searchable_fields", view.searchable_fields, takes="columns"
        )
        # None when the view leaves it out, which sorts by every column.
        self.sortable_fields = (
            None
            if view.sortable_fields is None
            else self.paths("sortable_fields", view.sortable_fields, takes="sortable")
        )
        self.fields_default_sort = self.sorts(
            "fields_default_sort", view.fields_default_sort
        )
        self.deferred_fields = self.paths(
            "deferred_fields", view.deferred_fields, takes="own columns"
        )
        self.record_title = view.record_title
        if self.record_title:
            check_title(
                f"{self._name}.record_title: {describe(self.record_title)}",
                self.record_title,
                self._model,
                inspector,
            )
        # A key the database or the model fills in, such as an autoincrement
        # id or a uuid4 default, is never typed into a form. A key people
        # choose, such as a code, is, until the record exists.
        self.filled_keys = frozenset(
            name
            for name in schema.primary_key
            if name in schema.fields and schema.fields[name].has_default
        )
        self.list_filters = self.filters("list_filters", view.list_filters)
        # The fields whose cells in the list change their value in place,
        # checked against the list and the edit form once both are known.
        self.inline_editable_fields = self.paths(
            "inline_editable_fields", view.inline_editable_fields, takes="fields"
        )
        # Where the forms and the record page put each field.
        self.form_layout = self._read_layout(view.form_layout)

    def entries(self, setting: str, entries: Sequence[T]) -> Sequence[T]:
        """A setting's entries, refusing one string where a list belongs.

        A string is a sequence of letters, so `searchable_fields = "note"`
        would otherwise search the columns n, o, t and e.
        """
        if isinstance(entries, str):
            raise AdminSiteError(
                f"{self._name}.{setting} is the string {describe(entries)}. "
                f"Make it a list: {setting} = [{describe(entries)}]."
            )
        return entries

    def converted(
        self,
        setting: str,
        entry: Any,
        convert: Callable[[Any, type[Any]], T],
        model: type[Any] | None = None,
    ) -> T:
        """One entry of a setting converted, naming the setting if it fails.

        It starts from the view's model, or from `model` for a setting about
        related records, such as an inline's fields.
        """
        try:
            return convert(entry, model or self._model)
        except AdminSiteError as error:
            raise AdminSiteError(f"{self._name}.{setting}: {error}") from None

    def paths(
        self,
        setting: str,
        entries: Sequence[ColumnReference],
        model: type[Any] | None = None,
        *,
        takes: Takes = "paths",
    ) -> tuple[str, ...]:
        """The paths a setting names, such as `customer.email`, each checked."""
        paths = []
        for entry in self.entries(setting, entries):
            path = self.converted(setting, entry, path_of, model)
            self.check(setting, path, model or self._model, takes)
            paths.append(path)
        return tuple(paths)

    def sorts(
        self, setting: str, entries: Sequence[ColumnReference | Descending]
    ) -> tuple[Sort, ...]:
        """The sorts a setting asks for, in order."""
        sorts = []
        for entry in self.entries(setting, entries):
            sort = self.converted(setting, entry, sort_of)
            self.check(setting, sort.path, self._model, "sortable")
            sorts.append(sort)
        return tuple(sorts)

    def filters(
        self, setting: str, entries: Sequence[ColumnReference | SQLFilter[Any]]
    ) -> tuple[SQLFilter[Any], ...]:
        """The filters a setting names: a column's own, or one given whole."""
        repository = SQLAlchemyRepository(self._model, self._inspector)
        built: list[SQLFilter[Any]] = []
        for item in self.entries(setting, entries):
            if isinstance(item, SQLFilter):
                built.append(item)
            elif is_column(item):
                path = self.converted(setting, item, path_of)
                self.check(setting, path, self._model, "paths")
                built.append(filter_for(repository, path))
            else:
                raise AdminSiteError(
                    f"{self._name}.{setting} takes columns or "
                    f"SQLFilter instances, not {type(item).__name__}."
                )
        return tuple(built)

    def check(self, setting: str, path: str, model: type[Any], takes: Takes) -> None:
        """Refuse a path the setting cannot take, as check_path says."""
        own = self.own_fields() if model is self._model else {}
        check_path(
            self._name,
            setting,
            path,
            model,
            takes,
            own=own,
            inspector=self._inspector,
        )

    def own_fields(self) -> dict[str, BaseField]:
        """The fields in `fields` that are no column, such as a computed one."""
        return {
            path: item
            for path, item in self.overrides.items()
            if not isinstance(item, Field) or item.form_only
        }

    def _read_layout(self, entries: Sequence[Any]) -> tuple[Placed, ...]:
        """`form_layout` read into parts, each field it places checked once."""
        placed: set[str] = set()

        def placed_path(entry: Any, where: str) -> str:
            path = self.converted(where, entry, path_of)
            self.check(where, path, self._model, "fields")
            check_placed(self._name, where, path, self.candidates, placed)
            placed.add(path)
            return path

        return read_layout(entries, placed_path, owner=self._name)

    def _read_fields(self, fields: Sequence[Any]) -> tuple[str, ...]:
        """The paths `fields` places, in order, keeping the fields it sets.

        A field is completed from its column when it is first asked for. A
        path placed twice keeps its first place.
        """
        placed: dict[str, None] = {}
        # The columns named, checked once the view's own fields are known,
        # so a name may come before the field it refers to.
        named: dict[str, Takes] = {}
        for index, entry in enumerate(self.entries("fields", fields)):
            if isinstance(entry, Field):
                path = self.converted("fields", entry.column, path_of)
                self.overrides[path] = entry
                if not entry.form_only:
                    named[path] = "paths"
            elif isinstance(entry, BaseField):
                path = entry.name
                self.overrides[path] = entry
                if isinstance(entry, ComputedField):
                    self.paths(f"fields[{index}].needs", entry.needs)
            else:
                path = self.converted("fields", entry, path_of)
                named.setdefault(path, "fields")
            placed.setdefault(path)
        for path, takes in named.items():
            self.check("fields", path, self._model, takes)
        return tuple(placed)


def default_paths(schema: ModelSchema) -> tuple[str, ...]:
    """Every column of a model in order, with a foreign key shown as its link.

    A form offering `customer_id` as a number box is no use to anyone,
    so the key column is swapped for the relationship it belongs to,
    which gets a proper picker and shows the customer's name.
    """
    links = {
        column: relation.name
        for relation in schema.relations.values()
        # Only a link that holds the key here. One held by the other model,
        # such as a person's passport, names this model's own key.
        if relation.direction is RelationDirection.MANY_TO_ONE
        for column in relation.local_columns
    }
    return tuple(dict.fromkeys(links.get(name, name) for name in schema.fields))
