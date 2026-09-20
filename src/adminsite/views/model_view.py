from collections.abc import Sequence
from typing import Any, ClassVar

from adminsite.backends.sqlalchemy.filters import SQLFilter, filter_for
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.exceptions import AdminSiteError
from adminsite.fields import Field, FieldRegistry, RelationField, default_registry
from adminsite.filters import Filter, FilterValue
from adminsite.query import CountMode, QuerySpec, Sort
from adminsite.text import RecordValues, pluralize, snake_case


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
