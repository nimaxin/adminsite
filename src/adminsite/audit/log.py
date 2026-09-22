from collections.abc import Sequence
from typing import Any

from sqlalchemy import insert, select

from adminsite.audit.entry import AuditEntry, audit_metadata, audit_table
from adminsite.backends.sqlalchemy.session import Database, SessionSource
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

    Entries are written after the change they describe has committed, so a
    change that was rolled back never shows up in the history.
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

    async def history(
        self, view: str, record_key: str, *, limit: int = 100
    ) -> list[AuditEntry]:
        """What happened to one record, newest first."""
        statement = (
            select(audit_table)
            .where(audit_table.c.view == view)
            .where(audit_table.c.record_key == record_key)
            .order_by(audit_table.c.occurred_at.desc(), audit_table.c.id.desc())
            .limit(limit)
        )
        return await self._read(statement)

    async def recent(
        self, *, view: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        """The latest entries, across the admin or for one view."""
        statement = select(audit_table)
        if view is not None:
            statement = statement.where(audit_table.c.view == view)
        statement = statement.order_by(
            audit_table.c.occurred_at.desc(), audit_table.c.id.desc()
        ).limit(limit)
        return await self._read(statement)

    async def _read(self, statement: Any) -> list[AuditEntry]:
        await self.prepare()
        async with self.database.session() as session:
            result = await session.execute(statement)
            return [AuditEntry.from_row(row._mapping) for row in result.all()]
