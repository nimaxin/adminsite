from collections.abc import Mapping, Sequence
from string import Formatter
from typing import TYPE_CHECKING, Any, ClassVar, TypeGuard
from uuid import uuid4

from sqlalchemy import Select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

if TYPE_CHECKING:
    from adminsite.actions.selection import Selection
    from adminsite.audit import AuditStore
    from adminsite.views.registry import ViewRegistry

from adminsite.actions.action import Action, action_of
from adminsite.audit.actor import actor_of
from adminsite.audit.entry import AuditEntry, AuditEvent, diff
from adminsite.backends.sqlalchemy.filters import SQLFilter, filter_for
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import Scope, SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    InvalidPathError,
    PermissionDeniedError,
    RecordNotFoundError,
    RefusedError,
)
from adminsite.fields import (
    ChoiceField,
    Field,
    FieldOptions,
    FieldRegistry,
    RelationField,
    default_registry,
)
from adminsite.fields.files import UNCHANGED, FileField, NewFile
from adminsite.filters import Filter, FilterValue
from adminsite.i18n import gettext as _
from adminsite.query import CountMode, Page, Pagination, QuerySpec, Sort
from adminsite.security import Permission, permission_name
from adminsite.text import RecordValues, pluralize, snake_case
from adminsite.views.inline import Inline, InlineRow
from adminsite.views.writing import (
    DeleteContext,
    FormData,
    FormResult,
    SaveContext,
)


class ModelView:
    """How one model appears in the admin.

    Set the class attributes to describe the list and the form. Override
    the `get_` methods when the answer depends on who is asking.
    """

    model: ClassVar[type[Any]]

    name: str = ""
    label: str = ""
    label_plural: str = ""
    group: str = ""
    icon: str = ""
    display_template: str = ""

    list_display: Sequence[str] = ()
    # More columns people may add to the list from the column picker.
    list_columns: Sequence[str] = ()
    search_fields: Sequence[str] = ()
    list_filter: Sequence[str | Filter] = ()
    ordering: Sequence[str] = ()
    page_size: int = 25
    # The sizes people may switch between. Empty leaves the size fixed.
    page_sizes: Sequence[int] = ()
    count_mode: CountMode = CountMode.EXACT
    # Whether the command palette looks through this view's records.
    global_search: bool = True
    pagination: Pagination = Pagination.OFFSET

    form_fields: Sequence[str] = ()
    # What the record page shows, when that differs from the form.
    detail_fields: Sequence[str] = ()
    readonly_fields: Sequence[str] = ()
    exclude: Sequence[str] = ()

    # Columns the list does not read, such as a large JSON payload, left out
    # of its query. The record page and the form load them as usual.
    deferred_fields: Sequence[str] = ()

    # Fields that replace the ones worked out from the columns, and
    # `FieldOptions` that only change one. Each is matched by its name.
    fields: Sequence[Field | FieldOptions] = ()

    # Child records edited inside this model's form.
    inlines: Sequence[Inline] = ()

    can_create: bool = True
    can_detail: bool = True
    can_export: bool = True
    can_edit: bool = True
    can_delete: bool = True
    # Importing is off until you switch it on: it writes many records at once.
    can_import: bool = False
    import_limit: int = 10_000

    # The other views of the same admin, set when the view is registered,
    # so a link can be checked against the view of the model it points at.
    views: "ViewRegistry | None" = None

    # Set by the admin when auditing is switched on.
    audit: "AuditStore | None" = None

    def __init_subclass__(cls, model: type[Any] | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if model is not None:
            cls.model = model

    def __init__(
        self,
        inspector: SQLAlchemyInspector | None = None,
        registry: FieldRegistry | None = None,
    ) -> None:
        if not hasattr(self, "model"):
            raise AdminSiteError(
                f"{type(self).__name__} needs a model: "
                f"class {type(self).__name__}(ModelView, model=YourModel)."
            )
        self.inspector = inspector or SQLAlchemyInspector()
        self.registry = registry or default_registry
        self.schema = self.inspector.inspect(self.model)

        self.name = self.name or pluralize(snake_case(self.model.__name__))
        self.label = self.label or self.schema.label
        self.label_plural = self.label_plural or self.schema.label_plural

        self._overrides = {
            item.name: item for item in self.fields if isinstance(item, Field)
        }
        self._changes = {
            item.name: item.changes
            for item in self.fields
            if isinstance(item, FieldOptions)
        }
        self._actions = self._collect_actions()
        self._inline_views = {
            inline.name: self._build_inline_view(inline) for inline in self.inlines
        }
        self.filters: tuple[SQLFilter, ...] = self._build_filters()
        self.repository = SQLAlchemyRepository(self.model, self.inspector, self.filters)
        self._fields: dict[str, Field] = {}

    # Reading the configuration. Override these when the answer depends on
    # the request, for example to hide a column from some people.

    def get_list_display(self, request: Any = None) -> tuple[str, ...]:
        """The columns the list shows."""
        if self.list_display:
            return tuple(self.list_display)
        return self._default_paths(skip=set(self.exclude))

    def get_page_sizes(self, request: Any = None) -> tuple[int, ...]:
        """The page sizes on offer, the view's own size among them."""
        if not self.page_sizes:
            return ()
        return tuple(sorted({*self.page_sizes, self.page_size}))

    def pick_page_size(self, wanted: int | None, request: Any = None) -> int:
        """The rows per page for what someone picked.

        Only a size on offer counts, so nobody can ask for a million rows
        by editing the URL.
        """
        offered = self.get_page_sizes(request)
        if wanted in offered:
            return int(wanted or self.page_size)
        return self.page_size

    def get_column_choices(self, request: Any = None) -> tuple[str, ...]:
        """The columns the picker offers: the list's own, then the extras."""
        shown = self.get_list_display(request)
        return shown + tuple(path for path in self.list_columns if path not in shown)

    def pick_columns(
        self, picked: Sequence[str], request: Any = None
    ) -> tuple[str, ...]:
        """The columns to show for what someone picked.

        Only columns on offer count, so a column hidden from this user cannot
        be brought back by editing the URL. Picking none gives the default.
        """
        wanted = set(picked)
        chosen = tuple(
            path for path in self.get_column_choices(request) if path in wanted
        )
        return chosen or self.get_list_display(request)

    def get_search_fields(self, request: Any = None) -> tuple[str, ...]:
        """The paths the search box looks in."""
        return tuple(self.search_fields)

    def get_filters(self, request: Any = None) -> tuple[SQLFilter, ...]:
        """The filters offered beside the list."""
        return self.filters

    def get_ordering(self, request: Any = None) -> tuple[Sort, ...]:
        """The order the list starts in."""
        return tuple(Sort.parse(item) for item in self.ordering)

    def get_form_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The fields the form shows, in order."""
        if self.form_fields:
            return tuple(self.form_fields)
        return self._default_paths(
            skip=set(self.exclude) | set(self.schema.primary_key)
        )

    def get_detail_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """What the record page shows. The form's fields unless you say."""
        if self.detail_fields:
            return tuple(self.detail_fields)
        return self.get_form_fields(request, record)

    def get_deferred_fields(self, request: Any = None) -> tuple[str, ...]:
        """The columns the list leaves out of its query."""
        return tuple(self.deferred_fields)

    def get_readonly_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The fields shown but not editable, named here or by themselves.

        A primary key is readonly by its nature, but a form that names one
        means to set it, so a key stays editable unless it is named here.
        """
        named = tuple(self.readonly_fields)
        keys = set(self.schema.primary_key)
        return named + tuple(
            path
            for path in self.get_form_fields(request, record)
            if path not in named and path not in keys and self.field_for(path).readonly
        )

    def get_inlines(
        self, request: Any = None, record: Any = None
    ) -> tuple[Inline, ...]:
        """The child records edited inside the form."""
        return tuple(self.inlines)

    def inline_view(self, name: str) -> "ModelView":
        """The view that reads and writes one inline's children."""
        try:
            return self._inline_views[name]
        except KeyError:
            raise AdminSiteError(
                f"{type(self).__name__} has no inline called {name!r}."
            ) from None

    def get_load_paths(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """Everything a record page shows, so it can be loaded in one go."""
        paths = list(self.get_form_fields(request, record))
        for path in self.get_detail_fields(request, record):
            if path not in paths:
                paths.append(path)
        paths = self.loadable(paths)
        for inline in self.get_inlines(request, record):
            paths.append(inline.name)
            child = self.inline_view(inline.name)
            for path in child.get_form_fields(request):
                if path in child.schema.relations:
                    paths.append(f"{inline.name}.{path}")
        return tuple(paths)

    def loadable(self, paths: Sequence[str]) -> list[str]:
        """The paths a query can load, with what computed fields read added.

        A computed field is worked out in Python, so it is not loaded, but
        whatever it reads is, or a list of 25 would cost 25 queries.
        """
        wanted: list[str] = []
        for path in paths:
            item = self.field_for(path)
            if item.stored:
                wanted.append(path)
                continue
            for needed in getattr(item, "needs", ()):
                if needed not in wanted:
                    wanted.append(needed)
        return wanted

    def sortable(self, path: str) -> bool:
        """Whether a list can be sorted by this column."""
        return self.field_for(path).stored

    def readable_paths(self, request: Any = None) -> tuple[str, ...]:
        """Every path this user may read on some page of the view."""
        paths = list(self.get_column_choices(request))
        for path in (*self.get_detail_fields(request), *self.get_form_fields(request)):
            if path not in paths:
                paths.append(path)
        return tuple(paths)

    def _build_inline_view(self, inline: Inline) -> "ModelView":
        relation = self.schema.relation_named(inline.name)
        if not relation.collection:
            raise AdminSiteError(
                f"{type(self).__name__}.inlines names {inline.name!r}, which "
                "holds one record. An inline needs a relationship holding many."
            )
        # The link back to the parent is set by the relationship itself, so
        # it never appears as an input in the child rows.
        target = self.inspector.inspect(relation.target)
        back_links = tuple(
            name
            for name, found in target.relations.items()
            if found.target is self.model and not found.collection
        )
        namespace: dict[str, Any] = {
            "model": relation.target,
            "name": f"{self.name}__{inline.name}",
            "form_fields": tuple(inline.fields),
            "readonly_fields": tuple(inline.readonly_fields),
            "exclude": back_links,
            "display_template": inline.display_template,
        }
        view_class = type(f"{relation.target.__name__}Inline", (ModelView,), namespace)
        built: ModelView = view_class(self.inspector, self.registry)
        return built

    def _default_paths(self, skip: set[str]) -> tuple[str, ...]:
        """Every column in order, with a foreign key shown as its link.

        A form offering `customer_id` as a number box is no use to anyone,
        so the key column is swapped for the relationship it belongs to,
        which gets a proper picker and shows the customer's name.
        """
        links = {
            column: relation.name
            for relation in self.schema.relations.values()
            if not relation.collection
            for column in relation.local_columns
        }
        paths: list[str] = []
        for name in self.schema.fields:
            if name in skip:
                continue
            path = links.get(name, name)
            if path not in paths and path not in skip:
                paths.append(path)
        return tuple(paths)

    # Turning paths into fields and values.

    def field_for(self, path: str) -> Field:
        """The field used to show and edit whatever the path points at."""
        known = self._fields.get(path)
        if known is not None:
            return known

        override = self._overrides.get(path)
        if override is not None:
            self._fields[path] = override
            return override

        resolved = self.inspector.resolve(self.model, path)
        changes = self._changes.get(path, {})
        try:
            if resolved.field is not None:
                options: dict[str, Any] = {"label": resolved.label, **changes}
                built: Field = self.registry.build(resolved.field, **options)
            else:
                built = RelationField.from_relation(resolved.relations[-1], **changes)
        except TypeError as error:
            raise AdminSiteError(
                f"FieldOptions({path!r}) in {type(self).__name__}.fields holds "
                f"something the field does not take: {error}"
            ) from error
        self._fields[path] = built
        return built

    def label_for(self, path: str) -> str:
        """The column heading for a path.

        A path through a link names the link as well, so `customer.name`
        reads Customer name rather than a bare Name.
        """
        label = self.field_for(path).label
        named = "label" in self._changes.get(path, {})
        if named or path in self._overrides or "." not in path:
            return label
        resolved = self.inspector.resolve(self.model, path)
        if resolved.field is None:
            return label
        owner = resolved.relations[-1].label
        return f"{owner} {label[:1].lower()}{label[1:]}"

    def value_at(self, record: Any, path: str) -> Any:
        """Read the value a path points at, following links as it goes."""
        value: Any = record
        for part in path.split("."):
            if value is None:
                return None
            if isinstance(value, list | tuple | set):
                return [getattr(item, part, None) for item in value]
            value = getattr(value, part, None)
        return value

    def display(self, record: Any, path: str) -> str:
        """The text shown in a cell."""
        item = self.field_for(path)
        return item.text_for(record, self.value_at(record, path))

    def title_of(self, record: Any) -> str:
        """Name a record, for a heading or a link to it."""
        if self.display_template:
            return self.display_template.format_map(RecordValues(record))
        return str(record)

    def identity_of(self, record: Any) -> str:
        """The key of a record, as it appears in a URL."""
        return self.repository.identity_of(record)

    # Building a read.

    def build_spec(
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
        wanted = tuple(self.loadable(tuple(paths) or self.get_list_display(request)))
        spec = QuerySpec(
            paths=wanted,
            defer=self._deferred(request, wanted),
            search=search,
            search_paths=self.get_search_fields(request),
            filters=tuple(filters),
            sort=tuple(sort) or self.get_ordering(request),
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
        keep = set(loaded) | set(self.schema.primary_key) | self._named_in_title()
        wanted = []
        for path in self.get_deferred_fields(request):
            if path not in self.schema.fields:
                raise AdminSiteError(
                    f"{type(self).__name__}.deferred_fields names {path!r}, "
                    f"which is not a column of {self.model.__name__}."
                )
            if path not in keep:
                wanted.append(path)
        return tuple(wanted)

    def _named_in_title(self) -> set[str]:
        """The columns `display_template` reads, which every row needs."""
        if not self.display_template:
            return set()
        return {
            name
            for _text, name, _spec, _conversion in Formatter().parse(
                self.display_template
            )
            if name
        }

    # Actions.

    def get_actions(self, request: Any = None) -> tuple[Action, ...]:
        """The actions this view offers, in the order they appear."""
        return tuple(self._actions.values())

    def actions_on(self, target: str, request: Any = None) -> tuple[Action, ...]:
        """The actions of one kind: over a selection, a record or the view."""
        return tuple(item for item in self.get_actions(request) if item.on == target)

    def action_named(self, name: str, request: Any = None) -> Action:
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

    def parse_action_inputs(self, found: Action, data: FormData) -> FormResult:
        """Read the values an action asked for, checked like form fields."""
        result = FormResult()
        for item in found.inputs:
            raw = data.get(item.name)
            try:
                if _holds_many(item):
                    result.values[item.name] = item.parse_many(_as_list(raw))
                else:
                    result.values[item.name] = item.parse(_as_text(raw))
            except FieldValidationError as error:
                result.errors[item.name] = error.message
        return result

    async def run_record_action(
        self,
        found: Action,
        record: Any,
        session: SessionAdapter,
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action on one record, and say what to tell the user."""
        await self.ensure(found.permission, request=request, record=record)
        key, title = self.identity_of(record), self.title_of(record)

        handler = getattr(self, found.method)
        answer = await handler(record, session, **(values or {}))
        if isinstance(answer, Response):
            return answer
        text = str(answer) if answer else _("{action} done.", action=found.label)

        self._audit(
            session,
            [
                AuditEntry(
                    view=self.name,
                    record_key=key,
                    record_title=title,
                    event=AuditEvent.ACTION,
                    action=found.label,
                    **actor_of(request),
                    message=text,
                )
            ],
        )
        return text

    async def run_view_action(
        self,
        found: Action,
        session: SessionAdapter,
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action that acts on the view, not on any record."""
        await self.ensure(found.permission, request=request)
        handler = getattr(self, found.method)
        answer = await handler(session, **(values or {}))
        if isinstance(answer, Response):
            return answer
        return str(answer) if answer else _("{action} done.", action=found.label)

    async def run_action(
        self,
        found: Action,
        selection: "Selection",
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action over a selection, and say what to tell the user.

        The values the action asked for are passed to its method by name.
        """
        await self.ensure(found.permission, request=request)
        # Read the keys before the action runs: afterwards the rows may no
        # longer match the filter they were chosen by.
        keys = await selection.covered_keys() if self.audit is not None else []

        handler = getattr(self, found.method)
        message = await handler(selection, **(values or {}))
        if isinstance(message, Response):
            return message
        text = str(message) if message else _("{action} done.", action=found.label)

        batch = str(uuid4())
        actor = actor_of(request)
        self._audit(
            selection.session,
            [
                AuditEntry(
                    view=self.name,
                    record_key=key,
                    event=AuditEvent.ACTION,
                    action=found.label,
                    batch=batch,
                    changes=selection.changes.get(key, {}),
                    **actor,
                    message=text,
                )
                for key in keys
            ],
        )
        return text

    def _collect_actions(self) -> dict[str, Action]:
        found: dict[str, Action] = {}
        for name in dir(type(self)):
            marked = action_of(getattr(type(self), name, None))
            if marked is not None:
                found[marked.name] = marked
        return found

    # Permissions. Four levels: the view, the action, the field and the row.

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        """Whether the current user may do this, to this record."""
        name = permission_name(action)
        if name == Permission.CREATE:
            return self.can_create
        if name == Permission.EDIT:
            return self.can_edit
        if name == Permission.DELETE:
            return self.can_delete
        if name == Permission.IMPORT:
            return self.can_import
        if name == Permission.DETAIL:
            return self.can_detail
        if name == Permission.EXPORT:
            return self.can_export
        return True

    async def ensure(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> None:
        """Raise unless the current user may do this."""
        if not await self.allows(action, request=request, record=record):
            raise PermissionDeniedError(permission_name(action), self.label_plural)

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        """Narrow every read to the rows this user may see.

        This runs on the list, the count, a single record, an export and a
        bulk action, so a row can never leak through a path that forgot to
        check.
        """
        return statement

    def scope_for(self, request: Any = None) -> Scope:
        """The scope as a function, ready to hand to the repository."""
        return lambda statement: self.scope_query(statement, request=request)

    # Reading records.

    async def fetch_page(
        self, session: SessionAdapter, spec: QuerySpec, *, request: Any = None
    ) -> Page:
        """Read one page, within the scope and after a permission check."""
        await self.ensure(Permission.VIEW, request=request)
        return await self.repository.list(session, spec, self.scope_for(request))

    async def fetch_record(
        self,
        session: SessionAdapter,
        key: Any,
        *,
        paths: Sequence[str] = (),
        request: Any = None,
    ) -> Any | None:
        """Load one record, or nothing if it is missing or out of scope."""
        await self.ensure(Permission.VIEW, request=request)
        return await self.repository.get(
            session, key, tuple(paths), self.scope_for(request)
        )

    # Writing.

    def parse_form(
        self,
        data: FormData,
        *,
        record: Any = None,
        request: Any = None,
    ) -> FormResult:
        """Read a submitted form into values, collecting any messages."""
        result = FormResult()
        readonly = set(self.get_readonly_fields(request, record))

        for path in self.get_form_fields(request, record):
            if path in readonly or not self.field_for(path).stored:
                continue
            item = self.field_for(path)
            raw = data.get(path)
            try:
                if isinstance(item, FileField):
                    choice = item.parse_upload(
                        raw,
                        remove=data.get(f"{path}-remove") is not None,
                        has_file=bool(
                            record is not None and self.value_at(record, path)
                        ),
                    )
                    if choice is not UNCHANGED:
                        result.values[path] = choice
                elif _holds_many(item):
                    result.values[path] = item.parse_many(_as_list(raw))
                else:
                    result.values[path] = item.parse(_as_text(raw))
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
        child = self.inline_view(inline.name)
        readonly = set(child.get_readonly_fields(request))
        paths = [
            path for path in child.get_form_fields(request) if path not in readonly
        ]
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
                    item = child.field_for(path)
                    try:
                        row.values[path] = item.parse(_as_text(raw[path]))
                    except FieldValidationError as error:
                        errors[inline.input_name(index, path)] = error.message
            rows.append(row)
        return rows

    async def save(
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
        await self.ensure(
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
                target = record if record is not None else self.repository.model()
                context = SaveContext(
                    session=session,
                    record=target,
                    values=dict(values),
                    created=created,
                    request=request,
                )
                await self.before_save(context)
                # Whatever the hook left in context.values is what is stored.
                values = context.values

                auditing = self.audit is not None
                before = (
                    self.snapshot(target, list(values))
                    if auditing and not created
                    else {}
                )
                async with session.no_autoflush():
                    await self.repository.apply_values(session, target, values)
                    await self._apply_inlines(
                        session, target, inline_rows or {}, request
                    )
                if created:
                    await session.add(target)
                await session.flush()

                await self.after_save(context)
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
                item = self.field_for(path)
                if not isinstance(item, FileField):
                    continue
                if isinstance(value, NewFile):
                    key = await item.storage.save(value.upload)
                    stored.append((item, key))
                    ready[path] = key
                old = self.value_at(record, path) if record is not None else None
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

    async def delete(
        self, session: SessionAdapter, record: Any, *, request: Any = None
    ) -> None:
        """Delete a record, running the hooks in one transaction."""
        await self.ensure(Permission.DELETE, request=request, record=record)
        try:
            async with session.transaction():
                context = DeleteContext(session=session, record=record, request=request)
                await self.before_delete(context)
                auditing = self.audit is not None
                before = (
                    self.snapshot(record, self.get_form_fields(request, record))
                    if auditing
                    else {}
                )
                key, title = self.identity_of(record), self.title_of(record)
                await self.repository.delete(session, record)
                await self.after_delete(context)
                if not auditing:
                    return
                self._audit(
                    session,
                    [
                        AuditEntry(
                            view=self.name,
                            record_key=key,
                            record_title=title,
                            event=AuditEvent.DELETED,
                            changes={
                                name: (value, None)
                                for name, value in before.items()
                                if value not in ("", None)
                            },
                            **actor_of(request),
                        )
                    ],
                )
        except IntegrityError as error:
            raise RefusedError(
                _(
                    "This {thing} cannot be deleted, because other records "
                    "still refer to it.",
                    thing=self.label.lower(),
                )
            ) from error

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
            child_view = self.inline_view(inline.name)
            children = getattr(parent, inline.name)
            by_key = {child_view.identity_of(child): child for child in children}
            for row in rows:
                if row.is_new:
                    if not row.delete:
                        child = child_view.model()
                        values = await self._resolve_links(
                            session, row.values, request, fields_of=child_view
                        )
                        await child_view.repository.apply_values(session, child, values)
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
                await child_view.repository.apply_values(session, existing, values)

    async def _resolve_links(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        request: Any,
        *,
        fields_of: "ModelView | None" = None,
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
            item = owner.field_for(path)
            if not isinstance(item, RelationField) or self.views is None:
                continue
            if value is None or value == "" or value == []:
                continue
            target = self.views.for_model(item.target)
            if target is None:
                continue
            keys = value if isinstance(value, list | tuple | set) else [value]
            found = []
            for key in keys:
                record = key
                if not isinstance(record, item.target):
                    record = await self._linked_through(target, session, key, request)
                if record is None:
                    raise RefusedError(_("Choose a record."), field=path)
                found.append(record)
            resolved[path] = found if item.collection else found[0]
        return resolved

    async def _linked_through(
        self, target: "ModelView", session: SessionAdapter, key: Any, request: Any
    ) -> Any | None:
        """One linked record, if the target's view lets this user see it."""
        wanted = key
        if isinstance(key, str) and len(target.schema.primary_key) > 1:
            wanted = tuple(key.split(","))
        try:
            return await target.fetch_record(session, wanted, request=request)
        except (PermissionDeniedError, InvalidPathError):
            return None

    def snapshot(self, record: Any, paths: Sequence[str]) -> dict[str, Any]:
        """What a record shows for these paths, as the history records it.

        Only what is already loaded is read. Touching anything else would
        start a lazy load, which an async session cannot do.
        """
        state = sqlalchemy_inspect(record, raiseerr=False)
        unloaded = state.unloaded if state is not None else set()
        return {
            path: self.display(record, path)
            for path in paths
            if path.split(".", 1)[0] not in unloaded
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
        if self.audit is None:
            return
        after = self.snapshot(record, paths)
        changes = (
            {name: (None, value) for name, value in after.items() if value}
            if created
            else diff(before, after)
        )
        if not changes and not created:
            return
        self._audit(
            session,
            [
                AuditEntry(
                    view=self.name,
                    record_key=self.identity_of(record),
                    record_title=self.title_of(record),
                    event=AuditEvent.CREATED if created else AuditEvent.UPDATED,
                    changes=changes,
                    **actor_of(request),
                )
            ],
        )

    def _audit(self, session: SessionAdapter, entries: Sequence[AuditEntry]) -> None:
        """Write entries once the transaction they describe has committed."""
        log = self.audit
        if log is None or not entries:
            return

        async def write() -> None:
            await log.record(entries)

        session.after_commit(write)

    async def before_save(self, context: SaveContext) -> None:
        """Runs before the values are written.

        Change `context.values`, or `context.set("slug", ...)`, to store
        something other than what was submitted. Raise `RefusedError` to
        refuse the save, naming a field to put the message beside it.
        """

    async def after_save(self, context: SaveContext) -> None:
        """Runs after the flush, while the transaction is still open."""

    async def before_delete(self, context: DeleteContext) -> None:
        """Runs before a record is deleted. Raise to refuse the delete."""

    async def after_delete(self, context: DeleteContext) -> None:
        """Runs after the delete, while the transaction is still open."""

    def _build_filters(self) -> tuple[SQLFilter, ...]:
        repository = SQLAlchemyRepository(self.model, self.inspector)
        built: list[SQLFilter] = []
        for item in self.list_filter:
            if isinstance(item, str):
                built.append(filter_for(repository, item))
            elif isinstance(item, SQLFilter):
                built.append(item)
            else:
                raise AdminSiteError(
                    f"{type(self).__name__}.list_filter takes paths or "
                    f"SQLFilter instances, not {type(item).__name__}."
                )
        return tuple(built)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model.__name__})"


def _as_text(raw: str | Sequence[str] | None) -> str | None:
    """Read one value out of form data, which may hold several."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return raw[0] if raw else None


def _holds_many(item: Field) -> TypeGuard[RelationField | ChoiceField]:
    """Whether the input sends several values rather than one."""
    if isinstance(item, RelationField):
        return item.collection
    return isinstance(item, ChoiceField) and item.multiple


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
