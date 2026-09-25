import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from adminsite.audit.entry import AuditEntry, AuditEvent
from adminsite.backends.sqlalchemy.session import Database

logger = logging.getLogger("adminsite")


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """Which entries to find. Every field narrows the result; empty means all.

    - `views`: only entries of these views, such as the ones a person may
      read. An empty string among them stands for entries of no view, such
      as signing in.
    - `view` and `record_key`: one view, or one record of it.
    - `user`: one person, matched against `user_key` and against `user`.
    - `events`: only these kinds of entry.
    - `since` and `until`: from `since`, up to but not including `until`.
    - `older_than`: the `(occurred_at, id)` of the last entry already shown,
      to find the next page after it.
    """

    views: Sequence[str] | None = None
    view: str | None = None
    record_key: str | None = None
    user: str | None = None
    events: Sequence[AuditEvent] = ()
    since: datetime | None = None
    until: datetime | None = None
    older_than: tuple[datetime, int] | None = None


@runtime_checkable
class AuditStore(Protocol):
    """Where the audit log is written and read back.

    `AuditLog` is the one that ships. Write your own to keep the log in a
    table of your application, or to send it on elsewhere; the History tab
    and the Activity page read it through `find` either way.

    A store that keeps its entries in the admin's own database can say so,
    and have each change and its entries saved in one transaction, with two
    more methods:

    - `lives_in(database) -> bool`: whether the entries are kept in that
      `Database`.
    - `async record_within(session, entries)`: write the entries through
      that session, inside its transaction, without committing.

    Without them, entries are written once the change has committed.
    """

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        """Keep these entries."""
        ...

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        """The entries that match, newest first, at most `limit` of them.

        Newest first means by `occurred_at`, then by `id`, both descending,
        which is also the order `older_than` pages through.
        """
        ...


def lives_in(store: AuditStore, database: Database) -> bool:
    """Whether a store keeps its entries in this database and can join its work."""
    check = getattr(store, "lives_in", None)
    return callable(check) and bool(check(database)) and hasattr(store, "record_within")


async def record_or_warn(store: AuditStore, entries: Sequence[AuditEntry]) -> None:
    """Write entries down, or say in the server log which ones were lost.

    What they describe has already happened, so a store that fails here
    must not turn it into an error for the person who did it.
    """
    try:
        await store.record(entries)
    except Exception:
        named = ", ".join(
            " ".join(
                part for part in (entry.event, entry.view, entry.record_key) if part
            )
            for entry in entries[:5]
        )
        more = f" and {len(entries) - 5} more" if len(entries) > 5 else ""
        logger.exception(
            "The change was saved, but its audit entries could not be written: %s%s.",
            named,
            more,
        )
