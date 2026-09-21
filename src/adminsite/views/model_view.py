from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from sqlalchemy import Select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from adminsite.actions.selection import Selection
    from adminsite.audit import AuditLog

from adminsite.actions.action import Action, action_of
from adminsite.audit.entry import AuditEntry, AuditEvent, diff
from adminsite.backends.sqlalchemy.filters import SQLFilter, filter_for
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import Scope, SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    PermissionDeniedError,
    RefusedError,
)
from adminsite.fields import Field, FieldRegistry, RelationField, default_registry
from adminsite.filters import Filter, FilterValue
from adminsite.query import CountMode, Page, QuerySpec, Sort
from adminsite.security import Permission, permission_name
from adminsite.text import RecordValues, pluralize, snake_case
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
    search_fields: Sequence[str] = ()
    list_filter: Sequence[str | Filter] = ()
    ordering: Sequence[str] = ()
    page_size: int = 25
    count_mode: CountMode = CountMode.EXACT

    form_fields: Sequence[str] = ()
    readonly_fields: Sequence[str] = ()
    exclude: Sequence[str] = ()

    # Fields that replace the ones worked out from the columns. Each one
    # is matched to a path by its name.
    fields: Sequence[Field] = ()

    can_create: bool = True
    can_edit: bool = True
    can_delete: bool = True

    # Set by the admin when auditing is switched on.
    audit: "AuditLog | None" = None

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

        self._overrides = {field.name: field for field in self.fields}
        self._actions = self._collect_actions()
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

    def get_readonly_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The fields shown but not editable."""
        return tuple(self.readonly_fields)

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
        if resolved.field is not None:
            built: Field = self.registry.build(resolved.field, label=resolved.label)
        else:
            built = RelationField.from_relation(resolved.relations[-1])
        self._fields[path] = built
        return built

    def label_for(self, path: str) -> str:
        """The column heading for a path.

        A path through a link names the link as well, so `customer.name`
        reads Customer name rather than a bare Name.
        """
        label = self.field_for(path).label
        if path in self._overrides or "." not in path:
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
        return self.field_for(path).display(self.value_at(record, path))

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
    ) -> QuerySpec:
        """Describe the read this view wants, page by page."""
        wanted = tuple(paths) or self.get_list_display(request)
        spec = QuerySpec(
            paths=wanted,
            search=search,
            search_paths=self.get_search_fields(request),
            filters=tuple(filters),
            sort=tuple(sort) or self.get_ordering(request),
            limit=self.page_size,
            count=self.count_mode,
        )
        return spec.page(page)

    # Actions.

    def get_actions(self, request: Any = None) -> tuple[Action, ...]:
        """The bulk actions this view offers, in the order they appear."""
        return tuple(self._actions.values())

    def action_named(self, name: str) -> Action:
        """Find an action by name, or say it is not there."""
        try:
            return self._actions[name]
        except KeyError:
            raise AdminSiteError(
                f"{type(self).__name__} has no action called {name!r}."
            ) from None

    def parse_action_inputs(self, found: Action, data: FormData) -> FormResult:
        """Read the values an action asked for, checked like form fields."""
        result = FormResult()
        for item in found.inputs:
            try:
                result.values[item.name] = item.parse(_as_text(data.get(item.name)))
            except FieldValidationError as error:
                result.errors[item.name] = error.message
        return result

    async def run_action(
        self,
        found: Action,
        selection: "Selection",
        *,
        request: Any = None,
        values: Mapping[str, Any] | None = None,
    ) -> str:
        """Run an action and return what to tell the user.

        The values the action asked for are passed to its method by name.
        """
        await self.ensure(found.permission, request=request)
        # Read the keys before the action runs: afterwards the rows may no
        # longer match the filter they were chosen by.
        keys = await selection.covered_keys() if self.audit is not None else []

        handler = getattr(self, found.method)
        message = await handler(selection, **(values or {}))
        text = str(message) if message else f"{found.label} done."

        batch = str(uuid4())
        user = user_of(request)
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
                    user=user,
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
            if path in readonly:
                continue
            item = self.field_for(path)
            raw = data.get(path)
            try:
                if isinstance(item, RelationField) and item.collection:
                    result.values[path] = item.parse_many(_as_list(raw))
                else:
                    result.values[path] = item.parse(_as_text(raw))
            except FieldValidationError as error:
                result.errors[path] = error.message

        return result

    async def save(
        self,
        session: SessionAdapter,
        values: Mapping[str, Any],
        *,
        record: Any = None,
        request: Any = None,
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
        try:
            async with session.transaction():
                target = record if record is not None else self.repository.model()
                context = SaveContext(
                    session=session,
                    record=target,
                    values=values,
                    created=created,
                    request=request,
                )
                await self.before_save(context)

                auditing = self.audit is not None
                before = (
                    self.snapshot(target, list(values))
                    if auditing and not created
                    else {}
                )
                await self.repository.apply_values(session, target, values)
                if created:
                    await session.add(target)
                await session.flush()

                await self.after_save(context)
                self._audit_save(
                    session, target, before, list(values), request, created=created
                )
        except IntegrityError as error:
            raise RefusedError(
                f"This {self.label.lower()} could not be saved, because it "
                "clashes with another record. A value that must be unique "
                "may already be taken."
            ) from error
        return target

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
                            user=user_of(request),
                        )
                    ],
                )
        except IntegrityError as error:
            raise RefusedError(
                f"This {self.label.lower()} cannot be deleted, because other "
                "records still refer to it."
            ) from error

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
                    user=user_of(request),
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
        """Runs before the values are written. Raise to refuse the save."""

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


def _as_list(raw: str | Sequence[str] | None) -> list[str]:
    """Read every value out of form data."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def user_of(request: Any) -> str | None:
    """Who is signed in, as the audit log writes it down."""
    scope = getattr(request, "scope", None)
    user = scope.get("user_record") if isinstance(scope, dict) else None
    return str(user) if user is not None else None
