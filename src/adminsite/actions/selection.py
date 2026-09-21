from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, delete, false, func, select, update
from sqlalchemy.sql import Executable

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

    async def count(self) -> int:
        """How many rows this covers."""
        counted = select(func.count()).select_from(self.statement().subquery())
        return int(await self.session.scalar(counted) or 0)

    async def records(self) -> list[Any]:
        """Load the records, for work that needs each one in turn."""
        statement = self.repository.base_statement(
            self.view.scope_for(self.request)
        ).where(
            self._primary_key_column().in_(select(self.statement().subquery().c[0]))
        )
        return list((await self.session.scalars(statement)).unique().all())

    async def update(self, **values: Any) -> int:
        """Change every row this covers, in one statement.

        This does not run the save hooks, because it never loads the
        records. Use `records` when the hooks matter.
        """
        if not values:
            return 0
        statement = (
            update(self.view.model)
            .where(
                self._primary_key_column().in_(select(self.statement().subquery().c[0]))
            )
            .values(**values)
        )
        return await self._run(statement)

    async def delete(self) -> int:
        """Delete every row this covers, in one statement."""
        statement = delete(self.view.model).where(
            self._primary_key_column().in_(select(self.statement().subquery().c[0]))
        )
        return await self._run(statement)

    async def _run(self, statement: Executable) -> int:
        """Run a write and report how many rows it touched."""
        result = await self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    def _primary_key_column(self) -> Any:
        return getattr(self.view.model, self.view.schema.primary_key[0])

    def _key_condition(self) -> Any:
        column = self._primary_key_column()
        field = self.view.schema.field_named(self.view.schema.primary_key[0])
        wanted = []
        for key in self.keys:
            try:
                wanted.append(to_column_type(field.python_type, key))
            except ValueError:
                continue
        return column.in_(wanted) if wanted else false()
