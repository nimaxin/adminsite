from dataclasses import dataclass, field
from decimal import Decimal
from types import TracebackType

from sqlalchemy import Engine, event

from adminsite.backends.sqlalchemy import Database
from tests.models import Product


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


async def spare_product(database: Database) -> int:
    """Add a product no order refers to, so it can be deleted freely."""
    async with database.session() as session:
        product = Product(name="Gift card", price=Decimal("10.00"))
        await session.add(product)
        await session.commit()
        return int(product.id)
