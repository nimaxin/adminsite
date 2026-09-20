from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, false, func, or_, select
from sqlalchemy.orm import aliased

from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.loader import build_load_options
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.exceptions import InvalidPathError
from adminsite.query import CountMode, Page, QuerySpec
from adminsite.schema import FieldPath, FieldSchema

NUMBER_TYPES = (int, float, Decimal)


class SQLAlchemyRepository:
    """Reads records for one model, in as few queries as it can manage."""

    def __init__(
        self, model: type[Any], inspector: SQLAlchemyInspector | None = None
    ) -> None:
        self.model = model
        self.inspector = inspector or SQLAlchemyInspector()
        self.schema = self.inspector.inspect(model)

    async def list(self, session: SessionAdapter, spec: QuerySpec) -> Page:
        """Read one page of records, loading what the page will show."""
        statement = self.statement(spec)
        wants_probe = spec.limit is not None and spec.count is CountMode.NONE
        fetch = spec.limit + 1 if wants_probe and spec.limit else spec.limit

        if fetch is not None:
            statement = statement.limit(fetch)
        if spec.offset:
            statement = statement.offset(spec.offset)

        rows = list((await session.scalars(statement)).unique().all())

        total: int | None = None
        has_next = False
        if wants_probe and spec.limit is not None:
            has_next = len(rows) > spec.limit
            rows = rows[: spec.limit]
        if spec.count is CountMode.EXACT:
            total = await self.count(session, spec)
            has_next = spec.offset + len(rows) < total

        return Page(
            rows=rows,
            offset=spec.offset,
            limit=spec.limit,
            total=total,
            has_next=has_next,
        )

    async def count(self, session: SessionAdapter, spec: QuerySpec) -> int:
        """Count the records the query matches, ignoring the page."""
        rows = self.base_statement().with_only_columns(*self._primary_key_columns())
        condition = self.search_clause(spec)
        if condition is not None:
            rows = rows.where(condition)
        counted = select(func.count()).select_from(rows.subquery())
        return int(await session.scalar(counted) or 0)

    async def get(
        self,
        session: SessionAdapter,
        key: Any,
        paths: tuple[str, ...] = (),
    ) -> Any | None:
        """Load one record by primary key, with the paths it will show."""
        statement = self.base_statement().where(self.key_clause(key))
        if paths:
            statement = statement.options(
                *build_load_options(self.inspector, self.model, paths)
            )
        return (await session.scalars(statement)).unique().first()

    def base_statement(self) -> Select[Any]:
        """The statement every read starts from."""
        return select(self.model)

    def statement(self, spec: QuerySpec) -> Select[Any]:
        """Build the select for a page: search, order and eager loads."""
        statement = self.base_statement()

        condition = self.search_clause(spec)
        if condition is not None:
            statement = statement.where(condition)

        statement = self.apply_sort(statement, spec)

        if spec.paths:
            statement = statement.options(
                *build_load_options(self.inspector, self.model, spec.paths)
            )
        return statement

    def search_clause(self, spec: QuerySpec) -> ColumnElement[bool] | None:
        """Match the search term against the searchable paths."""
        term = spec.search.strip()
        if not term or not spec.search_paths:
            return None

        clauses = [
            clause
            for path in spec.search_paths
            if (clause := self._clause_for_path(path, term)) is not None
        ]
        if not clauses:
            # The term fits none of the searchable columns, so nothing
            # matches. Returning nothing here would show every record and
            # look as though the search had been ignored.
            return false()
        return or_(*clauses)

    def apply_sort(self, statement: Select[Any], spec: QuerySpec) -> Select[Any]:
        """Order the statement, joining what the sort paths need."""
        joined: dict[tuple[str, ...], Any] = {}
        for sort in spec.sort:
            resolved, field = self._resolve_field(sort.path, "sorting")
            if resolved.crosses_collection:
                raise InvalidPathError(
                    sort.path,
                    "sorting cannot go through a relationship holding many records.",
                )
            statement, column = self._column_for_sort(
                statement, resolved, field, joined
            )
            statement = statement.order_by(
                column.desc() if sort.descending else column.asc()
            )
        return statement

    def key_clause(self, key: Any) -> ColumnElement[bool]:
        """Match a record by its primary key, single or composite."""
        columns = self._primary_key_columns()
        values = key if isinstance(key, tuple) else (key,)
        if len(values) != len(columns):
            raise InvalidPathError(
                str(key),
                f"{self.model.__name__} needs {len(columns)} key values.",
            )
        return and_(
            *(column == value for column, value in zip(columns, values, strict=True))
        )

    def identity_of(self, record: Any) -> str:
        """Write the primary key of a record as one string, for a URL."""
        values = [str(getattr(record, name)) for name in self.schema.primary_key]
        return ",".join(values)

    # The annotation says Sequence because this class has a method named
    # list, which shadows the builtin inside the class body.
    def _primary_key_columns(self) -> Sequence[Any]:
        return [getattr(self.model, name) for name in self.schema.primary_key]

    def _resolve_field(self, path: str, action: str) -> tuple[FieldPath, FieldSchema]:
        resolved = self.inspector.resolve(self.model, path)
        if resolved.field is None:
            raise InvalidPathError(path, f"{action} needs a field, not a link.")
        return resolved, resolved.field

    def _clause_for_path(self, path: str, term: str) -> ColumnElement[bool] | None:
        resolved, field = self._resolve_field(path, "searching")
        owners = [self.model]
        for relation in resolved.relations:
            owners.append(relation.target)

        column = getattr(owners[-1], field.name)
        clause: ColumnElement[bool] | None = self._match(column, field, term)
        if clause is None:
            return None

        for index in reversed(range(len(resolved.relations))):
            relation = resolved.relations[index]
            attribute = getattr(owners[index], relation.name)
            clause = (
                attribute.any(clause) if relation.collection else attribute.has(clause)
            )
        return clause

    def _match(
        self, column: ColumnElement[Any], field: FieldSchema, term: str
    ) -> ColumnElement[bool] | None:
        if field.enum_values:
            matches = [
                value for value in field.enum_values if term.lower() in value.lower()
            ]
            return column.in_(matches) if matches else None
        if field.python_type is str:
            return column.ilike(f"%{term}%")
        if field.python_type in NUMBER_TYPES:
            number = self._as_number(term, field.python_type)
            return None if number is None else column == number
        # Dates and booleans need a filter, not a text search.
        return None

    def _as_number(self, term: str, python_type: type[Any]) -> Any | None:
        try:
            return python_type(term)
        except (ValueError, ArithmeticError, InvalidOperation):
            return None

    def _column_for_sort(
        self,
        statement: Select[Any],
        resolved: FieldPath,
        field: FieldSchema,
        joined: dict[tuple[str, ...], Any],
    ) -> tuple[Select[Any], Any]:
        entity: Any = self.model
        prefix: tuple[str, ...] = ()

        for relation in resolved.relations:
            prefix += (relation.name,)
            if prefix in joined:
                entity = joined[prefix]
                continue
            target = aliased(relation.target)
            statement = statement.join(
                getattr(entity, relation.name).of_type(target), isouter=True
            )
            joined[prefix] = target
            entity = target

        return statement, getattr(entity, field.name)
