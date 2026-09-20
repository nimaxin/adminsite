from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from sqlalchemy import Select

from adminsite.backends.sqlalchemy.filters import SQLFilter, filter_for
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import Scope, SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    PermissionDeniedError,
)
from adminsite.fields import Field, FieldRegistry, RelationField, default_registry
from adminsite.filters import Filter, FilterValue
from adminsite.query import CountMode, Page, QuerySpec, Sort
from adminsite.security import Action, action_name
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
        self.filters: tuple[SQLFilter, ...] = self._build_filters()
        self.repository = SQLAlchemyRepository(self.model, self.inspector, self.filters)
        self._fields: dict[str, Field] = {}

    # Reading the configuration. Override these when the answer depends on
    # the request, for example to hide a column from some people.

    def get_list_display(self, request: Any = None) -> tuple[str, ...]:
        """The columns the list shows."""
        if self.list_display:
            return tuple(self.list_display)
        return tuple(name for name in self.schema.fields if name not in self.exclude)

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
        skip = set(self.exclude) | set(self.schema.primary_key)
        return tuple(name for name in self.schema.fields if name not in skip)

    def get_readonly_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        """The fields shown but not editable."""
        return tuple(self.readonly_fields)

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
        """The column heading for a path."""
        return self.field_for(path).label

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

    # Permissions. Four levels: the view, the action, the field and the row.

    async def allows(
        self, action: Action | str, *, request: Any = None, record: Any = None
    ) -> bool:
        """Whether the current user may do this, to this record."""
        name = action_name(action)
        if name == Action.CREATE:
            return self.can_create
        if name == Action.EDIT:
            return self.can_edit
        if name == Action.DELETE:
            return self.can_delete
        return True

    async def ensure(
        self, action: Action | str, *, request: Any = None, record: Any = None
    ) -> None:
        """Raise unless the current user may do this."""
        if not await self.allows(action, request=request, record=record):
            raise PermissionDeniedError(action_name(action), self.label_plural)

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
        await self.ensure(Action.VIEW, request=request)
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
        await self.ensure(Action.VIEW, request=request)
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
            Action.CREATE if created else Action.EDIT,
            request=request,
            record=record,
        )
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

            await self.repository.apply_values(session, target, values)
            if created:
                await session.add(target)
            await session.flush()

            await self.after_save(context)
        return target

    async def delete(
        self, session: SessionAdapter, record: Any, *, request: Any = None
    ) -> None:
        """Delete a record, running the hooks in one transaction."""
        await self.ensure(Action.DELETE, request=request, record=record)
        async with session.transaction():
            context = DeleteContext(session=session, record=record, request=request)
            await self.before_delete(context)
            await self.repository.delete(session, record)
            await self.after_delete(context)

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
