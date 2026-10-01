import types
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import replace
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
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
from adminsite.audit.inputs import HIDDEN, recorded_inputs
from adminsite.audit.store import record_or_warn
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
    describe,
    path_of,
)
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    IntegrityError,
    NotAModelError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
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
from adminsite.fields.documents import DocumentError
from adminsite.fields.files import UNCHANGED, FileField, NewFile, UploadField
from adminsite.i18n import gettext as _
from adminsite.messages import Message
from adminsite.query import CountMode, Pagination
from adminsite.schema import RelationDirection
from adminsite.security import Permission, RequestAction, permission_name
from adminsite.text import (
    RecordValues,
    names_itself,
    pluralize,
    snake_case,
)
from adminsite.views.checks import (
    check_title,
)
from adminsite.views.inline import Inline, InlineRow
from adminsite.views.links import Links
from adminsite.views.naming import name_all_linked
from adminsite.views.pages import PageFields
from adminsite.views.reading import Reader
from adminsite.views.settings import SettingsReader, default_paths
from adminsite.views.view_fields import ViewFields
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

        self._actions = self._collect_actions()
        self._inline_views = {
            inline.name: self._build_inline_view(inline, f"inlines[{index}]")
            for index, inline in enumerate(
                self._settings.entries("inlines", self.inlines)
            )
        }
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

    def _inline_view(self, name: str) -> "ModelView[Any]":
        """The view that reads and writes one inline's children."""
        try:
            return self._inline_views[name]
        except KeyError:
            raise AdminSiteError(
                f"{type(self).__name__} has no inline called {name!r}."
            ) from None

    def _build_inline_view(self, inline: Inline, setting: str) -> "ModelView[Any]":
        self._settings.converted(setting, inline.relation, path_of)
        self._settings.check(setting, inline.name, self.model, "paths")
        relation = self._schema.relation_named(inline.name)
        if not relation.collection:
            raise AdminSiteError(
                f"{type(self).__name__}.inlines names {inline.name!r}, which "
                "holds one record. An inline needs a relationship holding many."
            )
        if inline.record_title:
            check_title(
                f"{type(self).__name__}.{setting}.record_title: "
                f"{describe(inline.record_title)}",
                inline.record_title,
                relation.target,
                self._inspector,
            )
        # The relationship fills in the child's columns it joins on, and so
        # every link of the child made of them, the link back among them. None
        # of them appears in the child rows. Another link to the parent does.
        target = self._inspector.inspect(relation.target)
        joined = set(relation.remote_columns)
        filled = [
            *relation.remote_columns,
            *(
                found.name
                for found in target.relations.values()
                if found.direction is RelationDirection.MANY_TO_ONE
                and found.local_columns
                and set(found.local_columns) <= joined
            ),
        ]
        # Checked here, so a mistake names the parent's setting. The child view
        # takes the entries as they are, fields with their options included.
        entries = self._settings.entries(f"{setting}.fields", inline.fields)
        named = self._settings.paths(
            f"{setting}.fields",
            [
                entry.column if isinstance(entry, Field) else entry
                for entry in entries
                if not (isinstance(entry, Field) and entry.form_only)
            ],
            relation.target,
        )
        # Left off only where the rows show it, since an exclude list names
        # only fields the view shows.
        shown = named if entries else default_paths(target)
        filled = [name for name in filled if name in shown]

        def can_access_field(
            child: ModelView[Any], request: Any, field: BaseField, action: RequestAction
        ) -> bool:
            # This view answers for its children, asked about each field by
            # its path from here, such as items.unit_price.
            asked = copy(field)
            asked.name = f"{inline.name}.{field.name}"
            return self.can_access_field(request, asked, action)

        namespace: dict[str, Any] = {
            "model": relation.target,
            "name": f"{self.name}__{inline.name}",
            "fields": list(entries),
            "exclude_fields_from_detail": filled,
            "exclude_fields_from_create": filled,
            "exclude_fields_from_edit": filled,
            "record_title": inline.record_title,
            "can_access_field": can_access_field,
        }
        child_class = type(f"{relation.target.__name__}Inline", (ModelView,), namespace)
        try:
            built: ModelView[Any] = child_class(self._inspector, self._registry)
        except AdminSiteError as error:
            # The child's class is made here, so a mistake names the setting
            # it was written in rather than a class nobody wrote.
            said = str(error)
            generated = f"{child_class.__name__}."
            if said.startswith(generated):
                said = f".{said.removeprefix(generated)}"
            else:
                said = f": {said}"
            raise AdminSiteError(f"{type(self).__name__}.{setting}{said}") from None
        return built

    # Turning paths into fields and values.

    async def form_only_values(
        self, session: SessionAdapter, record: M | None, *, request: Request
    ) -> Mapping[str, Any]:
        """The values form-only fields start from, by name.

        Nothing by default, so they start empty. Answer with, say, settings
        kept as rows of another table, to edit them as one value; `record`
        is None on the form for a new record.
        """
        return {}

    def _masked(self, changes: Mapping[str, Change]) -> dict[str, Change]:
        """Changes as the audit log keeps them: a secret's values as ***.

        A field given `secret=True`, or named like `password_hash` or
        `api_key`, is kept as *** before and after, so a change to it
        still shows, and what it holds never does.
        """
        kept = {}
        for path, (before, after) in changes.items():
            if self._fields.secret(path):
                before = HIDDEN if before not in (None, "") else before
                after = HIDDEN if after not in (None, "") else after
            kept[path] = (before, after)
        return kept

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
        for path in self._pages.form_fields(request):
            item = self._fields.field_for(path)
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
        return tuple(
            self._asking_all(item)
            for item in self.get_actions(request)
            if item.on == target
        )

    def _asking_all(self, item: Action) -> Action:
        """The action, asking for every value its method needs.

        `get_actions` may replace an action's inputs, offer it again under
        another name, or build one by hand. An input given there takes the
        place of the one of its name, and the method's other parameters are
        still asked for.
        """
        known = self._actions.get(item.name)
        if item is known:
            return item
        if known is None or known.method != item.method:
            known = next(
                (one for one in self._actions.values() if one.method == item.method),
                None,
            )
        if known is not None:
            call, asked = known.call, known.inputs
        else:
            call = self._read_call(item)
            asked = (*item.inputs, *call.inputs)
        given = {one.name: one for one in item.inputs}
        inputs = (*[given.pop(one.name, one) for one in asked], *given.values())
        return replace(item, inputs=inputs, call=call)

    def _find_action(self, name: str, request: Any = None) -> Action | None:
        """The action of this name offered to this request, or None.

        It looks through `get_actions`, so an action built for this request,
        with its own choices or labels, is the one that runs. A mistake in
        the action itself is raised, never taken for a missing one.
        """
        for item in self.get_actions(request):
            if item.name == name:
                return self._asking_all(item)
        # No falling back to the class's own list: an action `get_actions`
        # leaves out for this user is not offered, so it cannot be run by
        # asking for it by name either.
        return None

    def _action_named(self, name: str, request: Any = None) -> Action:
        """Find an action by name, or say it is not there."""
        found = self._find_action(name, request)
        if found is None:
            raise AdminSiteError(
                f"{type(self).__name__} has no action called {name!r}."
            )
        return found

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
            self._fields.identity_of(record),
            self.get_record_title(record),
        )
        auditing = self._audit_log is not None
        paths = list(self._pages.form_fields(request, record)) if auditing else []
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
            label = found.input_label(item)
            records = [
                await self._input_record(item, session, key, request, label)
                for key in keys
            ]
            given[item.name] = records if item.collection else records[0]
        return given

    async def _input_record(
        self,
        item: RelationField,
        session: SessionAdapter,
        key: Any,
        request: Any,
        label: str,
    ) -> Any:
        """The record a link input names, or a refusal naming the input."""
        target = self._views.for_relation(item) if self._views is not None else None
        if target is not None:
            record = await self._links.linked_through(target, session, key, request)
        else:
            record = await self._links.linked_directly(item, session, key)
        if record is None:
            raise RefusedError(
                _("{field}: choose from the records offered.", field=label),
                field=item.name,
            )
        return record

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
        readonly = set(self._pages.readonly_paths(request, record))
        draft: Any = None

        for path in self._pages.form_fields(request, record):
            item = self._fields.field_for(path)
            if path in readonly:
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
                            record is not None and self._fields.value_at(record, path)
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
                inline, data, result.errors, request, record
            )
        return result

    def _parse_inline(
        self,
        inline: Inline,
        data: FormData,
        errors: dict[str, str],
        request: Any,
        record: Any = None,
    ) -> list[InlineRow]:
        """Read one inline's rows back from the form.

        A new row reads the create page's fields and an existing child the
        edit page's, less those the child or this view locks, as a view's
        form does.
        """
        child = self._inline_view(inline.name)
        locked = self._pages.inline_readonly(inline, request, record)
        children = {
            child._fields.identity_of(found): found
            for found in getattr(record, inline.name, None) or ()
        }
        try:
            count = int(_as_text(data.get(f"{inline.name}-count")) or 0)
        except ValueError:
            count = 0

        rows: list[InlineRow] = []
        for index in range(count):
            key = (_as_text(data.get(inline.input_name(index, "key"))) or "").strip()
            delete = data.get(inline.input_name(index, "delete")) is not None
            found = children.get(key) if key else None
            readonly = locked | set(child._pages.readonly_paths(request, found))
            paths = [
                path
                for path in child._pages.form_fields(request, found)
                if path not in readonly
            ]
            raw = {path: data.get(inline.input_name(index, path)) for path in paths}
            if not key and not any(_as_text(value) for value in raw.values()):
                # An empty row left over from "add another".
                continue
            row = InlineRow(key=key, delete=delete, index=index)
            if not delete:
                for path in paths:
                    item = child._fields.field_for(path)
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
                values = await self._links.resolve(session, values, request)
                target = record if record is not None else self._repository.model()
                context = SaveContext(
                    session=session,
                    record=target,
                    values=SaveValues(target, values, form_only=self._fields.form_only),
                    created=created,
                    request=request,
                )
                await self.before_save(context)
                # Whatever the hook left in context.values is what is stored,
                # apart from form-only values, which the hooks store themselves.
                values = {
                    path: value
                    for path, value in stored_values(context.values).items()
                    if not self._fields.form_only(path)
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
                await _flush_and_reload(session, target)

                await self.after_save(context)
                # A change the hook made to the record is flushed here rather
                # than at the commit, so what that flush sets is read too.
                await _flush_and_reload(session, target)
                session.after_commit(partial(self.after_save_committed, context))
                self._audit_save(
                    session, target, before, list(values), request, created=created
                )
        except IntegrityError as error:
            await self._discard_files(stored)
            raise RefusedError(
                _(
                    "This {thing} could not be saved. {reason}",
                    thing=self.label.lower(),
                    reason=str(error),
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
            item = self._fields.field_for(path)
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
                item = self._fields.field_for(path)
                if not isinstance(item, FileField):
                    continue
                if isinstance(value, NewFile):
                    key = await item.storage.save(value.upload)
                    stored.append((item, key))
                    ready[path] = key
                old = (
                    self._fields.value_at(record, path) if record is not None else None
                )
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
        except _ReferredToError as error:
            raise RefusedError(
                _(
                    "This {thing} cannot be deleted, because other records "
                    "still refer to it.",
                    thing=self.label.lower(),
                )
            ) from error
        except IntegrityError as error:
            # Refused for something else, such as a row a hook wrote.
            raise RefusedError(
                _(
                    "This {thing} could not be deleted. {reason}",
                    thing=self.label.lower(),
                    reason=str(error),
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
            self._snapshot(record, self._pages.form_fields(request, record))
            if auditing
            else {}
        )
        key, title = self._fields.identity_of(record), self.get_record_title(record)
        # What the hooks left unwritten goes first, so that a refusal of the
        # delete itself is the only one put down to the records referring.
        await session.flush()
        try:
            await self._repository.delete(session, record)
        except IntegrityError as error:
            raise _ReferredToError(str(error)) from error
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
            paths=self._pages.loadable(
                self._pages.form_fields(request, page=RequestAction.EDIT)
            )
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
            # The database refusing anything but the delete itself, such as a
            # row a hook wrote, goes on to the action's own handling, which
            # says Delete was not done and why, as it does at the commit.
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
            except _ReferredToError as error:
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
            by_key = {
                child_view._fields.identity_of(child): child for child in children
            }
            for row in rows:
                if row.is_new:
                    if not row.delete:
                        child = child_view.model()
                        values = await self._links.row_values(
                            session, inline, row, request
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
                values = await self._links.row_values(session, inline, row, request)
                await child_view._repository.apply_values(session, existing, values)

    def _snapshot(self, record: Any, paths: Sequence[str]) -> dict[str, Any]:
        """What a record shows for these paths, as the history records it.

        Only what is already loaded is read. Touching anything else would
        start a lazy load, which an async session cannot do.
        """
        state = sqlalchemy_inspect(record, raiseerr=False)
        unloaded = state.unloaded if state is not None else set()
        return {
            path: self._fields.display(record, path)
            for path in paths
            if path.split(".", 1)[0] not in unloaded
            and not self._fields.form_only(path)
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
                    record_key=self._fields.identity_of(record),
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


async def _flush_and_reload(session: SessionAdapter, record: Any) -> None:
    """Flush, then read back what the database set, such as an onupdate time."""
    await session.flush()
    # Read while the transaction can still load it, so the hooks and the
    # pages after the commit can read it too.
    expired = sqlalchemy_inspect(record, raiseerr=True).expired_attributes
    if expired:
        await session.refresh(record, sorted(expired))


class _ReferredToError(IntegrityError):
    """The database refused a delete, since other records refer to the record."""
