from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ColumnElement,
    Select,
    Table,
    and_,
    false,
    func,
    or_,
    select,
    text,
)
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import aliased

from adminsite.backends.sqlalchemy.cursor import decode_cursor, encode_cursor
from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
from adminsite.backends.sqlalchemy.loader import build_load_options
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.backends.sqlalchemy.values import to_column_type
from adminsite.exceptions import InvalidPathError, RecordNotFoundError
from adminsite.query import DEFAULT_PAGE_SIZE, CountMode, Page, QuerySpec
from adminsite.schema import FieldPath, FieldSchema, ModelSchema, RelationSchema

if TYPE_CHECKING:
    from adminsite.backends.sqlalchemy.filters import SQLFilter

NUMBER_TYPES = (int, float, Decimal)

ConditionBuilder = Callable[
    [ColumnElement[Any], FieldSchema], ColumnElement[bool] | None
]

# Narrows every read to the rows the current user may see.
Scope = Callable[[Select[Any]], Select[Any]]

# Up to this many rows an exact count is cheap, so an estimated count only
# guesses above it, and a narrowed count stops just past it.
EXACT_COUNT_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class KeysetKey:
    """One column a keyset page is ordered by."""

    name: str
    column: Any
    descending: bool
    python_type: type[Any]


@dataclass(frozen=True, slots=True)
class Total:
    """How many records match, and how sure that number is."""

    value: int | None = None
    estimated: bool = False
    at_least: bool = False


class SQLAlchemyRepository:
    """Reads records for one model, in as few queries as it can manage."""

    def __init__(
        self,
        model: type[Any],
        inspector: SQLAlchemyInspector | None = None,
        filters: Sequence["SQLFilter"] = (),
    ) -> None:
        self.model = model
        self.inspector = inspector or SQLAlchemyInspector()
        self.schema = self.inspector.inspect(model)
        self.filters = tuple(filters)
        self._filters_by_name = {item.name: item for item in self.filters}

    async def list(
        self,
        session: SessionAdapter,
        spec: QuerySpec,
        scope: Scope | None = None,
    ) -> Page:
        """Read one page of records, loading what the page will show."""
        keys = self.keyset_keys(spec) if spec.keyset else None
        if keys is not None:
            return await self._list_by_keyset(session, spec, scope, keys)

        statement = self.statement(spec, scope)
        wants_probe = spec.limit is not None and spec.count is not CountMode.EXACT
        fetch = spec.limit + 1 if wants_probe and spec.limit else spec.limit

        if fetch is not None:
            statement = statement.limit(fetch)
        if spec.offset:
            statement = statement.offset(spec.offset)

        rows = list((await session.scalars(statement)).unique().all())

        has_next = False
        if wants_probe and spec.limit is not None:
            has_next = len(rows) > spec.limit
            rows = rows[: spec.limit]
        total = await self.total(session, spec, scope)
        if spec.count is CountMode.EXACT and total.value is not None:
            has_next = spec.offset + len(rows) < total.value

        return Page(
            rows=rows,
            offset=spec.offset,
            limit=spec.limit,
            total=total.value,
            has_next=has_next,
            estimated=total.estimated,
            at_least=total.at_least,
        )

    async def count(
        self,
        session: SessionAdapter,
        spec: QuerySpec,
        scope: Scope | None = None,
        *,
        limit: int | None = None,
    ) -> int:
        """Count the records the query matches, ignoring the page.

        With a limit the count stops there, so it never costs more than
        reading that many keys.
        """
        rows = self.base_statement(scope).with_only_columns(
            *self._primary_key_columns()
        )
        rows = self.narrow(rows, spec)
        if limit is not None:
            rows = rows.limit(limit)
        counted = select(func.count()).select_from(rows.subquery())
        return int(await session.scalar(counted) or 0)

    async def total(
        self,
        session: SessionAdapter,
        spec: QuerySpec,
        scope: Scope | None = None,
    ) -> Total:
        """Count the matches as hard as the count mode asks."""
        if spec.count is CountMode.NONE:
            return Total()
        if spec.count is CountMode.ESTIMATED:
            if self._narrows(spec, scope):
                counted = await self.count(
                    session, spec, scope, limit=EXACT_COUNT_LIMIT + 1
                )
                if counted > EXACT_COUNT_LIMIT:
                    return Total(EXACT_COUNT_LIMIT, at_least=True)
                return Total(counted)
            guess = await self.estimate(session)
            if guess is not None and guess > EXACT_COUNT_LIMIT:
                return Total(guess, estimated=True)
        return Total(await self.count(session, spec, scope))

    async def estimate(self, session: SessionAdapter) -> int | None:
        """The database's own guess at how many rows the table holds.

        Postgres and MySQL keep one in their statistics, which costs nothing
        to read. Other databases give None.
        """
        table = sqlalchemy_inspect(self.model).local_table
        if not isinstance(table, Table):
            return None
        dialect = await session.run(lambda plain: plain.get_bind().dialect)
        if dialect.name == "postgresql":
            statement = text(
                "SELECT CAST(reltuples AS BIGINT) FROM pg_class"
                " WHERE oid = to_regclass(:name)"
            ).bindparams(name=dialect.identifier_preparer.format_table(table))
        elif dialect.name in ("mysql", "mariadb"):
            statement = text(
                "SELECT table_rows FROM information_schema.tables"
                " WHERE table_schema = COALESCE(:schema, DATABASE())"
                " AND table_name = :name"
            ).bindparams(schema=table.schema, name=table.name)
        else:
            return None
        found = await session.scalar(statement)
        # Postgres says -1 for a table it has never analyzed.
        if found is None or found < 0:
            return None
        return int(found)

    def _narrows(self, spec: QuerySpec, scope: Scope | None) -> bool:
        """Whether a search, a filter or the scope leaves rows out."""
        if spec.filters or (spec.search.strip() and spec.search_paths):
            return True
        if scope is None:
            return False
        plain = select(self.model)
        return scope(plain) is not plain

    def keyset_keys(self, spec: QuerySpec) -> tuple[KeysetKey, ...] | None:
        """The columns a keyset page orders by, or None if the sort rules it out.

        Every sort column has to be on this model and never empty, since a
        missing value cannot be compared. Enum columns are left out too:
        MySQL sorts a native ENUM by the order it was declared in but
        compares it as text, so the page after a cursor would skip rows.
        The primary key goes last, so no two rows tie.
        """
        keys: list[KeysetKey] = []
        for sort in spec.sort:
            resolved, field = self._resolve_field(sort.path, "sorting")
            if resolved.relations or field.nullable or field.enum_values is not None:
                return None
            keys.append(
                KeysetKey(
                    field.name,
                    getattr(self.model, field.name),
                    sort.descending,
                    field.python_type,
                )
            )
        named = {key.name for key in keys}
        for name in self.schema.primary_key:
            if name not in named:
                keys.append(
                    KeysetKey(
                        name,
                        getattr(self.model, name),
                        False,
                        self.schema.field_named(name).python_type,
                    )
                )
        return tuple(keys)

    async def _list_by_keyset(
        self,
        session: SessionAdapter,
        spec: QuerySpec,
        scope: Scope | None,
        keys: Sequence[KeysetKey],
    ) -> Page:
        token = spec.before or spec.after
        cursor = (
            decode_cursor(token, [key.python_type for key in keys]) if token else None
        )
        backwards = cursor is not None and bool(spec.before)

        statement = self.narrow(self.base_statement(scope), spec)
        if cursor is not None:
            statement = statement.where(self._beyond(keys, cursor, backwards))
        for key in keys:
            descending = key.descending != backwards
            statement = statement.order_by(
                key.column.desc() if descending else key.column.asc()
            )
        if spec.paths:
            statement = statement.options(
                *build_load_options(self.inspector, self.model, spec.paths)
            )

        limit = spec.limit or DEFAULT_PAGE_SIZE
        rows = list((await session.scalars(statement.limit(limit + 1))).unique().all())
        more = len(rows) > limit
        rows = rows[:limit]

        if backwards and not more:
            # Back at the start: show a full first page rather than the few
            # rows that happened to sit before the cursor.
            return await self._list_by_keyset(
                session, spec.replace(after="", before=""), scope, keys
            )
        if backwards:
            rows.reverse()

        has_next = True if backwards else more
        has_previous = more if backwards else cursor is not None
        next_cursor = self._cursor(keys, rows[-1]) if has_next and rows else ""
        previous_cursor = ""
        if has_previous:
            # A page emptied by deletes still offers a way back.
            previous_cursor = self._cursor(keys, rows[0]) if rows else token

        total = await self.total(session, spec, scope)
        return Page(
            rows=rows,
            limit=limit,
            total=total.value,
            has_next=bool(next_cursor),
            estimated=total.estimated,
            at_least=total.at_least,
            keyset=True,
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
        )

    def _beyond(
        self, keys: Sequence[KeysetKey], cursor: Sequence[Any], backwards: bool
    ) -> ColumnElement[bool]:
        """Rows past the cursor in reading order, or before it going back."""
        clauses = []
        for index, key in enumerate(keys):
            value = cursor[index]
            upward = key.descending == backwards
            step = key.column > value if upward else key.column < value
            ties = [
                earlier.column == cursor[position]
                for position, earlier in enumerate(keys[:index])
            ]
            clauses.append(and_(*ties, step))
        return or_(*clauses)

    def _cursor(self, keys: Sequence[KeysetKey], record: Any) -> str:
        return encode_cursor([getattr(record, key.name) for key in keys])

    async def get(
        self,
        session: SessionAdapter,
        key: Any,
        paths: tuple[str, ...] = (),
        scope: Scope | None = None,
    ) -> Any | None:
        """Load one record by primary key, with the paths it will show.

        A record outside the scope reads as missing, so a row the user may
        not see cannot be opened, changed or deleted by guessing its key.
        """
        statement = self.base_statement(scope).where(self.key_clause(key))
        if paths:
            statement = statement.options(
                *build_load_options(self.inspector, self.model, paths)
            )
        return (await session.scalars(statement)).unique().first()

    async def create(self, session: SessionAdapter, values: Mapping[str, Any]) -> Any:
        """Build a record from the given values and put it in the session."""
        record = self.model()
        await self.apply_values(session, record, values)
        await session.add(record)
        await session.flush()
        return record

    async def update(
        self, session: SessionAdapter, record: Any, values: Mapping[str, Any]
    ) -> Any:
        """Change a record, leaving anything not given as it was."""
        await self.apply_values(session, record, values)
        await session.flush()
        return record

    async def delete(self, session: SessionAdapter, record: Any) -> None:
        """Remove a record."""
        await session.delete(record)
        await session.flush()

    async def apply_values(
        self, session: SessionAdapter, record: Any, values: Mapping[str, Any]
    ) -> None:
        """Write values onto a record, loading any records they link to."""
        for name, value in values.items():
            relation = self.schema.relations.get(name)
            if relation is None:
                setattr(record, name, value)
                continue
            setattr(record, name, await self._linked(session, relation, value))

    def base_statement(self, scope: Scope | None = None) -> Select[Any]:
        """The statement every read starts from, narrowed to the scope."""
        statement = select(self.model)
        return statement if scope is None else scope(statement)

    def statement(self, spec: QuerySpec, scope: Scope | None = None) -> Select[Any]:
        """Build the select for a page: search, filters, order, eager loads."""
        statement = self.narrow(self.base_statement(scope), spec)
        statement = self.apply_sort(statement, spec)

        if spec.paths:
            statement = statement.options(
                *build_load_options(self.inspector, self.model, spec.paths)
            )
        return statement

    def narrow(self, statement: Select[Any], spec: QuerySpec) -> Select[Any]:
        """Apply the search and the filters, which every read shares.

        The list, the count, an export and a bulk action all go through
        here, so they always see the same records.
        """
        condition = self.search_clause(spec)
        if condition is not None:
            statement = statement.where(condition)

        for value in spec.filters:
            item = self._filters_by_name.get(value.name)
            if item is not None:
                statement = item.apply(statement, value, self)
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
        if spec.sort:
            # Rows with equal values have no order of their own, and the
            # database may return them differently on each page.
            sorted_by = {sort.path for sort in spec.sort}
            for name in self.schema.primary_key:
                if name not in sorted_by:
                    statement = statement.order_by(getattr(self.model, name).asc())
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
        try:
            converted = [
                to_column_type(self.schema.field_named(name).python_type, value)
                for name, value in zip(self.schema.primary_key, values, strict=True)
            ]
        except ValueError:
            # A key that cannot be the column's type matches nothing, so the
            # caller shows "not found" rather than a database error.
            return false()
        return and_(
            *(column == value for column, value in zip(columns, converted, strict=True))
        )

    def identity_of(self, record: Any) -> str:
        """Write the primary key of a record as one string, for a URL."""
        values = [str(getattr(record, name)) for name in self.schema.primary_key]
        return ",".join(values)

    async def _linked(
        self, session: SessionAdapter, relation: RelationSchema, value: Any
    ) -> Any:
        if relation.collection:
            keys = value or ()
            return [await self._load_linked(session, relation, key) for key in keys]
        if value is None or value == "":
            return None
        return await self._load_linked(session, relation, value)

    async def _load_linked(
        self, session: SessionAdapter, relation: RelationSchema, key: Any
    ) -> Any:
        if isinstance(key, relation.target):
            return key
        target = self.inspector.inspect(relation.target)
        wanted = self._as_key(target, key)
        record = await session.get(relation.target, wanted)
        if record is None:
            raise RecordNotFoundError(relation.target, key)
        return record

    def _as_key(self, target: ModelSchema, key: Any) -> Any:
        values = key if isinstance(key, tuple) else (key,)
        try:
            converted = [
                to_column_type(target.field_named(name).python_type, value)
                for name, value in zip(target.primary_key, values, strict=False)
            ]
        except ValueError:
            raise RecordNotFoundError(target.model, key) from None
        return converted[0] if len(converted) == 1 else tuple(converted)

    # The annotation says Sequence because this class has a method named
    # list, which shadows the builtin inside the class body.
    def _primary_key_columns(self) -> Sequence[Any]:
        return [getattr(self.model, name) for name in self.schema.primary_key]

    def _resolve_field(self, path: str, action: str) -> tuple[FieldPath, FieldSchema]:
        resolved = self.inspector.resolve(self.model, path)
        if resolved.field is None:
            raise InvalidPathError(path, f"{action} needs a field, not a link.")
        return resolved, resolved.field

    def condition_at(
        self, path: str, build: ConditionBuilder, action: str = "filtering"
    ) -> ColumnElement[bool] | None:
        """Build a condition on the column a path names.

        When the path crosses a relationship the condition is wrapped in
        `has` or `any`, so no joins are added and no rows are duplicated.
        """
        resolved, field = self._resolve_field(path, action)
        owners = [self.model]
        for relation in resolved.relations:
            owners.append(relation.target)

        column = getattr(owners[-1], field.name)
        clause: ColumnElement[bool] | None = build(column, field)
        if clause is None:
            return None

        for index in reversed(range(len(resolved.relations))):
            relation = resolved.relations[index]
            attribute = getattr(owners[index], relation.name)
            clause = (
                attribute.any(clause) if relation.collection else attribute.has(clause)
            )
        return clause

    def _clause_for_path(self, path: str, term: str) -> ColumnElement[bool] | None:
        return self.condition_at(
            path,
            lambda column, field: self._match(column, field, term),
            action="searching",
        )

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
