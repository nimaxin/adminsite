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

from adminsite._orm.repository import Scope, SQLAlchemyRepository
from adminsite._text import RecordValues, names_itself, pluralize, snake_case
from adminsite.actions.base import Action
from adminsite.actions.selection import Selection
from adminsite.columns import (
    ColumnReference,
    Descending,
)
from adminsite.database import SessionAdapter, Statement
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
from adminsite.filters.sql import SQLFilter
from adminsite.i18n import gettext as _
from adminsite.inspector import SQLAlchemyInspector
from adminsite.permissions import Permission, RequestAction, permission_name
from adminsite.query import CountMode, Pagination
from adminsite.views._actions import DELETE_ACTION, ActionRunner
from adminsite.views._audit import AuditRecorder
from adminsite.views._fields import ViewFields
from adminsite.views._forms import FormParser
from adminsite.views._inline_views import InlineViews
from adminsite.views._links import Links
from adminsite.views._pages import PageFields
from adminsite.views._reader import Reader
from adminsite.views._saver import Saver
from adminsite.views._settings import SettingsReader
from adminsite.views.contexts import DeleteContext, SaveContext
from adminsite.views.inlines import Inline
from adminsite.views.layout import LayoutEntry

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

    Args:
        inspector: Reads the model's columns and relationships. The admin
            passes its own when it builds the view from its class.
        registry: Picks the field for each type of column. The admin
            passes its `field_registry` when it builds the view from its
            class.
    """

    model: type[M]
    """The model the view shows, taken from `ModelView[Order]`."""
    # The type parameter that stands for the model in a view that is generic
    # itself, such as class ShopView(ModelView[M]), so a class written as
    # ShopView[Order] can find its model.
    _model_parameter: ClassVar[object] = None

    name: str = ""
    """The view's name in its URLs, such as `orders` in /admin/orders.

    Left empty, the model's name, plural and in snake case: `order_items`.
    Two views of one model each need a name of their own.
    """
    label: str = ""
    """What one record is called in headings and buttons, such as "Order".

    Left empty, the model's name in words: `OrderItem` reads "Order item".
    """
    label_plural: str = ""
    """What the records are called in the sidebar and the list's heading.

    Left empty, the model's name in words, made plural: "Order items".
    """
    group: str = ""
    """The sidebar section the view sits under, such as "Sales".

    Views with no group sit together, under no heading.
    """
    icon: str = ""
    """The view's icon in the sidebar: inline SVG markup, or a picture's address.

    Markup goes into the page as it is, so keep it to icons you control. A
    relative address starts at the admin, so a plugin's `add_static` folder
    works.
    """
    record_title: str = ""
    """How a record is named in headings, links to it and the history.

    A template of the model's columns, such as `"Order #{id}"` or
    `"{name} ({email})"`, each name checked when the admin starts. Left
    empty, the model's own `__str__`, or else the view's label and the
    record's key, "Order #12". Links from other models name the record the
    same way. `get_record_title` names one no template can.
    """

    # A field's value type differs from one to the next, hence Field[Any].
    fields: Sequence[ColumnReference | Field[Any] | ComputedField[M, Any]] = ()
    """Every field of the view, once, in the order each page shows them.

    A column is named by its attribute, `Order.total`, by a `Link` for a
    column of a related model, or by its name as a string, `"total"` or
    `"customer.email"`. A field, such as `Field(Order.total,
    read_only=True)`, gives the column options. Left empty, the view shows
    every column of the model, a foreign key as its relationship.
    """
    exclude_fields_from_list: Sequence[ColumnReference] = ()
    """Fields left off the list, as `exclude_from_list` on a field does."""
    exclude_fields_from_detail: Sequence[ColumnReference] = ()
    """Fields left off the record page, as `exclude_from_detail` on a field does."""
    exclude_fields_from_create: Sequence[ColumnReference] = ()
    """Fields left off the form for a new record, as `exclude_from_create` does."""
    exclude_fields_from_edit: Sequence[ColumnReference] = ()
    """Fields left off the edit form, as `exclude_from_edit` on a field does."""
    exclude_fields_from_export: Sequence[ColumnReference] = ()
    """Fields left out of the CSV export, as `exclude_from_export` on a field does."""
    searchable_fields: Sequence[ColumnReference] = ()
    """The columns the search box looks in.

    Text matches anywhere in the value, a number only exactly. Left empty,
    the list has no search box, and the command palette skips the view.
    """
    sortable_fields: Sequence[ColumnReference] | None = None
    """The columns people may sort the list by.

    Left out, every column the list shows, but no relationship and no
    column reached through one holding many records. An empty list, none.
    """
    fields_default_sort: Sequence[ColumnReference | Descending] = ()
    """The order the list starts in.

    `Descending(Order.created_at)`, or `"-created_at"`, sorts newest first.
    Left empty, the list goes by the record's key.
    """

    list_filters: Sequence[ColumnReference | SQLFilter[M]] = ()
    """The filters beside the list.

    A column gets the filter that suits its type; a `SQLFilter` of your own
    decides for itself.
    """
    inline_editable_fields: Sequence[ColumnReference] = ()
    """The fields whose values can be changed straight from the list.

    A cell of each opens a small editor under it, which saves that one value
    as the edit form does: `allows`, `can_access_field`,
    `get_readonly_fields`, the save hooks and the audit log all apply, and a
    cell this user may not change stays plain text. The list's first column
    opens the record, so it cannot be one of them.
    """
    page_size: int = 25
    """How many rows a page of the list holds."""
    page_size_options: Sequence[int] = ()
    """The page sizes people may switch between, such as `[25, 100, 500]`.

    Left empty, the size stays at `page_size`.
    """
    count_mode: CountMode = CountMode.EXACT
    """How hard the list works to say how many records match.

    The rows never wait for it: the total follows them in a request of its
    own. `CountMode.ESTIMATED` suits a table of millions of rows on Postgres
    or MySQL, and `CountMode.NONE` skips the count. Either also leaves the
    counts off the filters' options, unless a filter is given
    `show_counts=True`.
    """
    global_search: bool = True
    """Whether the command palette looks through this view's records."""
    pagination: Pagination = Pagination.OFFSET
    """How the list moves between pages: page numbers, or a keyset for big tables."""
    list_refresh_seconds: int | None = None
    """How often an open list reads its rows again, in seconds, such as 30.

    New and changed records then show up without a reload, under the search,
    the filters, the sort and the page in use. The list waits while someone
    is at work on it, with a row ticked, a menu, a dialog or a value's editor
    open, the focus in the table or its text selected, and while its tab is
    hidden, which asks the database nothing; shown again, it reads them at
    once. Left None, a list changes only when someone asks it to.
    """

    deferred_fields: Sequence[ColumnReference] = ()
    """Columns the list leaves out of its query, such as a large JSON payload.

    The record page, the form and the API load them as usual. A column the
    list shows is loaded whatever this says, as is anything `record_title`
    reads.
    """

    inlines: Sequence[Inline] = ()
    """Child records edited inside this model's form, such as order lines."""
    form_layout: Sequence[LayoutEntry] = ()
    """How the forms and the record page arrange the fields.

    Panels, fieldsets, rows and tabs, built from `PanelWidget`,
    `FieldsetWidget`, `RowWidget` and `TabsWidget`; a tuple is a row and a
    list a column. Left empty, one field under another, in the order of
    `fields`. A field the layout leaves out comes last.
    """

    can_create: bool = True
    """Whether records can be added. `allows` decides per user."""
    can_view_detail: bool = True
    """Whether records have a page of their own. Without it, rows open the form."""
    can_export: bool = True
    """Whether the list can be downloaded as CSV."""
    can_edit: bool = True
    """Whether records can be changed."""
    can_delete: bool = True
    """Whether records can be deleted."""
    can_delete_selected: bool = True
    """Whether the ticked rows can be deleted together.

    The Delete action then sits in the bar that rises when rows are ticked.
    `can_delete` has to allow it too.
    """
    can_import: bool = False
    """Whether records can be imported from a CSV or Excel file.

    Off until you switch it on, since it writes many records at once.
    """
    import_limit: int = 10_000
    """The most rows one imported file may hold."""

    # The other views of the same admin, set when the view is registered,
    # so a link can be checked against the view of the model it points at.
    _views: "ViewRegistry | None" = None

    in_sidebar: bool = True
    """Whether the view is listed in the sidebar.

    False leaves it out of the sidebar, the command palette's pages and the
    overview's counts, for a view whose records are only opened from other
    records. Its pages, links and pickers stay as they are.
    """

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

        self._fill_names()

        # The settings as the paths the rest of adminsite works with.
        self._settings = SettingsReader(self, self._schema, self._inspector)
        # Whether scope_query narrows anything, so a page showing this model's
        # records through a link asks it only when it does.
        self._scoped = type(self).scope_query is not ModelView.scope_query
        # Whether allows is the project's own, which may answer differently
        # for each record, so the Activity page loads the records to ask it.
        self._custom_allows = type(self).allows is not ModelView.allows

        self._inline_views = InlineViews(
            self,
            ModelView,
            self._settings,
            self._schema,
            self._inspector,
            self._registry,
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

    def _fill_names(self) -> None:
        """Name the view after its model wherever no name was given."""
        # Outside __init__, so the Reference page shows each setting's own
        # default rather than this line.
        self.name = self.name or pluralize(snake_case(self.model.__name__))
        self.label = self.label or self._schema.label
        self.label_plural = self.label_plural or self._schema.label_plural

    # Reading the configuration. Override these when the answer depends on
    # the request, for example to hide a column from some people.

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        """Whether this user sees the field on this page.

        Every field is shown to everyone by default. Answer False to keep one
        from someone, such as a cost price from staff who are not managers:
        it leaves the page, the export and the API, and no form reads it
        back.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.
            field: The field, whose `name` is the path it shows, such as
                "total" or "customer.email".
            action: The page asking, such as `RequestAction.LIST`.

        Returns:
            True to show the field, False to keep it from this user.
        """
        return True

    def get_searchable_fields(self, request: Request) -> Sequence[ColumnReference]:
        """The columns the search box looks in, for this user.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            Columns, as `searchable_fields` names them; that setting by
            default.
        """
        return self.searchable_fields

    def search_condition(
        self, term: str, *, request: Request
    ) -> ColumnElement[bool] | None:
        """The condition the search box matches with, or None for the usual one.

        The usual one looks for the term inside every search field, which a
        large table cannot answer from an index. Return a condition of your
        own, such as an exact match on a normalised phone number, and the
        list, its count, the export, "select all matching", the command
        palette and pickers all use it.

        Args:
            term: What was typed into the search box.
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            A condition on the view's model, or None for the usual search.
        """
        return None

    def get_list_filters(
        self, request: Request
    ) -> Sequence[ColumnReference | SQLFilter[M]]:
        """The filters offered beside the list, for this user.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            Columns and filters, as `list_filters` takes them; that setting
            by default.
        """
        return self.list_filters

    def get_fields_default_sort(
        self, request: Request
    ) -> Sequence[ColumnReference | Descending]:
        """The order the list starts in, for this user.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            Columns, any of them `Descending`, as `fields_default_sort`
            takes them; that setting by default.
        """
        return self.fields_default_sort

    def get_deferred_fields(self, request: Request) -> Sequence[ColumnReference]:
        """The columns the list leaves out of its query, for this user.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            Columns, as `deferred_fields` names them; that setting by
            default.
        """
        return self.deferred_fields

    def get_readonly_fields(
        self, request: Request, record: M | None
    ) -> Sequence[ColumnReference]:
        """Fields shown on this record's form but not editable there.

        A field with `read_only=True` is never editable. Answer with more
        for one record or one user, such as the customer of a shipped order.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.
            record: The record on the form, or None on the form for a new
                record.

        Returns:
            The fields to lock as well, named as in `fields`. None by
            default.
        """
        return []

    def get_inlines(self, request: Request, record: M | None) -> Sequence[Inline]:
        """The child records edited inside the form, for this user and record.

        Args:
            request: The request, with the signed in user on
                `request.state.user`.
            record: The record on the form, or None on the form for a new
                record.

        Returns:
            Inlines from `inlines`, whose rows are checked when the admin
            starts; all of them by default.
        """
        return self.inlines

    async def form_only_values(
        self, session: SessionAdapter, record: M | None, *, request: Request
    ) -> Mapping[str, Any]:
        """The values form-only fields start from, by name.

        Nothing by default, so they start empty. Answer with, say, settings
        kept as rows of another table, to edit them as one value.

        Args:
            session: The session the form is read in.
            record: The record on the form, or None on the form for a new
                record.
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            Each form-only field's starting value, by the field's name.
        """
        return {}

    def get_record_title(self, record: M, /) -> str:
        """Name a record, for a heading, a link to it and the history.

        `record_title` first, then the model's own `__str__`. A model with
        neither is named by the view's label and the record's key, "Order
        #12", rather than by where it sits in memory.

        Args:
            record: The record, with its columns loaded but not its links.
                It is passed by position, so the parameter may be named
                after the model.

        Returns:
            The record's name.
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

        Args:
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            The view's actions, each a method marked with `@action`. The
            permission each one asks for is checked as well.
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

        By default it answers from the `can_` settings, such as `can_edit`,
        and allows everything else.

        Args:
            action: What the user wants to do: a `Permission`, such as
                `Permission.EDIT`, or the permission an action asks for.
            request: The request, with the signed in user on
                `request.state.user`.
            record: The record the question is about, or None when it is
                about the view as a whole, such as whether its list may be
                exported.

        Returns:
            True when the user may.
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

        This runs on the list, the count, a single record, an export, a
        bulk action and the history on the Activity page, so a row can never
        leak through a path that forgot to check. A record it leaves out
        reads as Hidden where another view links to it.

        Hand the statement back unchanged for a user it leaves nothing out
        for, such as a superuser. The Activity page then shows them the
        history of deleted records too, which no scope can be checked against.

        Args:
            statement: A select of the view's model, about to run.
            request: The request, with the signed in user on
                `request.state.user`.

        Returns:
            The statement, narrowed, such as with a `where`. Unchanged by
            default.
        """
        return statement

    def _scope_for(self, request: Request) -> Scope:
        """The scope as a function, ready to hand to the repository."""
        return lambda statement: self.scope_query(statement, request=request)

    # Hooks around saves and deletes.

    async def before_save(self, context: SaveContext[M]) -> None:
        """Runs before the values are written.

        `context.values[Order.slug].set(...)` stores something other than
        what was submitted.

        Args:
            context: The record, the values about to be written, the
                session and the request.

        Raises:
            RefusedError: To refuse the save, with a message for the user.
                Name a field to put the message beside its input.
        """

    async def after_save(self, context: SaveContext[M]) -> None:
        """Runs after the flush, while the transaction is still open.

        The record has its primary key by now, and the values the database
        set in its own columns.

        Args:
            context: The record, the values written, the session and the
                request.

        Raises:
            RefusedError: To roll the save back, with a message for the
                user.
        """

    async def after_save_committed(self, context: SaveContext[M]) -> None:
        """Runs once the save has committed, such as to send an email.

        The change is stored by now, so nothing here can undo it: an error is
        written to the server's log, and the save still succeeds. The
        transaction is over, so write through a session of your own.

        Args:
            context: The record, the values written and the request.
        """

    async def before_delete(self, context: DeleteContext[M]) -> None:
        """Runs before a record is deleted.

        Args:
            context: The record, the session and the request.

        Raises:
            RefusedError: To refuse the delete, with a message for the user.
        """

    async def after_delete(self, context: DeleteContext[M]) -> None:
        """Runs after the delete, while the transaction is still open.

        Args:
            context: The record, the session and the request.

        Raises:
            RefusedError: To roll the delete back, with a message for the
                user.
        """

    async def after_delete_committed(self, context: DeleteContext[M]) -> None:
        """Runs once the delete has committed.

        As with `after_save_committed`, an error here is logged and the
        delete still stands.

        Args:
            context: The record and the request.
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
