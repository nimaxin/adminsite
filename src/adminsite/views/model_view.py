import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Literal,
    TypeAlias,
    TypeGuard,
    TypeVar,
    get_args,
    get_origin,
)
from uuid import uuid4

from markupsafe import Markup
from sqlalchemy import ColumnElement
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import class_mapper
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from adminsite.audit import AuditStore
    from adminsite.views.registry import ViewRegistry

from adminsite.actions.action import Action, action_of
from adminsite.actions.parameters import (
    ASYNC_SESSION,
    ActionCall,
    async_session_refused,
    read_call,
)
from adminsite.actions.selection import Selection
from adminsite.audit.actor import actor_of
from adminsite.audit.entry import AuditEntry, AuditEvent, Change, diff
from adminsite.audit.inputs import HIDDEN, looks_secret, recorded_inputs
from adminsite.audit.store import record_or_warn
from adminsite.backends.sqlalchemy.filters import (
    SQLFilter,
    SQLFilterContext,
    filter_for,
)
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import (
    Scope,
    SQLAlchemyRepository,
    Statement,
)
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.columns import (
    ColumnReference,
    Descending,
    describe,
    is_column,
    path_of,
    sort_of,
)
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    IntegrityError,
    InvalidPathError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
    UnknownFieldError,
)
from adminsite.fields import (
    BaseField,
    ComputedField,
    EnumField,
    Field,
    FieldRegistry,
    JSONField,
    RelationField,
    default_registry,
)
from adminsite.fields.computed import LOADED
from adminsite.fields.documents import DocumentError
from adminsite.fields.files import UNCHANGED, FileField, NewFile, UploadField
from adminsite.filters import FilterOption, FilterValue
from adminsite.i18n import gettext as _
from adminsite.messages import Message
from adminsite.query import CountMode, Page, Pagination, QuerySpec, Sort
from adminsite.security import Permission, RequestAction, permission_name
from adminsite.text import (
    RecordValues,
    names_itself,
    pluralize,
    snake_case,
    template_names,
)
from adminsite.views.inline import Inline, InlineRow
from adminsite.views.naming import name_all_linked, name_linked
from adminsite.views.writing import (
    DeleteContext,
    FormData,
    FormResult,
    SaveContext,
    SaveValues,
    stored_values,
)

__all__ = [
    "BULK_DELETE_LIMIT",
    "DELETE_ACTION",
    "ModelView",
    "view_class",
]

# The name of the built-in action that deletes the chosen rows.
DELETE_ACTION = "delete_selected"

# The most records one Delete removes, each loaded and run through the
# hooks inside a single transaction.
BULK_DELETE_LIMIT = 1000

# Settings and methods under the names other admins give them, or adminsite
# did before 0.1.0a10, with the name adminsite uses. Python would take one as a
# new attribute that adminsite never reads, so a view setting one is refused.
_RENAMED = {
    "list_filter": "list_filters",
    "get_filters": "get_list_filters",
    "page_sizes": "page_size_options",
    "bulk_delete": "can_delete_selected",
    "search_fields": "searchable_fields",
    "get_search_fields": "get_searchable_fields",
    "ordering": "fields_default_sort",
    "get_ordering": "get_fields_default_sort",
    "display_template": "record_title",
    "title_of": "get_record_title",
    "can_detail": "can_view_detail",
    "form_values": "form_only_values",
}

# Settings and methods adminsite has nothing by that name for, as other
# admins do, each with what to write instead.
_REPLACED = {
    "list_display": (
        "List the view's fields in fields, and leave one off the list with "
        "exclude_fields_from_list."
    ),
    "list_columns": "Give each of those fields hidden_in_list=True in fields.",
    "form_fields": (
        "List the view's fields in fields, and leave one off the forms with "
        "exclude_fields_from_create and exclude_fields_from_edit."
    ),
    "detail_fields": (
        "List the view's fields in fields, and leave one off the record page "
        "with exclude_fields_from_detail."
    ),
    "exclude": "List the fields to show in fields, leaving those out.",
    "readonly_fields": (
        "Give each of those fields read_only=True in fields, or name them in "
        "get_readonly_fields."
    ),
    "get_list_display": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
    "get_form_fields": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
    "get_detail_fields": (
        "Decide who sees a field with can_access_field(request, field, action)."
    ),
}

# The model a view shows. Not bound to DeclarativeBase: a SQLModel model is
# mapped without it. The admin refuses a class that is not mapped.
M = TypeVar("M")

T = TypeVar("T")

# What a setting may name: any field of the view, a column or relationship
# of the model, a column, a column the list can be sorted by, or a column of
# the model itself.
_Takes: TypeAlias = Literal["fields", "paths", "columns", "sortable", "own columns"]


class ModelView(Generic[M]):
    """How one model appears in the admin: `class OrderView(ModelView[Order])`.

    ```python
    class OrderView(ModelView[Order]):
        fields = [Order.id, Order.customer, Order.status, Order.total]
        searchable_fields = [Order.id, Link(Order.customer, Customer.name)]
        fields_default_sort = [Descending(Order.created_at)]
    ```

    `fields` lists every field once, in order, for every page. The other
    class attributes describe the list and the forms, and the `get_`
    methods and `can_access_field` answer per request, for when the answer
    depends on who is asking. A view with no settings needs no class:
    register `ModelView[Tag]`.
    """

    model: type[M]
    # The type parameter that stands for the model in a view that is generic
    # itself, such as class ShopView(ModelView[M]), so a class written as
    # ShopView[Order] can find its model.
    _model_parameter: ClassVar[object] = None

    name: str = ""
    label: str = ""
    label_plural: str = ""
    group: str = ""
    icon: str = ""
    # How a record is named in headings, links to it and the history, such
    # as "Order #{id}". Left empty, the model's own __str__, or the view's
    # label and the record's key.
    record_title: str = ""

    # Every field of the view, in order, for every page. Columns are named by
    # attribute, Order.total, by Link for a column of a related model, or by
    # name as a string, "total" or "customer.email"; a field sets options.
    # Left empty, the view shows every column the model has. A field's value
    # type differs from one to the next, hence Field[Any].
    fields: Sequence[ColumnReference | Field[Any] | ComputedField[M, Any]] = ()
    # Fields left off one page, as the exclude_from_ flags on a field do.
    exclude_fields_from_list: Sequence[ColumnReference] = ()
    exclude_fields_from_detail: Sequence[ColumnReference] = ()
    exclude_fields_from_create: Sequence[ColumnReference] = ()
    exclude_fields_from_edit: Sequence[ColumnReference] = ()
    exclude_fields_from_export: Sequence[ColumnReference] = ()
    searchable_fields: Sequence[ColumnReference] = ()
    # The columns the list can be sorted by. Left empty, every stored column.
    sortable_fields: Sequence[ColumnReference] = ()
    # Descending(Order.created_at), or "-created_at", sorts newest first.
    fields_default_sort: Sequence[ColumnReference | Descending] = ()

    # The filters beside the list: a column, which gets the filter that
    # suits its type, or a filter of your own.
    list_filters: Sequence[ColumnReference | SQLFilter[M]] = ()
    page_size: int = 25
    # The sizes people may switch between. Empty leaves the size fixed.
    page_size_options: Sequence[int] = ()
    count_mode: CountMode = CountMode.EXACT
    # Whether the command palette looks through this view's records.
    global_search: bool = True
    pagination: Pagination = Pagination.OFFSET

    # Columns the list does not read, such as a large JSON payload, left out
    # of its query. The record page and the form load them as usual.
    deferred_fields: Sequence[ColumnReference] = ()

    # Child records edited inside this model's form.
    inlines: Sequence[Inline] = ()

    can_create: bool = True
    can_view_detail: bool = True
    can_export: bool = True
    can_edit: bool = True
    can_delete: bool = True
    # Whether the chosen rows can be deleted together, from the bar that
    # rises when rows are ticked. can_delete has to allow it too.
    can_delete_selected: bool = True
    # Importing is off until you switch it on: it writes many records at once.
    can_import: bool = False
    import_limit: int = 10_000

    # The other views of the same admin, set when the view is registered,
    # so a link can be checked against the view of the model it points at.
    _views: "ViewRegistry | None" = None

    # False leaves the view out of the sidebar, the command palette's pages
    # and the overview's counts. Its pages, links and pickers stay as they
    # are, for a view whose records are only opened from other records.
    in_sidebar: bool = True

    # Set by the admin when auditing is switched on.
    _audit_log: "AuditStore | None" = None
    # Set by the admin when that log lives in the admin's own database, so a
    # change and its entries are saved in one transaction.
    _audit_with_changes: bool = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        model = kwargs.pop("model", None)
        if model is not None:
            named = getattr(model, "__name__", "YourModel")
            raise AdminSiteError(
                f"Name the model of {cls.__name__} as its type argument: "
                f"class {cls.__name__}(ModelView[{named}])."
            )
        super().__init_subclass__(**kwargs)
        # The model is the type argument of the ModelView this class is built
        # on, or of a view that is generic itself, such as ShopView[Order].
        for base in cls.__dict__.get("__orig_bases__", ()):
            origin = get_origin(base)
            if not (isinstance(origin, type) and issubclass(origin, ModelView)):
                continue
            parameters: tuple[object, ...] = getattr(origin, "__parameters__", ())
            if origin is ModelView:
                position = 0
            elif origin._model_parameter in parameters:
                position = parameters.index(origin._model_parameter)
            else:
                continue
            argument = get_args(base)[position]
            if isinstance(argument, TypeVar):
                cls._model_parameter = argument
            elif isinstance(argument, type) and argument is not Any:
                cls.model = argument

    def __init__(
        self,
        inspector: SQLAlchemyInspector | None = None,
        registry: FieldRegistry | None = None,
    ) -> None:
        if not hasattr(self, "model"):
            raise AdminSiteError(
                f"{type(self).__name__} needs a model: "
                f"class {type(self).__name__}(ModelView[YourModel])."
            )
        self._refuse_old_names()
        self._inspector = inspector or SQLAlchemyInspector()
        self._registry = registry or default_registry
        try:
            self._schema = self._inspector.inspect(self.model)
        except NotAModelError:
            view = type(self).__name__
            raise AdminSiteError(
                f"{view} shows {getattr(self.model, '__name__', self.model)}, "
                "which is not a mapped SQLAlchemy model. Name a mapped class: "
                f"class {view}(ModelView[YourModel])."
            ) from None

        self.name = self.name or pluralize(snake_case(self.model.__name__))
        self.label = self.label or self._schema.label
        self.label_plural = self.label_plural or self._schema.label_plural

        # The settings as the paths the rest of adminsite works with.
        self._overrides: dict[str, BaseField] = {}
        self._placed = self._read_fields()
        exclusions: dict[RequestAction, Sequence[ColumnReference]] = {
            RequestAction.LIST: self.exclude_fields_from_list,
            RequestAction.DETAIL: self.exclude_fields_from_detail,
            RequestAction.CREATE: self.exclude_fields_from_create,
            RequestAction.EDIT: self.exclude_fields_from_edit,
            RequestAction.EXPORT: self.exclude_fields_from_export,
        }
        self._excluded = {
            page: self._paths(f"exclude_fields_from_{page}", entries, takes="fields")
            for page, entries in exclusions.items()
        }
        self._search_fields = self._paths(
            "searchable_fields", self.searchable_fields, takes="columns"
        )
        self._sortable_fields = self._paths(
            "sortable_fields", self.sortable_fields, takes="sortable"
        )
        self._ordering = self._sorts("fields_default_sort", self.fields_default_sort)
        self._deferred_fields = self._paths(
            "deferred_fields", self.deferred_fields, takes="own columns"
        )
        self._record_title = self.record_title
        if self._record_title:
            self._check_title(
                f"{type(self).__name__}.record_title: {describe(self._record_title)}",
                self._record_title,
                self.model,
            )
        # A key the database numbers, or one the parent record's key fills
        # in, is never typed into a form. A key people choose, such as a
        # code, is.
        self._filled_keys = frozenset(
            name
            for name in self._schema.primary_key
            if name in self._schema.fields
            and (
                self._schema.fields[name].autoincrement
                or self._schema.fields[name].foreign_key
            )
        )

        self._actions = self._collect_actions()
        self._inline_views = {
            inline.name: self._build_inline_view(inline, f"inlines[{index}]")
            for index, inline in enumerate(self._entries("inlines", self.inlines))
        }
        self._filters: tuple[SQLFilter[Any], ...] = self._built_filters(
            "list_filters", self.list_filters
        )
        self._repository = SQLAlchemyRepository(
            self.model, self._inspector, self._filters
        )
        self._fields: dict[str, BaseField] = {}
        # Built now, so a mistake such as a tone for a value the field does
        # not have stops the admin starting rather than the page that shows it.
        for path in self._placed:
            try:
                self._field_for(path)
                self._check_list_flags(path)
            except AdminSiteError as error:
                raise AdminSiteError(f"{type(self).__name__}.fields: {error}") from None

    def _refuse_old_names(self) -> None:
        """Refuse a setting or method written under the name it had before."""
        view = type(self).__name__
        for owner in type(self).__mro__:
            if owner is ModelView:
                return
            written = vars(owner)
            for old, new in _RENAMED.items():
                if old in written:
                    verb = "defines" if callable(written[old]) else "sets"
                    raise AdminSiteError(
                        f"{view} {verb} {old}, which adminsite calls {new}. "
                        f"Rename it to {new}."
                    )
            for old, instead in _REPLACED.items():
                if old not in written:
                    continue
                if callable(written[old]):
                    said = f"{view} defines {old}, a method adminsite does not call."
                else:
                    said = f"{view} sets {old}, a setting adminsite does not have."
                raise AdminSiteError(f"{said} {instead}")
            replaced = written.get(DELETE_ACTION)
            if replaced is not None and action_of(replaced) is None:
                raise AdminSiteError(
                    f"{view}.{DELETE_ACTION} does not replace the built-in delete "
                    "of the chosen rows. Set can_delete_selected = False and add "
                    "an action of your own."
                )

    # Reading the configuration. Override these when the answer depends on
    # the request, for example to hide a column from some people.

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        """Whether this user sees the field on this page.

        Every field is shown to everyone by default. Answer False to keep one
        from someone, such as a cost price from staff who are not managers:
        it leaves the page, the export and the API, and no form reads it
        back. `field.name` is the path it shows, such as "total" or
        "customer.email".
        """
        return True

    def _accessible(
        self, request: Any, paths: Sequence[str], action: RequestAction
    ) -> tuple[str, ...]:
        """The paths among these whose fields this user sees on this page."""
        return tuple(
            path
            for path in paths
            if self.can_access_field(request, self._field_for(path), action)
        )

    def _list_fields(self, request: Any = None) -> tuple[str, ...]:
        """The columns the list shows, for this user."""
        shown = [
            path for path in self._listed() if not self._field_for(path).hidden_in_list
        ]
        return self._accessible(request, shown, RequestAction.LIST)

    def _page_sizes(self, request: Any = None) -> tuple[int, ...]:
        """The page sizes on offer, the view's own size among them."""
        if not self.page_size_options:
            return ()
        return tuple(sorted({*self.page_size_options, self.page_size}))

    def _pick_page_size(self, wanted: int | None, request: Any = None) -> int:
        """The rows per page for what someone picked.

        Only a size on offer counts, so nobody can ask for a million rows
        by editing the URL.
        """
        offered = self._page_sizes(request)
        if wanted in offered:
            return int(wanted or self.page_size)
        return self.page_size

    def _column_choices(self, request: Any = None) -> tuple[str, ...]:
        """The columns the picker offers: the list's own, then the hidden ones."""
        hidden = [
            path for path in self._listed() if self._field_for(path).hidden_in_list
        ]
        return self._list_fields(request) + self._accessible(
            request, hidden, RequestAction.LIST
        )

    def _pick_columns(
        self, picked: Sequence[str], request: Any = None
    ) -> tuple[str, ...]:
        """The columns to show for what someone picked.

        Only columns on offer count, so a column hidden from this user cannot
        be brought back by editing the URL. Picking none gives the default.
        """
        wanted = set(picked)
        chosen = tuple(path for path in self._column_choices(request) if path in wanted)
        return chosen or self._list_fields(request)

    def get_searchable_fields(self, request: Request) -> Sequence[ColumnReference]:
        """The columns the search box looks in, for this user."""
        return self.searchable_fields

    def _search_paths(self, request: Any) -> tuple[str, ...]:
        """The paths the search box looks in, checked like the setting."""
        named = self.get_searchable_fields(request)
        if named is self.searchable_fields:
            return self._search_fields
        return self._paths("get_searchable_fields", named, takes="columns")

    def search_condition(
        self, term: str, *, request: Request
    ) -> ColumnElement[bool] | None:
        """The condition the search box matches with, or None for the usual one.

        The usual one looks for the term inside every search field, which a
        large table cannot answer from an index. Return a condition of your
        own, such as an exact match on a normalised phone number, and the
        list, its count, the export, "select all matching", the command
        palette and pickers all use it. Return None to fall back.
        """
        return None

    def get_list_filters(
        self, request: Request
    ) -> Sequence[ColumnReference | SQLFilter[M]]:
        """The filters offered beside the list, for this user."""
        return self.list_filters

    def _list_filters(self, request: Any) -> tuple[SQLFilter[Any], ...]:
        """The filters offered beside the list, each built and checked."""
        named = self.get_list_filters(request)
        if named is self.list_filters:
            return self._filters
        return self._built_filters("get_list_filters", named)

    def get_fields_default_sort(
        self, request: Request
    ) -> Sequence[ColumnReference | Descending]:
        """The order the list starts in, for this user."""
        return self.fields_default_sort

    def _default_sort(self, request: Any) -> tuple[Sort, ...]:
        """The order the list starts in, checked like the setting."""
        named = self.get_fields_default_sort(request)
        if named is self.fields_default_sort:
            return self._ordering
        return self._sorts("get_fields_default_sort", named)

    def _form_fields(self, request: Any = None, record: Any = None) -> tuple[str, ...]:
        """The fields the form shows, in order: a new record's, or `record`'s.

        A column of a related model and a computed field are shown, never
        edited, so they stay off forms, as does a key nobody types in.
        """
        page = RequestAction.CREATE if record is None else RequestAction.EDIT
        placed = [
            path
            for path in self._candidates()
            if self._editable(path) and not self._excluded_from(page, path)
        ]
        return self._accessible(request, placed, page)

    def _detail_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """What the record page shows, for this user.

        A form-only field, such as a password to set, has nothing to show.
        """
        shown = [
            path
            for path in self._candidates()
            if not self._field_for(path).form_only
            and not self._excluded_from(RequestAction.DETAIL, path)
        ]
        return self._accessible(request, shown, RequestAction.DETAIL)

    def _exported(self, paths: Sequence[str], request: Any = None) -> tuple[str, ...]:
        """The columns of a list that go into its export."""
        return self._accessible(
            request,
            [
                path
                for path in paths
                if not self._excluded_from(RequestAction.EXPORT, path)
            ],
            RequestAction.EXPORT,
        )

    def get_deferred_fields(self, request: Request) -> Sequence[ColumnReference]:
        """The columns the list leaves out of its query, for this user."""
        return self.deferred_fields

    def get_readonly_fields(
        self, request: Request, record: M | None
    ) -> Sequence[ColumnReference]:
        """Fields shown on this record's form but not editable there.

        A field with `read_only=True` is never editable. Answer with more
        for one record or one user, such as the customer of a shipped order;
        `record` is None on the form for a new record.
        """
        return []

    def _readonly_paths(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The paths shown but not editable, named for the record or by themselves.

        A primary key is readonly by its nature, but a form that names one
        means to set it, so a key stays editable unless it is named.
        """
        named = self._paths(
            "get_readonly_fields",
            self.get_readonly_fields(request, record),
            takes="fields",
        )
        keys = set(self._schema.primary_key)
        return named + tuple(
            path
            for path in self._form_fields(request, record)
            if path not in named
            and path not in keys
            and self._field_for(path).read_only
        )

    def get_inlines(self, request: Request, record: M | None) -> Sequence[Inline]:
        """The child records edited inside the form, for this user and record.

        `record` is None on the form for a new record. Each inline returned
        has to be one of `inlines`, whose rows are checked when the admin
        starts.
        """
        return self.inlines

    def _inline_view(self, name: str) -> "ModelView[Any]":
        """The view that reads and writes one inline's children."""
        try:
            return self._inline_views[name]
        except KeyError:
            raise AdminSiteError(
                f"{type(self).__name__} has no inline called {name!r}."
            ) from None

    def _load_paths(self, request: Any = None, record: Any = None) -> tuple[str, ...]:
        """Everything a record page shows, so it can be loaded in one go."""
        paths = list(self._form_fields(request, record))
        for path in self._detail_fields(request, record):
            if path not in paths:
                paths.append(path)
        paths = self._loadable(paths)
        for inline in self.get_inlines(request, record):
            paths.append(inline.name)
            child = self._inline_view(inline.name)
            for path in child._form_fields(request):
                if path in child._schema.relations:
                    paths.append(f"{inline.name}.{path}")
        return tuple(paths)

    def _loadable(self, paths: Sequence[str]) -> list[str]:
        """The paths a query can load, with what computed fields read added.

        A computed field is worked out in Python, so it is not loaded, but
        whatever it reads is, or a list of 25 would cost 25 queries.
        """
        wanted: list[str] = []
        for path in paths:
            item = self._field_for(path)
            if item.stored:
                wanted.append(path)
                continue
            for needed in self._needs_of(item):
                if needed not in wanted:
                    wanted.append(needed)
        return wanted

    def _needs_of(self, item: BaseField) -> list[str]:
        """The paths a computed field reads, checked when the view is built."""
        if not isinstance(item, ComputedField):
            return list(getattr(item, "needs", ()))
        return [path_of(needed, self.model) for needed in item.needs]

    def _sortable(self, path: str) -> bool:
        """Whether a list can be sorted by this column."""
        if self._sortable_fields and path not in self._sortable_fields:
            return False
        return self._field_for(path).stored

    def _readable_paths(self, request: Any = None) -> tuple[str, ...]:
        """Every path this user may read on some page of the view."""
        paths = list(self._column_choices(request))
        for path in (*self._detail_fields(request), *self._form_fields(request)):
            if path not in paths:
                paths.append(path)
        return tuple(paths)

    def _build_inline_view(self, inline: Inline, setting: str) -> "ModelView[Any]":
        self._converted(setting, inline.relation, path_of)
        self._check_path(setting, inline.name, self.model, "paths")
        relation = self._schema.relation_named(inline.name)
        if not relation.collection:
            raise AdminSiteError(
                f"{type(self).__name__}.inlines names {inline.name!r}, which "
                "holds one record. An inline needs a relationship holding many."
            )
        if inline.record_title:
            self._check_title(
                f"{type(self).__name__}.{setting}.record_title: "
                f"{describe(inline.record_title)}",
                inline.record_title,
                relation.target,
            )
        # The link back to the parent is set by the relationship itself, so
        # it never appears as an input in the child rows.
        target = self._inspector.inspect(relation.target)
        back_links = [
            name
            for name, found in target.relations.items()
            if found.target is self.model and not found.collection
        ]
        # Checked here, so a mistake names the parent's setting. The child view
        # takes the entries as they are, fields with their options included.
        entries = self._entries(f"{setting}.fields", inline.fields)
        self._paths(
            f"{setting}.fields",
            [
                entry.column if isinstance(entry, Field) else entry
                for entry in entries
                if not (isinstance(entry, Field) and entry.form_only)
            ],
            relation.target,
        )
        namespace: dict[str, Any] = {
            "model": relation.target,
            "name": f"{self.name}__{inline.name}",
            "fields": list(entries),
            "exclude_fields_from_create": back_links,
            "exclude_fields_from_edit": back_links,
            "record_title": inline.record_title,
        }
        child_class = type(f"{relation.target.__name__}Inline", (ModelView,), namespace)
        built: ModelView[Any] = child_class(self._inspector, self._registry)
        return built

    def _candidates(self) -> tuple[str, ...]:
        """The fields a page picks from: the view's, or every column."""
        return self._placed or self._default_paths()

    def _listed(self) -> tuple[str, ...]:
        """The columns the list can show, hidden or not."""
        return tuple(
            path
            for path in self._candidates()
            if not self._field_for(path).form_only
            and not self._excluded_from(RequestAction.LIST, path)
        )

    def _editable(self, path: str) -> bool:
        """Whether a path can be an input in a form."""
        if "." in path or path in self._filled_keys:
            return False
        item = self._field_for(path)
        return item.stored or item.form_only

    def _excluded_from(self, page: RequestAction, path: str) -> bool:
        """Whether the field is left off a page, by its flag or the view's list."""
        if path in self._excluded[page]:
            return True
        item = self._field_for(path)
        return {
            RequestAction.LIST: item.exclude_from_list,
            RequestAction.DETAIL: item.exclude_from_detail,
            RequestAction.CREATE: item.exclude_from_create,
            RequestAction.EDIT: item.exclude_from_edit,
            RequestAction.EXPORT: item.exclude_from_export,
        }[page]

    def _default_paths(self) -> tuple[str, ...]:
        """Every column in order, with a foreign key shown as its link.

        A form offering `customer_id` as a number box is no use to anyone,
        so the key column is swapped for the relationship it belongs to,
        which gets a proper picker and shows the customer's name.
        """
        links = {
            column: relation.name
            for relation in self._schema.relations.values()
            if not relation.collection
            for column in relation.local_columns
        }
        return tuple(
            dict.fromkeys(links.get(name, name) for name in self._schema.fields)
        )

    # Turning settings into paths.

    def _entries(self, setting: str, entries: Sequence[T]) -> Sequence[T]:
        """A setting's entries, refusing one string where a list belongs.

        A string is a sequence of letters, so `searchable_fields = "note"`
        would otherwise search the columns n, o, t and e.
        """
        if isinstance(entries, str):
            raise AdminSiteError(
                f"{type(self).__name__}.{setting} is the string {describe(entries)}. "
                f"Make it a list: {setting} = [{describe(entries)}]."
            )
        return entries

    def _converted(
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
            return convert(entry, model or self.model)
        except AdminSiteError as error:
            raise AdminSiteError(f"{type(self).__name__}.{setting}: {error}") from None

    def _paths(
        self,
        setting: str,
        entries: Sequence[ColumnReference],
        model: type[Any] | None = None,
        *,
        takes: _Takes = "paths",
    ) -> tuple[str, ...]:
        """The paths a setting names, such as `customer.email`, each checked."""
        paths = []
        for entry in self._entries(setting, entries):
            path = self._converted(setting, entry, path_of, model)
            self._check_path(setting, path, model or self.model, takes)
            paths.append(path)
        return tuple(paths)

    def _sorts(
        self, setting: str, entries: Sequence[ColumnReference | Descending]
    ) -> tuple[Sort, ...]:
        """The sorts a setting asks for, in order."""
        sorts = []
        for entry in self._entries(setting, entries):
            sort = self._converted(setting, entry, sort_of)
            self._check_path(setting, sort.path, self.model, "sortable")
            sorts.append(sort)
        return tuple(sorts)

    def _check_path(
        self, setting: str, path: str, model: type[Any], takes: _Takes
    ) -> None:
        """Refuse a path the setting cannot take, saying what it can.

        A type checker sees an attribute; only this sees a string, and what
        the setting does with the path, such as sorting through a
        relationship holding many records, which no query can.
        """
        view = type(self).__name__
        own = self._own_fields() if model is self.model else {}
        if path in own:
            if takes == "fields":
                return
            wanted = "columns and relationships" if takes == "paths" else "columns"
            raise AdminSiteError(
                f"{view}.{setting}: {own[path]!r} is a field of the view, not a "
                f"column of {model.__name__}, and {setting} takes {wanted}."
            )
        try:
            resolved = self._inspector.resolve(model, path)
        except UnknownFieldError as error:
            listed = own if takes == "fields" else {}
            raise AdminSiteError(
                f"{view}.{setting}: {self._missing(path, error, listed)}"
            ) from None
        except InvalidPathError as error:
            raise AdminSiteError(f"{view}.{setting}: {error}") from None
        if takes in ("columns", "sortable") and resolved.field is None:
            target = self._inspector.inspect(resolved.relations[-1].target)
            texts = [
                name
                for name, found in target.fields.items()
                if found.python_type is str
            ]
            example = f"{path}.{(texts or list(target.fields))[0]}"
            raise AdminSiteError(
                f"{view}.{setting}: {describe(path)} is a relationship, and "
                f"{setting} takes columns. Name a column of "
                f"{target.model.__name__}, such as {describe(example)}."
            )
        if takes == "sortable" and resolved.crosses_collection:
            raise AdminSiteError(
                f"{view}.{setting}: {describe(path)} goes through a relationship "
                "holding many records, so no list can be sorted by it."
            )
        if takes == "own columns" and (resolved.relations or resolved.field is None):
            raise AdminSiteError(
                f"{view}.{setting}: {describe(path)} is not a column of "
                f"{model.__name__} itself, and {setting} takes the model's own "
                "columns."
            )

    def _missing(
        self, path: str, error: UnknownFieldError, own: Mapping[str, BaseField]
    ) -> str:
        """Say which name does not exist, and list the names that do."""
        schema = self._inspector.inspect(error.model)
        said = (
            f"{error.model.__name__} has no column or relationship "
            f"{describe(error.name)}."
        )
        if error.name != path:
            said = f"{describe(path)}: {said}"
        said += f" Its columns: {', '.join(schema.fields)}."
        if schema.relations:
            said += f" Its relationships: {', '.join(schema.relations)}."
        if own and error.name == path:
            said += f" The view's own fields: {', '.join(own)}."
        return said

    def _check_title(self, where: str, template: str, model: type[Any]) -> None:
        """Refuse a record title that reads an attribute the model does not have.

        A template `str.format` cannot read would fail on every page that
        names a record, and a name the model lacks would show as nothing, so
        both stop the admin.
        """
        try:
            names = template_names(template)
        except ValueError as error:
            raise AdminSiteError(
                f"{where} cannot be read: {error}. Write each column's name in "
                "braces, such as {id}, and double a brace meant as text."
            ) from None
        for name in names:
            if not hasattr(model, name):
                missing = UnknownFieldError(model, name)
                raise AdminSiteError(
                    f"{where} reads {{{name}}}, and {self._missing(name, missing, {})}"
                )

    def _check_link_title(self, item: BaseField, written: str) -> None:
        """Refuse a link's record_title that reads what its model lacks."""
        if isinstance(item, RelationField) and item.record_title:
            self._check_title(
                f"{written}: its record_title {describe(item.record_title)}",
                item.record_title,
                item.related_model,
            )

    def _own_fields(self) -> dict[str, BaseField]:
        """The fields in `fields` that are no column, such as a computed one."""
        return {
            path: item
            for path, item in self._overrides.items()
            if not isinstance(item, Field) or item.form_only
        }

    def _read_fields(self) -> tuple[str, ...]:
        """The paths `fields` places, in order, keeping the fields it sets.

        A field is completed from its column when it is first asked for. A
        path placed twice keeps its first place.
        """
        placed: dict[str, None] = {}
        # The columns named, checked once the view's own fields are known,
        # so a name may come before the field it refers to.
        named: dict[str, _Takes] = {}
        for index, entry in enumerate(self._entries("fields", self.fields)):
            if isinstance(entry, Field):
                path = self._converted("fields", entry.column, path_of)
                self._overrides[path] = entry
                if not entry.form_only:
                    named[path] = "paths"
            elif isinstance(entry, BaseField):
                path = entry.name
                self._overrides[path] = entry
                if isinstance(entry, ComputedField):
                    self._paths(f"fields[{index}].needs", entry.needs)
            else:
                path = self._converted("fields", entry, path_of)
                named.setdefault(path, "fields")
            placed.setdefault(path)
        for path, takes in named.items():
            self._check_path("fields", path, self.model, takes)
        return tuple(placed)

    def _check_list_flags(self, path: str) -> None:
        """Refuse a field both hidden in the list and left off it."""
        item = self._field_for(path)
        if not item.hidden_in_list:
            return
        written = self._overrides.get(path, item)
        if item.exclude_from_list:
            both = (
                f"{written!r} has both hidden_in_list=True and exclude_from_list=True."
            )
        elif path in self._excluded[RequestAction.LIST]:
            both = (
                f"{written!r} has hidden_in_list=True, and exclude_fields_from_list "
                "names it."
            )
        else:
            return
        raise AdminSiteError(
            f"{both} hidden_in_list offers it among the columns people can add to "
            "the list; excluding it keeps it off the list altogether. Keep one."
        )

    # Turning paths into fields and values.

    def _field_for(self, path: str) -> BaseField:
        """The field used to show and edit whatever the path points at."""
        known = self._fields.get(path)
        if known is not None:
            return known
        given = self._overrides.get(path)
        built = self._built_field(path) if given is None else self._completed(given)
        self._fields[path] = built
        return built

    def _completed(self, given: BaseField) -> BaseField:
        """A field from `fields`, with what its column says for options left out.

        A computed or form-only field has no column, so it is used as it is.
        `Field(...)` on its own becomes the field adminsite picks for the
        column, with the options it was given.
        """
        if not isinstance(given, Field) or given.form_only:
            given.check_options()
            return given
        path = path_of(given.column, self.model)
        resolved = self._inspector.resolve(self.model, path)
        completed: BaseField
        # Built under the path, so a column of a related model is named
        # customer.email rather than email.
        if type(given) is Field:
            if resolved.field is not None:
                completed = self._registry.build(
                    replace(resolved.field, name=path), **given.given_options()
                )
            else:
                completed = RelationField.from_relation(
                    replace(resolved.relations[-1], name=path),
                    **given.given_options(),
                )
        elif resolved.field is not None:
            completed = self._registry.fill(given, resolved.field)
        elif isinstance(given, RelationField):
            completed = given.filled_from_relation(resolved.relations[-1])
        else:
            raise AdminSiteError(
                f"{given!r} names a relationship. Show it with RelationField, or "
                "name it in fields without a field."
            )
        completed.check_options()
        self._check_link_title(completed, repr(given))
        return completed

    def _built_field(self, path: str) -> BaseField:
        """The field adminsite works out for a path nobody gave a field for."""
        resolved = self._inspector.resolve(self.model, path)
        if resolved.field is not None:
            return self._registry.build(replace(resolved.field, name=path))
        return RelationField.from_relation(replace(resolved.relations[-1], name=path))

    async def form_only_values(
        self, session: SessionAdapter, record: M | None, *, request: Request
    ) -> Mapping[str, Any]:
        """The values form-only fields start from, by name.

        Nothing by default, so they start empty. Answer with, say, settings
        kept as rows of another table, to edit them as one value; `record`
        is None on the form for a new record.
        """
        return {}

    def _form_only(self, path: str) -> bool:
        """Whether a path is a form-only field of this view."""
        try:
            return self._field_for(path).form_only
        except AdminSiteError:
            return False

    def _masked(self, changes: Mapping[str, Change]) -> dict[str, Change]:
        """Changes as the audit log keeps them: a secret's values as ***.

        A field given `secret=True`, or named like `password_hash` or
        `api_key`, is kept as *** before and after, so a change to it
        still shows, and what it holds never does.
        """
        kept = {}
        for path, (before, after) in changes.items():
            if self._secret(path):
                before = HIDDEN if before not in (None, "") else before
                after = HIDDEN if after not in (None, "") else after
            kept[path] = (before, after)
        return kept

    def _secret(self, path: str) -> bool:
        try:
            chosen = self._field_for(path).secret
        except AdminSiteError:
            chosen = None
        return looks_secret(path.rsplit(".", 1)[-1]) if chosen is None else chosen

    def _label_for(self, path: str) -> str:
        """The column heading for a path.

        A path through a link names the link as well, so `customer.name`
        reads Customer name rather than a bare Name.
        """
        item = self._field_for(path)
        label = item.label
        if item.labelled or "." not in path:
            return label
        resolved = self._inspector.resolve(self.model, path)
        if resolved.field is None:
            return label
        owner = resolved.relations[-1].label
        return f"{owner} {label[:1].lower()}{label[1:]}"

    def _value_at(self, record: Any, path: str) -> Any:
        """Read the value a path points at, following links as it goes."""
        value: Any = record
        for part in path.split("."):
            if value is None:
                return None
            if isinstance(value, list | tuple | set):
                return [getattr(item, part, None) for item in value]
            value = getattr(value, part, None)
        return value

    def _draft_record(self, data: FormData, request: Any = None) -> Any:
        """An unsaved record holding the plain values a form holds so far.

        A JSON field whose schema comes from the record is given this while
        the record is new, so the schema can follow what is chosen in the
        form, such as a setting's key. Only the record's own columns are
        set, from the values that can be read; it is never added to a
        session.
        """
        # Made without the model's own __init__, which may ask for values.
        draft = class_mapper(self.model).class_manager.new_instance()
        for path in self._form_fields(request):
            item = self._field_for(path)
            if "." in path or not item.stored:
                continue
            if isinstance(item, RelationField | FileField | JSONField):
                continue
            raw = data.get(path)
            try:
                if _holds_many(item):
                    value = item.parse_many(_as_list(raw))
                else:
                    value = item.parse(_as_text(raw))
            except FieldValidationError:
                continue
            if value is not None:
                setattr(draft, path, value)
        return draft

    async def _load_values(
        self,
        session: SessionAdapter,
        records: Sequence[Any],
        paths: Sequence[str],
        *,
        request: Any = None,
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
                item = self._field_for(path)
            except AdminSiteError:
                continue
            if not isinstance(item, ComputedField) or item.load is None:
                continue
            found = await item.load(session, records)
            for record in records:
                waiting = vars(record).setdefault(LOADED, {})
                waiting[item.name] = found.get(self._key_value(record), item.default)

    def _key_value(self, record: Any) -> Any:
        """A record's primary key as its columns hold it: a tuple when composite."""
        identity: tuple[Any, ...] = sqlalchemy_inspect(record).identity or ()
        return identity[0] if len(identity) == 1 else tuple(identity)

    def _display(self, record: Any, path: str) -> str:
        """The text shown in a cell."""
        item = self._field_for(path)
        if item.form_only:
            # Never read from the record, so there is nothing to show.
            return ""
        value = self._value_at(record, path)
        if isinstance(item, RelationField):
            # A linked record reads here as it does everywhere else.
            return name_all_linked(
                item, value, views=self._views, inspector=self._inspector
            )
        return item.text_for(record, value)

    def _name_linked(self, item: RelationField, record: Any) -> str:
        """Name a record one of this view's links points at."""
        return name_linked(item, record, views=self._views, inspector=self._inspector)

    def get_record_title(self, record: M, /) -> str:
        """Name a record, for a heading, a link to it and the history.

        `record_title` first, then the model's own `__str__`. A model with
        neither is named by the view's label and the record's key, "Order
        #12", rather than by where it sits in memory.
        """
        if self._record_title:
            return self._record_title.format_map(RecordValues(record))
        if names_itself(record):
            return str(record)
        return _("{thing} #{key}", thing=self.label, key=self._identity_of(record))

    def _identity_of(self, record: Any) -> str:
        """The key of a record, as it appears in a URL."""
        return self._repository.identity_of(record)

    # Building a read.

    def _build_spec(
        self,
        *,
        request: Any = None,
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
        wanted = tuple(self._loadable(tuple(paths) or self._list_fields(request)))
        spec = QuerySpec(
            paths=wanted,
            defer=self._deferred(request, wanted),
            search=search,
            search_paths=self._search_paths(request),
            search_condition=self.search_condition(search.strip(), request=request)
            if search.strip()
            else None,
            filters=tuple(filters),
            sort=tuple(sort) or self._default_sort(request),
            limit=size or self.page_size,
            count=self.count_mode,
            keyset=self.pagination is Pagination.KEYSET,
            after=after,
            before=before,
        )
        return spec.page(page)

    def _deferred(self, request: Any, loaded: Sequence[str]) -> tuple[str, ...]:
        """The columns to leave out of this query.

        A column the page reads is never left out, whatever the view says,
        since reading it afterwards would cost a query for every row. That
        covers the columns on show, the key, and the ones the record's name
        is built from.
        """
        keep = set(loaded) | set(self._schema.primary_key) | self._named_in_title()
        named = self.get_deferred_fields(request)
        paths = (
            self._deferred_fields
            if named is self.deferred_fields
            else self._paths("get_deferred_fields", named, takes="own columns")
        )
        return tuple(path for path in paths if path not in keep)

    def _named_in_title(self) -> set[str]:
        """The columns `record_title` reads, which every row needs."""
        return set(template_names(self._record_title))

    # Actions.

    def get_actions(self, request: Request) -> Sequence[Action]:
        """The actions this view offers this user, in the order they appear.

        Delete comes last, where the view allows deleting several at once,
        unless the view has an action of its own by that name.
        """
        found = list(self._actions.values())
        deletes = self.can_delete and self.can_delete_selected
        if deletes and DELETE_ACTION not in self._actions:
            found.append(self._delete_action())
        return found

    def _delete_action(self) -> Action:
        """The built-in delete, worded for this view in the current language."""
        return Action(
            name=DELETE_ACTION,
            label=_("Delete"),
            method="_delete_selected",
            confirm=_(
                "Delete the chosen {things}? This cannot be undone.",
                things=self.label_plural.lower(),
            ),
            permission=Permission.DELETE,
            dangerous=True,
            writes_own_audit=True,
        )

    def _actions_on(self, target: str, request: Any = None) -> tuple[Action, ...]:
        """The actions of one kind: over a selection, a record or the view."""
        return tuple(item for item in self.get_actions(request) if item.on == target)

    def _action_named(self, name: str, request: Any = None) -> Action:
        """Find an action by name, or say it is not there.

        It looks through `get_actions` first, so an action built for this
        request, with its own choices or labels, is the one that runs.
        """
        for item in self.get_actions(request):
            if item.name == name:
                return item
        # No falling back to the class's own list: an action `get_actions`
        # leaves out for this user is not offered, so it cannot be run by
        # asking for it by name either.
        raise AdminSiteError(f"{type(self).__name__} has no action called {name!r}.")

    def _parse_action_inputs(self, found: Action, data: FormData) -> FormResult:
        """Read the values an action asked for, checked like form fields."""
        result = FormResult()
        for item in found.inputs:
            raw = data.get(item.name)
            try:
                if isinstance(item, UploadField):
                    result.values[item.name] = item.read_upload(raw)
                elif _holds_many(item):
                    result.values[item.name] = item.parse_many(_as_list(raw))
                else:
                    result.values[item.name] = item.parse(_as_text(raw))
            except FieldValidationError as error:
                result.errors[item.name] = error.message
        return result

    async def _run_record_action(
        self,
        found: Action,
        record: Any,
        session: SessionAdapter,
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action on one record, and say what to tell the user."""
        entry = self._action_entry(
            found,
            request,
            values,
            self._identity_of(record),
            self.get_record_title(record),
        )
        auditing = self._audit_log is not None
        paths = list(self._form_fields(request, record)) if auditing else []
        try:
            await self._ensure(found.permission, request=request, record=record)
            given, entry = await self._given(found, session, values, entry, request)
            before = self._snapshot(record, paths)
            answer = await self._call(found, record, request, session, given)
            if auditing:
                # Anything the record refuses surfaces here, while the entry
                # can still be written down as failed.
                await session.flush()
        except Exception as error:
            self._audit_failure(session, [entry], error)
            raise

        text = self._answer_text(found, answer)
        changes = (
            self._masked(diff(before, self._snapshot(record, paths)))
            if auditing
            else {}
        )
        self._write_audit(
            session,
            [replace(entry, changes=changes, message=self._kept_answer(found, text))],
        )
        return self._shown(answer, text)

    async def _run_view_action(
        self,
        found: Action,
        session: SessionAdapter,
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action that acts on the view, not on any record."""
        entry = self._action_entry(found, request, values, "", None)
        try:
            await self._ensure(found.permission, request=request)
            given, entry = await self._given(found, session, values, entry, request)
            answer = await self._call(found, None, request, session, given)
            if self._audit_log is not None:
                await session.flush()
        except Exception as error:
            self._audit_failure(session, [entry], error)
            raise

        text = self._answer_text(found, answer)
        self._write_audit(
            session, [replace(entry, message=self._kept_answer(found, text))]
        )
        return self._shown(answer, text)

    async def _run_action(
        self,
        found: Action,
        selection: Selection[M],
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action over a selection, and say what to tell the user.

        The values the action asked for are passed to its method by name.
        """
        entry = replace(
            self._action_entry(found, request, values, "", None), batch=str(uuid4())
        )
        auditing = self._audit_log is not None and not found.writes_own_audit
        keys: list[str] = []
        try:
            await self._ensure(found.permission, request=request)
            # Read the keys before the action runs: afterwards the rows may no
            # longer match the filter they were chosen by.
            if auditing:
                keys = await selection.covered_keys()
            given, entry = await self._given(
                found, selection.session, values, entry, request
            )
            answer = await self._call(
                found, selection, request, selection.session, given
            )
            if self._audit_log is not None:
                await selection.session.flush()
        except Exception as error:
            if auditing:
                failed = [replace(entry, record_key=key) for key in keys] or [entry]
                self._audit_failure(selection.session, failed, error)
            raise

        text = self._answer_text(found, answer)
        self._write_audit(
            selection.session,
            [
                replace(
                    entry,
                    record_key=key,
                    changes=self._masked(selection.changes.get(key, {})),
                    message=self._kept_answer(found, text),
                )
                for key in keys
            ],
        )
        return self._shown(answer, text)

    async def _resolve_inputs(
        self,
        found: Action,
        session: SessionAdapter,
        values: Mapping[str, Any],
        *,
        request: Any = None,
    ) -> dict[str, Any]:
        """The values an action was given, with the records its links name.

        A key is read through the target's own view, as a form's link is, so
        the method only ever gets a record this user may see. A key for any
        other record is refused, and nothing says whether it exists.
        """
        given = dict(values)
        for item in found.inputs:
            value = given.get(item.name)
            if not isinstance(item, RelationField) or value in (None, "", []):
                continue
            keys = value if isinstance(value, list | tuple | set) else [value]
            records = [
                await self._input_record(item, session, key, request) for key in keys
            ]
            given[item.name] = records if item.collection else records[0]
        return given

    async def _input_record(
        self, item: RelationField, session: SessionAdapter, key: Any, request: Any
    ) -> Any:
        """The record a link input names, or a refusal naming the input."""
        target = self._views.for_relation(item) if self._views is not None else None
        if target is not None:
            record = await self._linked_through(target, session, key, request)
        else:
            record = await self._linked_directly(item, session, key)
        if record is None:
            raise RefusedError(
                _("{field}: choose from the records offered.", field=item.label),
                field=item.name,
            )
        return record

    async def _linked_directly(
        self, item: RelationField, session: SessionAdapter, key: Any
    ) -> Any | None:
        """A linked record by its key, for a model no view shows."""
        repository = SQLAlchemyRepository(item.related_model, self._inspector)
        wanted = key
        if isinstance(key, str) and len(repository.schema.primary_key) > 1:
            wanted = tuple(key.split(","))
        try:
            return await repository.get(session, wanted)
        except InvalidPathError:
            return None

    async def _given(
        self,
        found: Action,
        session: SessionAdapter,
        values: Mapping[str, Any] | None,
        entry: AuditEntry,
        request: Any,
    ) -> tuple[dict[str, Any], AuditEntry]:
        """What the method is given, and the entry that writes it down.

        The entry keeps a linked record by its name, as the history names
        it everywhere else, rather than by its key.
        """
        given = await self._resolve_inputs(
            found, session, values or {}, request=request
        )
        named = dict(given)
        for item in found.inputs:
            if isinstance(item, RelationField) and named.get(item.name) is not None:
                named[item.name] = name_all_linked(
                    item, named[item.name], views=self._views, inspector=self._inspector
                )
        return given, replace(entry, inputs=recorded_inputs(found.inputs, named))

    async def _call(
        self,
        found: Action,
        subject: Any,
        request: Any,
        session: SessionAdapter,
        values: Mapping[str, Any],
    ) -> Any:
        """Call an action's method with what its parameters ask for.

        `subject` is the selection or the record it runs on, and `values`
        what the dialog asked for, by input name.
        """
        positional, named = self._call_of(found).arguments(
            subject=subject, request=request, session=session, values=values
        )
        return await getattr(self, found.method)(*positional, **named)

    def _call_of(self, found: Action) -> ActionCall:
        """How an action's method is called, read from its parameters.

        Read when the view is built for each marked method, and here for an
        action built by hand, such as the built-in delete.
        """
        if found.call is not None:
            return found.call
        return self._read_call(found)

    def _read_call(self, found: Action) -> ActionCall:
        return read_call(
            getattr(self, found.method),
            where=f"{type(self).__name__}.{found.method}",
            on=found.on,
            model=self.model,
            asked=found.inputs,
        )

    def _check_database(self, is_async: bool) -> None:
        """Refuse an action asking for an AsyncSession of a database that is not async.

        The admin calls it when the view is registered, so the mistake stops
        it starting rather than the action's first run.
        """
        if is_async:
            return
        for found in self._actions.values():
            asking = found.call.handed_as(ASYNC_SESSION) if found.call else None
            if asking is not None:
                where = f"{type(self).__name__}.{found.method}"
                raise AdminSiteError(f"{where}: {async_session_refused(asking.name)}")

    def _action_entry(
        self,
        found: Action,
        request: Any,
        values: Mapping[str, Any] | None,
        key: str,
        title: str | None,
    ) -> AuditEntry:
        """The entry an action run is written down as, before it has run."""
        return AuditEntry(
            view=self.name,
            record_key=key,
            record_title=title,
            event=AuditEvent.ACTION,
            action=found.label,
            inputs=recorded_inputs(found.inputs, values or {}),
            **actor_of(request),
        )

    def _answer_text(self, found: Action, answer: Any) -> str | None:
        """What to tell the user; nothing when the action sent a response."""
        if isinstance(answer, Response):
            return None
        return str(answer) if answer else _("{action} done.", action=found.label)

    def _shown(self, answer: Any, text: str | None) -> Any:
        """What the page is given: the answer itself, when it says more than text.

        A response is sent as it is, and a `Message` or `Html` keeps what
        plain text would lose: a link, a value to copy, its markup.
        """
        if isinstance(answer, Response | Message | Markup):
            return answer
        return text

    def _kept_answer(self, found: Action, text: str | None) -> str | None:
        """What the audit log keeps of the answer: nothing, if it is secret."""
        return text if found.audit_answer else None

    def _collect_actions(self) -> dict[str, Action]:
        """The marked methods, each with what its parameters are handed and ask for.

        Read now, so a parameter no dialog can ask for stops the admin
        starting rather than the action's first run.
        """
        found: dict[str, Action] = {}
        for name in dir(type(self)):
            marked = action_of(getattr(type(self), name, None))
            if marked is not None:
                call = self._read_call(marked)
                found[marked.name] = replace(
                    marked, inputs=(*marked.inputs, *call.inputs), call=call
                )
        return found

    # Permissions. Four levels: the view, the action, the field and the row.

    async def allows(
        self, action: Permission | str, *, request: Request, record: M | None
    ) -> bool:
        """Whether the current user may do this, to this record.

        `record` is None when the question is about the view as a whole,
        such as whether its list may be exported.
        """
        name = permission_name(action)
        if name == Permission.CREATE:
            return self.can_create
        if name == Permission.EDIT:
            return self.can_edit
        if name == Permission.DELETE:
            return self.can_delete
        if name == Permission.IMPORT:
            return self.can_import
        if name == Permission.VIEW_DETAIL:
            return self.can_view_detail
        if name == Permission.EXPORT:
            return self.can_export
        return True

    async def _ensure(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> None:
        """Raise unless the current user may do this."""
        if not await self.allows(action, request=request, record=record):
            raise PermissionDeniedError(permission_name(action), self.label_plural)

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        """Narrow every read to the rows this user may see.

        This runs on the list, the count, a single record, an export and a
        bulk action, so a row can never leak through a path that forgot to
        check.
        """
        return statement

    def _scope_for(self, request: Any = None) -> Scope:
        """The scope as a function, ready to hand to the repository."""
        return lambda statement: self.scope_query(statement, request=request)

    # Reading records.

    async def _fetch_page(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Any = None
    ) -> Page:
        """Read one page, within the scope and after a permission check."""
        await self._ensure(Permission.VIEW, request=request)
        return await self._repository.list(session, spec, self._scope_for(request))

    async def _fetch_record(
        self,
        session: SessionAdapter,
        key: Any,
        *,
        paths: Sequence[str] = (),
        request: Any = None,
    ) -> Any | None:
        """Load one record, or nothing if it is missing or out of scope."""
        await self._ensure(Permission.VIEW, request=request)
        return await self._repository.get(
            session, key, tuple(paths), self._scope_for(request)
        )

    async def _filter_options(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Any = None
    ) -> list[tuple[SQLFilter[Any], Sequence[FilterOption]]]:
        """Each filter beside the list, with the choices it offers.

        Counted within the scope, so a count never gives away how many
        records the user may not see.
        """
        await self._ensure(Permission.VIEW, request=request)
        context = SQLFilterContext(
            session, self._repository, spec, self._scope_for(request)
        )
        return [
            (item, await item.options(context)) for item in self._list_filters(request)
        ]

    async def _fetch_related(
        self,
        session: SessionAdapter,
        record: Any,
        path: str,
        *,
        limit: int,
        request: Any = None,
    ) -> tuple[Sequence[Any], int]:
        """The first records a to-many link of this record holds, and the total."""
        await self._ensure(Permission.VIEW_DETAIL, request=request, record=record)
        return await self._repository.related(session, record, path, limit=limit)

    # Writing.

    def _parse_form(
        self,
        data: FormData,
        *,
        record: Any = None,
        request: Any = None,
    ) -> FormResult:
        """Read a submitted form into values, collecting any messages."""
        result = FormResult()
        readonly = set(self._readonly_paths(request, record))
        draft: Any = None

        for path in self._form_fields(request, record):
            item = self._field_for(path)
            if path in readonly or not (item.stored or item.form_only):
                continue
            raw = data.get(path)
            if item.keeps_value_when_blank and record is not None and _is_blank(raw):
                # Left empty on a record that exists: it keeps what it has.
                continue
            try:
                if isinstance(item, FileField):
                    choice = item.parse_upload(
                        raw,
                        remove=data.get(f"{path}-remove") is not None,
                        has_file=bool(
                            record is not None and self._value_at(record, path)
                        ),
                    )
                    if choice is not UNCHANGED:
                        result.values[path] = choice
                elif isinstance(item, JSONField) and item.schema is not None:
                    # Drawn from its schema, the document has an input for
                    # each of its parts. A new record's schema may follow
                    # what the rest of the form holds.
                    owner = record
                    if owner is None and item.schema_from_record:
                        if draft is None:
                            draft = self._draft_record(data, request)
                        owner = draft
                    result.values[path] = item.read_form(data, path, record=owner)
                elif _holds_many(item):
                    result.values[path] = item.parse_many(_as_list(raw))
                else:
                    result.values[path] = item.parse(_as_text(raw))
            except DocumentError as error:
                result.errors.update(error.errors)
            except FieldValidationError as error:
                result.errors[path] = error.message

        for inline in self.get_inlines(request, record):
            result.inline_rows[inline.name] = self._parse_inline(
                inline, data, result.errors, request
            )
        return result

    def _parse_inline(
        self,
        inline: Inline,
        data: FormData,
        errors: dict[str, str],
        request: Any,
    ) -> list[InlineRow]:
        child = self._inline_view(inline.name)
        readonly = set(child._readonly_paths(request))
        paths = [path for path in child._form_fields(request) if path not in readonly]
        try:
            count = int(_as_text(data.get(f"{inline.name}-count")) or 0)
        except ValueError:
            count = 0

        rows: list[InlineRow] = []
        for index in range(count):
            key = (_as_text(data.get(inline.input_name(index, "key"))) or "").strip()
            delete = data.get(inline.input_name(index, "delete")) is not None
            raw = {path: data.get(inline.input_name(index, path)) for path in paths}
            if not key and not any(_as_text(value) for value in raw.values()):
                # An empty row left over from "add another".
                continue
            row = InlineRow(key=key, delete=delete)
            if not delete:
                for path in paths:
                    item = child._field_for(path)
                    try:
                        row.values[path] = item.parse(_as_text(raw[path]))
                    except FieldValidationError as error:
                        errors[inline.input_name(index, path)] = error.message
            rows.append(row)
        return rows

    async def _save(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        *,
        record: Any = None,
        request: Any = None,
        inline_rows: Mapping[str, Sequence[InlineRow]] | None = None,
    ) -> Any:
        """Create or change a record, running the hooks in one transaction.

        A hook that raises rolls the whole save back, so business rules can
        refuse a change.
        """
        created = record is None
        await self._ensure(
            Permission.CREATE if created else Permission.EDIT,
            request=request,
            record=record,
        )
        # A copy, so a hook can change what is stored without the caller's
        # own dictionary changing under it.
        values, stored = await self._store_files(session, dict(values), record)
        try:
            async with session.transaction():
                values = await self._resolve_links(session, values, request)
                target = record if record is not None else self._repository.model()
                context = SaveContext(
                    session=session,
                    record=target,
                    values=SaveValues(target, values, form_only=self._form_only),
                    created=created,
                    request=request,
                )
                await self.before_save(context)
                # Whatever the hook left in context.values is what is stored,
                # apart from form-only values, which the hooks store themselves.
                values = {
                    path: value
                    for path, value in stored_values(context.values).items()
                    if not self._form_only(path)
                }

                auditing = self._audit_log is not None
                before = (
                    self._snapshot(target, list(values))
                    if auditing and not created
                    else {}
                )
                if not created:
                    await self._clear_reordered(session, target, values)
                async with session.no_autoflush():
                    await self._repository.apply_values(session, target, values)
                    await self._apply_inlines(
                        session, target, inline_rows or {}, request
                    )
                if created:
                    await session.add(target)
                await session.flush()

                await self.after_save(context)
                session.after_commit(partial(self.after_save_committed, context))
                self._audit_save(
                    session, target, before, list(values), request, created=created
                )
        except IntegrityError as error:
            await self._discard_files(stored)
            raise RefusedError(
                _(
                    "This {thing} could not be saved, because it clashes with "
                    "another record. A value that must be unique may already "
                    "be taken.",
                    thing=self.label.lower(),
                )
            ) from error
        except BaseException:
            await self._discard_files(stored)
            raise
        return target

    async def _clear_reordered(
        self, session: SessionAdapter, record: Any, values: Mapping[str, Any]
    ) -> None:
        """Empty each ordered link whose new order a plain save would lose.

        Saving a link to many only adds and removes what changed, so the rows
        kept stay where they were, and one added goes last. That loses a new
        order, so such a link is emptied here, and written again whole, in
        the order given, when the values are applied.
        """
        cleared = False
        for path, value in values.items():
            item = self._field_for(path)
            if not isinstance(item, RelationField) or not item.ordered:
                continue
            target = SQLAlchemyRepository(item.related_model, self._inspector)

            def key_of(one: Any, target: SQLAlchemyRepository[Any] = target) -> str:
                return (
                    target.identity_of(one)
                    if isinstance(one, target.model)
                    else str(one)
                )

            held = [key_of(one) for one in getattr(record, path) or ()]
            wanted = [key_of(one) for one in value or ()]
            # What adding and removing alone would leave: the rows kept, in
            # their old order, then the new ones.
            kept = [key for key in held if key in wanted]
            if kept + [key for key in wanted if key not in held] != wanted:
                setattr(record, path, [])
                cleared = True
        if cleared:
            await session.flush()

    async def _store_files(
        self, session: SessionAdapter, values: Mapping[str, Any], record: Any
    ) -> tuple[dict[str, Any], list[tuple[FileField, str]]]:
        """Store new uploads and swap them for their keys.

        The files a save replaces or removes are deleted once it commits,
        so a save that fails never loses the file that was there.
        """
        ready = dict(values)
        stored: list[tuple[FileField, str]] = []
        try:
            for path, value in values.items():
                item = self._field_for(path)
                if not isinstance(item, FileField):
                    continue
                if isinstance(value, NewFile):
                    key = await item.storage.save(value.upload)
                    stored.append((item, key))
                    ready[path] = key
                old = self._value_at(record, path) if record is not None else None
                if old and old != ready[path]:
                    session.after_commit(_deleting(item, old))
        except BaseException:
            await self._discard_files(stored)
            raise
        return ready, stored

    async def _discard_files(self, stored: Sequence[tuple[FileField, str]]) -> None:
        """Remove files stored for a save that did not go through."""
        for item, key in stored:
            await item.storage.delete(key)

    async def _delete(
        self, session: SessionAdapter, record: Any, *, request: Any = None
    ) -> None:
        """Delete a record, running the hooks in one transaction."""
        await self._ensure(Permission.DELETE, request=request, record=record)
        try:
            async with session.transaction():
                await self._delete_within(session, record, request=request)
        except IntegrityError as error:
            raise RefusedError(
                _(
                    "This {thing} cannot be deleted, because other records "
                    "still refer to it.",
                    thing=self.label.lower(),
                )
            ) from error

    async def _delete_within(
        self, session: SessionAdapter, record: Any, *, request: Any = None
    ) -> None:
        """Delete one record inside a transaction the caller holds open."""
        await self._ensure(Permission.DELETE, request=request, record=record)
        context = DeleteContext(session=session, record=record, request=request)
        await self.before_delete(context)
        auditing = self._audit_log is not None
        before = (
            self._snapshot(record, self._form_fields(request, record))
            if auditing
            else {}
        )
        key, title = self._identity_of(record), self.get_record_title(record)
        await self._repository.delete(session, record)
        await self.after_delete(context)
        session.after_commit(partial(self.after_delete_committed, context))
        if not auditing:
            return
        self._write_audit(
            session,
            [
                AuditEntry(
                    view=self.name,
                    record_key=key,
                    record_title=title,
                    event=AuditEvent.DELETED,
                    changes=self._masked(
                        {
                            name: (value, None)
                            for name, value in before.items()
                            if value not in ("", None)
                        }
                    ),
                    **actor_of(request),
                )
            ],
        )

    async def _delete_selected(self, selection: Selection[M]) -> str:
        """Delete the chosen records, each as a single delete would, all or none.

        Every record goes through `allows`, `before_delete` and
        `after_delete`, and gets its own entry in the audit log. When one is
        refused, nothing is deleted, and the message names it.
        """
        request = selection.request
        records = await selection.records(
            paths=self._loadable(self._form_fields(request))
        )
        if len(records) > BULK_DELETE_LIMIT:
            raise RefusedError(
                _(
                    "Delete at most {count} at a time. Narrow the list first.",
                    count=f"{BULK_DELETE_LIMIT:,}",
                )
            )
        for record in records:
            title = self.get_record_title(record)
            try:
                await self._delete_within(selection.session, record, request=request)
            except (RefusedError, PermissionDeniedError) as error:
                raise RefusedError(
                    _(
                        "Nothing was deleted, because {thing} cannot be: {reason}",
                        thing=title,
                        reason=str(error),
                    )
                ) from error
            except IntegrityError as error:
                raise RefusedError(
                    _(
                        "Nothing was deleted, because other records still refer "
                        "to {thing}.",
                        thing=title,
                    )
                ) from error
        things = self.label if len(records) == 1 else self.label_plural
        return _("{count} {things} deleted.", count=len(records), things=things.lower())

    async def _apply_inlines(
        self,
        session: SessionAdapter,
        parent: Any,
        inline_rows: Mapping[str, Sequence[InlineRow]],
        request: Any = None,
    ) -> None:
        """Add, change and remove child records as the form asked."""
        for inline in self.inlines:
            rows = inline_rows.get(inline.name)
            if not rows:
                continue
            child_view = self._inline_view(inline.name)
            children = getattr(parent, inline.name)
            by_key = {child_view._identity_of(child): child for child in children}
            for row in rows:
                if row.is_new:
                    if not row.delete:
                        child = child_view.model()
                        values = await self._resolve_links(
                            session, row.values, request, fields_of=child_view
                        )
                        await child_view._repository.apply_values(
                            session, child, values
                        )
                        children.append(child)
                    continue
                existing = by_key.get(row.key)
                if existing is None:
                    raise RecordNotFoundError(child_view.model, row.key)
                if row.delete:
                    if inline.can_delete:
                        children.remove(existing)
                        await session.delete(existing)
                    continue
                values = await self._resolve_links(
                    session, row.values, request, fields_of=child_view
                )
                await child_view._repository.apply_values(session, existing, values)

    async def _resolve_links(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        request: Any,
        *,
        fields_of: "ModelView[Any] | None" = None,
    ) -> dict[str, Any]:
        """Turn the keys sent for links into records, through their own view.

        The picker offered only the records the target's view lets this
        user see, so a key for any other record did not come from the form.
        It is refused like any other bad choice, and nothing is said about
        whether the record exists. A target with no view is left to the
        repository, which loads it by key.
        """
        owner = fields_of or self
        resolved = dict(values)
        for path, value in values.items():
            item = owner._field_for(path)
            if not isinstance(item, RelationField) or self._views is None:
                continue
            if value is None or value == "" or value == []:
                continue
            target = self._views.for_relation(item)
            if target is None:
                continue
            keys = value if isinstance(value, list | tuple | set) else [value]
            found = []
            for key in keys:
                record = key
                if not isinstance(record, item.related_model):
                    record = await self._linked_through(target, session, key, request)
                if record is None:
                    raise RefusedError(_("Choose a record."), field=path)
                found.append(record)
            resolved[path] = found if item.collection else found[0]
        return resolved

    async def _linked_through(
        self, target: "ModelView[Any]", session: SessionAdapter, key: Any, request: Any
    ) -> Any | None:
        """One linked record, if the target's view lets this user see it."""
        wanted = key
        if isinstance(key, str) and len(target._schema.primary_key) > 1:
            wanted = tuple(key.split(","))
        try:
            return await target._fetch_record(session, wanted, request=request)
        except (PermissionDeniedError, InvalidPathError):
            return None

    def _snapshot(self, record: Any, paths: Sequence[str]) -> dict[str, Any]:
        """What a record shows for these paths, as the history records it.

        Only what is already loaded is read. Touching anything else would
        start a lazy load, which an async session cannot do.
        """
        state = sqlalchemy_inspect(record, raiseerr=False)
        unloaded = state.unloaded if state is not None else set()
        return {
            path: self._display(record, path)
            for path in paths
            if path.split(".", 1)[0] not in unloaded and not self._form_only(path)
        }

    def _audit_save(
        self,
        session: SessionAdapter,
        record: Any,
        before: Mapping[str, Any],
        paths: Sequence[str],
        request: Any,
        *,
        created: bool,
    ) -> None:
        if self._audit_log is None:
            return
        after = self._snapshot(record, paths)
        changes = self._masked(
            {name: (None, value) for name, value in after.items() if value}
            if created
            else diff(before, after)
        )
        if not changes and not created:
            return
        self._write_audit(
            session,
            [
                AuditEntry(
                    view=self.name,
                    record_key=self._identity_of(record),
                    record_title=self.get_record_title(record),
                    event=AuditEvent.CREATED if created else AuditEvent.UPDATED,
                    changes=changes,
                    **actor_of(request),
                )
            ],
        )

    def _audit_failure(
        self, session: SessionAdapter, entries: Sequence[AuditEntry], error: Exception
    ) -> None:
        """Write entries down as failed, once the work they describe is undone."""
        log = self._audit_log
        if log is None or not entries:
            return
        # A refusal is worded for people; anything else is a fault, and only
        # its kind goes in the log, since its text may hold the data itself.
        reason = (
            str(error)
            if isinstance(error, AdminSiteError)
            else _("It stopped with an error: {kind}.", kind=type(error).__name__)
        )
        failed = [replace(entry, error=reason) for entry in entries]

        async def write() -> None:
            await record_or_warn(log, failed)

        session.after_rollback(write)

    def _write_audit(
        self, session: SessionAdapter, entries: Sequence[AuditEntry]
    ) -> None:
        """Write entries down with the change they describe.

        A log in the admin's own database is written in the same transaction,
        so the change and its entries are saved together or not at all. Any
        other log is written once the change has committed; if that fails,
        the change stays, and the server log says which entries were lost.
        """
        log = self._audit_log
        if log is None or not entries:
            return
        within = getattr(log, "record_within", None)
        if self._audit_with_changes and within is not None:

            async def write_within() -> None:
                await within(session, entries)

            session.before_commit(write_within)
            return

        async def write() -> None:
            await record_or_warn(log, entries)

        session.after_commit(write)

    async def before_save(self, context: SaveContext[M]) -> None:
        """Runs before the values are written.

        `context.values[Order.slug].set(...)` stores something other than
        what was submitted. Raise `RefusedError` to refuse the save, naming
        a field to put the message beside it.
        """

    async def after_save(self, context: SaveContext[M]) -> None:
        """Runs after the flush, while the transaction is still open."""

    async def after_save_committed(self, context: SaveContext[M]) -> None:
        """Runs once the save has committed, such as to send an email.

        The change is stored by now, so nothing here can undo it: an error is
        written to the server's log, and the save still succeeds. The
        transaction is over, so write through a session of your own.
        """

    async def before_delete(self, context: DeleteContext[M]) -> None:
        """Runs before a record is deleted. Raise to refuse the delete."""

    async def after_delete(self, context: DeleteContext[M]) -> None:
        """Runs after the delete, while the transaction is still open."""

    async def after_delete_committed(self, context: DeleteContext[M]) -> None:
        """Runs once the delete has committed.

        As with `after_save_committed`, an error here is logged and the
        delete still stands.
        """

    def _built_filters(
        self, setting: str, entries: Sequence[ColumnReference | SQLFilter[M]]
    ) -> tuple[SQLFilter[Any], ...]:
        """The filters a setting names: a column's own, or one given whole."""
        repository = SQLAlchemyRepository(self.model, self._inspector)
        built: list[SQLFilter[Any]] = []
        for item in self._entries(setting, entries):
            if isinstance(item, SQLFilter):
                built.append(item)
            elif is_column(item):
                path = self._converted(setting, item, path_of)
                self._check_path(setting, path, self.model, "paths")
                built.append(filter_for(repository, path))
            else:
                raise AdminSiteError(
                    f"{type(self).__name__}.{setting} takes columns or "
                    f"SQLFilter instances, not {type(item).__name__}."
                )
        return tuple(built)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model.__name__})"


def view_class(view: type[ModelView[Any]]) -> type[ModelView[Any]]:
    """The class a view is built from.

    `ModelView[Tag]` is an alias rather than a class, so it gets a class of
    its own, `TagView`, as if one had been written with no settings.
    """
    if isinstance(view, type):
        return view
    arguments = get_args(view)
    named = getattr(arguments[0], "__name__", "Model") if arguments else "Model"
    module = getattr(view, "__module__", __name__)
    return types.new_class(
        f"{named}View", (view,), exec_body=lambda body: body.update(__module__=module)
    )


def _as_text(raw: str | Sequence[str] | None) -> str | None:
    """Read one value out of form data, which may hold several."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return raw[0] if raw else None


def _is_blank(raw: str | Sequence[str] | None) -> bool:
    """Whether nothing but spaces was sent."""
    return not (_as_text(raw) or "").strip()


def _holds_many(item: BaseField) -> TypeGuard[RelationField | EnumField]:
    """Whether the input sends several values rather than one."""
    if isinstance(item, RelationField):
        return item.collection
    return isinstance(item, EnumField) and item.multiple


def _as_list(raw: str | Sequence[str] | None) -> list[str]:
    """Read every value out of form data."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _deleting(item: FileField, key: str) -> Any:
    """Work that deletes one stored file, for after the commit."""

    async def work() -> None:
        await item.storage.delete(key)

    return work
