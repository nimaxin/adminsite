from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    or_,
    select,
)

from adminsite.backends.sqlalchemy.session import Database, SessionSource
from adminsite.storage import Store

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

DEFAULT_URL = "sqlite:///adminsite_views.db"

# Query values that belong to a moment, not to a view worth keeping.
UNSAVED_KEYS = frozenset({"page", "after", "before", "_csrf"})

saved_view_metadata = MetaData()

saved_view_table = Table(
    "adminsite_saved_views",
    saved_view_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("view", String(100), nullable=False, index=True),
    Column("name", String(100), nullable=False),
    Column("query", Text, nullable=False),
    Column("owner", String(200), nullable=True),
    Column("shared", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, nullable=False),
)


@dataclass(frozen=True, slots=True)
class SavedView:
    """A search, filters, sort and columns kept under a name."""

    view: str
    name: str
    query: str
    owner: str | None = None
    shared: bool = False
    id: int | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> "SavedView":
        """Build one from a row of the table."""
        return cls(
            view=row["view"],
            name=row["name"],
            query=row["query"],
            owner=row["owner"],
            shared=bool(row["shared"]),
            id=row["id"],
            created_at=row["created_at"],
        )


def clean_query(query: str) -> str:
    """Keep what describes the list and drop the page it happened to be on."""
    pairs = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=False)
        if key not in UNSAVED_KEYS
    ]
    return urlencode(pairs)


class SavedViews(Store):
    """Where people keep the lists they come back to.

    Like the audit log, the views go to a SQLite file of their own by
    default. Pass your engine to keep them in your database; the table is
    then `adminsite_saved_views` on `saved_view_metadata`.
    """

    metadata = saved_view_metadata

    def __init__(
        self,
        source: Database | SessionSource | str = DEFAULT_URL,
        *,
        create_table: bool | None = None,
    ) -> None:
        super().__init__(source, create_table=create_table)

    async def visible_to(self, view: str, owner: str | None) -> list[SavedView]:
        """A person's own views of a list, and the ones others shared."""
        await self.prepare()
        mine = (
            saved_view_table.c.owner.is_(None)
            if owner is None
            else saved_view_table.c.owner == owner
        )
        statement = (
            select(saved_view_table)
            .where(saved_view_table.c.view == view)
            .where(or_(mine, saved_view_table.c.shared.is_(True)))
            .order_by(saved_view_table.c.name, saved_view_table.c.id)
        )
        async with self.database.session() as session:
            result = await session.execute(statement)
            return [SavedView.from_row(row._mapping) for row in result.all()]

    async def save(self, saved: SavedView) -> SavedView:
        """Keep a view, and return it with its id."""
        await self.prepare()
        values = {
            "view": saved.view,
            "name": saved.name,
            "query": clean_query(saved.query),
            "owner": saved.owner,
            "shared": saved.shared,
            "created_at": saved.created_at,
        }
        async with self.database.session() as session:
            result = await session.execute(insert(saved_view_table).values(values))
            await session.commit()
            key = cast("CursorResult[Any]", result).inserted_primary_key
        return SavedView.from_row({**values, "id": key[0] if key else None})

    async def delete(self, key: int, owner: str | None) -> bool:
        """Remove a view, if it belongs to this person."""
        await self.prepare()
        mine = (
            saved_view_table.c.owner.is_(None)
            if owner is None
            else saved_view_table.c.owner == owner
        )
        statement = delete(saved_view_table).where(saved_view_table.c.id == key, mine)
        async with self.database.session() as session:
            result = await session.execute(statement)
            await session.commit()
        return bool(getattr(result, "rowcount", 0))
