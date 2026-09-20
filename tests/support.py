from dataclasses import dataclass, field
from types import TracebackType

from sqlalchemy import Engine, event

from adminsite.backends.sqlalchemy import Database


@dataclass
class Backend:
    """A database under test, plus the engine its queries can be counted on."""

    database: Database
    engine: Engine
    is_async: bool


@dataclass
class QueryCounter:
    """Counts the statements an engine runs inside a block."""

    engine: Engine
    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)

    def _record(
        self,
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        self.statements.append(statement)

    def __enter__(self) -> "QueryCounter":
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)


def count_queries(backend: Backend) -> QueryCounter:
    """Count the queries a block of code runs against this backend."""
    return QueryCounter(backend.engine)
