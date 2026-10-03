import types
from collections.abc import Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    TypeVar,
    get_args,
    get_origin,
)

from sqlalchemy import ColumnElement
from starlette.requests import Request

if TYPE_CHECKING:
    from adminsite.audit import AuditStore
    from adminsite.views.registry import ViewRegistry

from adminsite.actions.action import Action
from adminsite.actions.selection import Selection
from adminsite.backends.sqlalchemy.filters import (
    SQLFilter,
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
)
from adminsite.exceptions import (
    AdminSiteError,
    NotAModelError,
    PermissionDeniedError,
)
from adminsite.fields import (
    BaseField,
    ComputedField,
    Field,
    FieldRegistry,
    default_registry,
)
from adminsite.i18n import gettext as _
from adminsite.query import CountMode, Pagination
from adminsite.security import Permission, RequestAction, permission_name
from adminsite.text import (
    RecordValues,
    names_itself,
    pluralize,
    snake_case,
)
from adminsite.views.action_runner import DELETE_ACTION, ActionRunner
from adminsite.views.auditing import AuditRecorder
from adminsite.views.form_parsing import FormParser
from adminsite.views.inline import Inline, InlineViews
from adminsite.views.links import Links
from adminsite.views.pages import PageFields
from adminsite.views.reading import Reader
from adminsite.views.saving import Saver
from adminsite.views.settings import SettingsReader
from adminsite.views.view_fields import ViewFields
from adminsite.views.writing import (
    DeleteContext,
    SaveContext,
)

__all__ = [
    "ModelView",
    "view_class",
]

# The model a view shows. Not bound to DeclarativeBase: a SQLModel model is
# mapped without it. The admin refuses a class that is not mapped.
M = TypeVar("M")


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
    # The columns the list can be sorted by. Left out, every stored column;
    # an empty list, none.
    sortable_fields: Sequence[ColumnReference] | None = None
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
        self._settings = SettingsReader(self, self._schema, self._inspector)

        self._inline_views = InlineViews(
            self, self._settings, self._schema, self._inspector, self._registry
        )
        self._repository = SQLAlchemyRepository(
            self.model, self._inspector, self._settings.list_filters
        )
        self._fields = ViewFields(
            self, self._settings, self._inspector, self._registry, self._repository
        )
        self._pages = PageFields(
            self,
            self._settings,
            self._fields,
            self._inspector,
            self._schema,
            self._inline_views,
        )
        self._reader = Reader(
            self,
            self._settings,
            self._fields,
            self._pages,
            self._repository,
            self._schema,
        )
        self._links = Links(self, self._inspector)
        self._forms = FormParser(self, self._fields, self._pages)
        self._audit = AuditRecorder(self, self._fields)
        self._saver = Saver(
            self,
            self._fields,
            self._pages,
            self._links,
            self._audit,
            self._repository,
            self._inspector,
        )
        self._actions = ActionRunner(
            self, self._fields, self._pages, self._links, self._audit, self._inspector
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

    def get_searchable_fields(self, request: Request) -> Sequence[ColumnReference]:
        """The columns the search box looks in, for this user."""
        return self.searchable_fields

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

    def get_fields_default_sort(
        self, request: Request
    ) -> Sequence[ColumnReference | Descending]:
        """The order the list starts in, for this user."""
        return self.fields_default_sort

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

    def get_inlines(self, request: Request, record: M | None) -> Sequence[Inline]:
        """The child records edited inside the form, for this user and record.

        `record` is None on the form for a new record. Each inline returned
        has to be one of `inlines`, whose rows are checked when the admin
        starts.
        """
        return self.inlines

    async def form_only_values(
        self, session: SessionAdapter, record: M | None, *, request: Request
    ) -> Mapping[str, Any]:
        """The values form-only fields start from, by name.

        Nothing by default, so they start empty. Answer with, say, settings
        kept as rows of another table, to edit them as one value; `record`
        is None on the form for a new record.
        """
        return {}

    def get_record_title(self, record: M, /) -> str:
        """Name a record, for a heading, a link to it and the history.

        `record_title` first, then the model's own `__str__`. A model with
        neither is named by the view's label and the record's key, "Order
        #12", rather than by where it sits in memory.
        """
        if self._settings.record_title:
            return self._settings.record_title.format_map(RecordValues(record))
        if names_itself(record):
            return str(record)
        return _(
            "{thing} #{key}", thing=self.label, key=self._fields.identity_of(record)
        )

    # Actions.

    def get_actions(self, request: Request) -> Sequence[Action]:
        """The actions this view offers this user, in the order they appear.

        Delete comes last, where the view allows deleting several at once,
        unless the view has an action of its own by that name.
        """
        found = list(self._actions.marked.values())
        deletes = self.can_delete and self.can_delete_selected
        if deletes and DELETE_ACTION not in self._actions.marked:
            found.append(self._actions.delete_action())
        return found

    async def _delete_selected(self, selection: Selection[M]) -> str:
        """Delete the chosen records: the built-in Delete action."""
        return await self._saver.delete_selected(selection)

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
        self, action: Permission | str, *, request: Request, record: M | None = None
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

    def _scope_for(self, request: Request) -> Scope:
        """The scope as a function, ready to hand to the repository."""
        return lambda statement: self.scope_query(statement, request=request)

    # Hooks around saves and deletes.

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
