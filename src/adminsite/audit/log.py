from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, and_, insert, or_, select

from adminsite.audit.entry import AuditEntry, audit_metadata, audit_table
from adminsite.audit.store import AuditQuery
from adminsite.backends.sqlalchemy.session import (
    Database,
    SessionAdapter,
    SessionSource,
)
from adminsite.storage import Store

DEFAULT_URL = "sqlite:///adminsite_audit.db"

# Entries for a bulk action are written this many at a time.
BATCH_SIZE = 500


class AuditLog(Store):
    """Where the admin writes down who changed what.

    By default the entries go to a SQLite file of their own, which needs no
    setup. Pass your engine to keep them in your own database instead; the
    table is then `adminsite_audit_log` on `audit_metadata`, which you add to
    your migrations.

    Kept in the admin's own database, a change and its entries are saved in
    one transaction: both or neither. Kept anywhere else, entries are written
    once the change has committed, so a change that was rolled back never
    shows up, but one whose entries fail to write is not logged.
    """

    metadata = audit_metadata

    def __init__(
        self,
        source: Database | SessionSource | str = DEFAULT_URL,
        *,
        create_table: bool | None = None,
    ) -> None:
        super().__init__(source, create_table=create_table)

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        """Write entries down, a batch at a time."""
        if not entries:
            return
        await self.prepare()
        rows = [entry.as_row() for entry in entries]
        async with self.database.session() as session:
            for start in range(0, len(rows), BATCH_SIZE):
                await session.execute(
                    insert(audit_table).values(rows[start : start + BATCH_SIZE])
                )
            await session.commit()

    def lives_in(self, database: Database) -> bool:
        """Whether the entries are kept in that database."""
        return self.database.same_as(database)

    async def record_within(
        self, session: SessionAdapter, entries: Sequence[AuditEntry]
    ) -> None:
        """Write entries through the caller's session, inside its transaction.

        Nothing is committed here: the entries are saved with the change
        they describe, when its transaction commits, or not at all.
        """
        if not entries:
            return
        if self.create_table and not self._ready:
            # Created through the same session, since a second connection
            # could wait forever on the locks this transaction holds.
            await session.run(self._create_tables)
            self._ready = True
        rows = [entry.as_row() for entry in entries]
        for start in range(0, len(rows), BATCH_SIZE):
            await session.execute(
                insert(audit_table).values(rows[start : start + BATCH_SIZE])
            )

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        """The entries that match, newest first, at most `limit` of them."""
        return await self._read(matching(query).limit(limit))

    async def history(
        self, view: str, record_key: str, *, limit: int = 100
    ) -> list[AuditEntry]:
        """What happened to one record, newest first."""
        query = AuditQuery(view=view, record_key=record_key)
        return await self.find(query, limit=limit)

    async def recent(
        self,
        *,
        view: str | None = None,
        views: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """The latest entries, across the admin, for some views or for one."""
        return await self.find(AuditQuery(view=view, views=views), limit=limit)

    async def _read(self, statement: Any) -> list[AuditEntry]:
        await self.prepare()
        async with self.database.session() as session:
            result = await session.execute(statement)
            return [AuditEntry.from_row(row._mapping) for row in result.all()]


def matching(query: AuditQuery) -> Select[Any]:
    """The rows of the audit table a query asks for, newest first."""
    table = audit_table
    statement = select(table).order_by(table.c.occurred_at.desc(), table.c.id.desc())
    if query.views is not None:
        statement = statement.where(table.c.view.in_(query.views))
    if query.view is not None:
        statement = statement.where(table.c.view == query.view)
    if query.record_key is not None:
        statement = statement.where(table.c.record_key == query.record_key)
    if query.user is not None:
        statement = statement.where(
            or_(table.c.user_key == query.user, table.c.user == query.user)
        )
    if query.events:
        statement = statement.where(
            table.c.event.in_([event.value for event in query.events])
        )
    if query.since is not None:
        statement = statement.where(table.c.occurred_at >= query.since)
    if query.until is not None:
        statement = statement.where(table.c.occurred_at < query.until)
    if query.older_than is not None:
        when, key = query.older_than
        statement = statement.where(
            or_(
                table.c.occurred_at < when,
                and_(table.c.occurred_at == when, table.c.id < key),
            )
        )
    return statement
