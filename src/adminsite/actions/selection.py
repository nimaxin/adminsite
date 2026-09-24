from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, and_, delete, false, func, or_, select, tuple_, update
from sqlalchemy.sql import Executable

from adminsite.audit.entry import Change, diff
from adminsite.backends.sqlalchemy.loader import build_load_options
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.backends.sqlalchemy.values import to_column_type
from adminsite.query import QuerySpec

if TYPE_CHECKING:
    from adminsite.views import ModelView


@dataclass
class Selection:
    """The rows an action runs over.

    Either the rows that were ticked, or every row the current search and
    filters match. The second kind is never loaded into memory, so an
    action over a large table stays one statement.
    """

    view: "ModelView"
    session: SessionAdapter
    spec: QuerySpec
    keys: Sequence[str] = ()
    everything: bool = False
    request: Any = None
    # What update and delete changed, per record key, for the audit log.
    changes: dict[str, dict[str, Change]] = field(default_factory=dict)

    @property
    def repository(self) -> Any:
        """The repository of the view this selection belongs to."""
        return self.view.repository

    def statement(self) -> Select[Any]:
        """A statement selecting the primary keys this covers."""
        columns = [
            getattr(self.view.model, name) for name in self.view.schema.primary_key
        ]
        rows: Select[Any] = select(*columns).select_from(self.view.model)
        rows = self.view.scope_for(self.request)(rows)
        rows = self.repository.narrow(rows, self.spec)

        if not self.everything:
            rows = rows.where(self._key_condition())
        return rows

    async def covered_keys(self) -> list[str]:
        """The keys of the records this covers, written as the URLs write them."""
        result = await self.session.execute(self.statement())
        return [",".join(str(value) for value in row) for row in result.all()]

    async def count(self) -> int:
        """How many rows this covers."""
        counted = select(func.count()).select_from(self.statement().subquery())
        return int(await self.session.scalar(counted) or 0)

    async def records(self, *, paths: Sequence[str] = ()) -> list[Any]:
        """Load the records, for work that needs each one in turn.

        `paths` names links to load with them, such as `customer`, so work
        on each record never waits on a query of its own.
        """
        statement = self.repository.base_statement(
            self.view.scope_for(self.request)
        ).where(self._covered())
        if paths:
            statement = statement.options(
                *build_load_options(self.repository.inspector, self.view.model, paths)
            )
        return list((await self.session.scalars(statement)).unique().all())

    async def update(self, **values: Any) -> int:
        """Change every row this covers, in one statement.

        This does not run the save hooks, because it never loads the
        records. Use `records` when the hooks matter.
        """
        if not values:
            return 0
        if self.view.audit is not None:
            await self._remember(list(values), after=values)
        statement = update(self.view.model).where(self._covered()).values(**values)
        return await self._run(statement)

    async def delete(self) -> int:
        """Delete every row this covers, in one statement."""
        if self.view.audit is not None:
            paths = [
                path
                for path in self.view.get_form_fields(self.request)
                if path in self.view.schema.fields
            ]
            await self._remember(paths, after=None)
        statement = delete(self.view.model).where(self._covered())
        return await self._run(statement)

    async def _remember(
        self, paths: Sequence[str], after: Mapping[str, Any] | None
    ) -> None:
        """Read what the rows hold now, so the log can show what changed.

        One query covers every row, whatever the size of the selection.
        """
        names = [path for path in paths if path in self.view.schema.fields]
        columns = [getattr(self.view.model, name) for name in names]
        key_columns = self._primary_key_columns()
        statement = select(*key_columns, *columns).where(self._covered())
        result = await self.session.execute(statement)
        for row in result.all():
            key_parts, current = row[: len(key_columns)], row[len(key_columns) :]
            key = ",".join(str(part) for part in key_parts)
            before = {
                name: self.view.field_for(name).display(value)
                for name, value in zip(names, current, strict=True)
            }
            if after is None:
                changes = {
                    name: (value, None) for name, value in before.items() if value
                }
            else:
                changes = diff(
                    before,
                    {
                        name: self.view.field_for(name).display(after[name])
                        for name in names
                    },
                )
            self.changes[str(key)] = changes

    async def _run(self, statement: Executable) -> int:
        """Run a write and report how many rows it touched."""
        result = await self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    def _primary_key_columns(self) -> list[Any]:
        return [getattr(self.view.model, name) for name in self.view.schema.primary_key]

    def _covered(self) -> Any:
        """A condition matching the rows this selection covers.

        The keys are read from a derived table, which MySQL insists on when
        a write refers back to the table it changes. A composite key is
        matched as a row value, so a selection of one line of an order
        never covers the other lines of the same order.
        """
        covered = self.statement().subquery()
        columns = self._primary_key_columns()
        if len(columns) == 1:
            return columns[0].in_(select(covered.c[0]))
        return tuple_(*columns).in_(select(*covered.c))

    def _key_condition(self) -> Any:
        """Match the keys that were ticked, written as the URLs write them."""
        columns = self._primary_key_columns()
        fields = [
            self.view.schema.field_named(name) for name in self.view.schema.primary_key
        ]
        wanted = []
        for key in self.keys:
            parts = key.split(",") if len(columns) > 1 else [key]
            if len(parts) != len(columns):
                continue
            try:
                wanted.append(
                    tuple(
                        to_column_type(field.python_type, part)
                        for field, part in zip(fields, parts, strict=True)
                    )
                )
            except ValueError:
                continue
        if not wanted:
            return false()
        if len(columns) == 1:
            return columns[0].in_([values[0] for values in wanted])
        return or_(
            *[
                and_(
                    *[
                        column == value
                        for column, value in zip(columns, values, strict=True)
                    ]
                )
                for values in wanted
            ]
        )
