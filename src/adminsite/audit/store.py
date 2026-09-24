from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from adminsite.audit.entry import AuditEntry, AuditEvent


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
