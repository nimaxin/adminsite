from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

# The table lives on its own metadata, so a project can add it to its
# migrations without it ever mixing with the project's own models.
audit_metadata = MetaData()

audit_table = Table(
    "adminsite_audit_log",
    audit_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("occurred_at", DateTime, nullable=False, index=True),
    Column("view", String(100), nullable=False),
    Column("record_key", String(200), nullable=False),
    Column("record_title", String(300), nullable=True),
    Column("event", String(20), nullable=False),
    Column("action", String(100), nullable=True),
    Column("batch", String(36), nullable=True, index=True),
    Column("user", String(200), nullable=True),
    Column("changes", JSON, nullable=False),
    Column("message", Text, nullable=True),
    # These came after the columns above. Every one may be empty, so a table
    # made before them takes them with ALTER TABLE ... ADD COLUMN alone.
    Column("user_key", String(200), nullable=True, index=True),
    Column("ip", String(45), nullable=True),
    Column("user_agent", String(300), nullable=True),
    Column("error", Text, nullable=True),
    Column("inputs", JSON, nullable=True),
    Index("ix_adminsite_audit_log_record", "view", "record_key"),
)


class AuditEvent(StrEnum):
    """What happened: to a record, or to someone signing in or out."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACTION = "action"
    EXPORTED = "exported"
    SIGNED_IN = "signed_in"
    SIGN_IN_FAILED = "sign_in_failed"
    SIGNED_OUT = "signed_out"


# Signing in happens to no record, so these entries have no view.
SIGN_IN_EVENTS = (
    AuditEvent.SIGNED_IN,
    AuditEvent.SIGN_IN_FAILED,
    AuditEvent.SIGNED_OUT,
)


# One change is the value before and the value after, as JSON.
Change = tuple[Any, Any]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One thing that happened in the admin, who did it, and from where.

    `user` is the name the admin shows for the person, and `user_key` what
    `AuthProvider.identity` returns for them, which does not change when the
    name does. `ip` and `user_agent` say where the request came from.
    `error` holds why something failed; it is empty when it worked.
    `inputs` holds the values an action was run with, secrets masked.
    """

    view: str
    record_key: str
    event: AuditEvent
    changes: Mapping[str, Change] = field(default_factory=dict)
    record_title: str | None = None
    action: str | None = None
    batch: str | None = None
    user: str | None = None
    message: str | None = None
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    id: int | None = None
    user_key: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    error: str | None = None
    inputs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Whether what the entry describes worked."""
        return self.error is None

    def as_row(self) -> dict[str, Any]:
        """The entry as a row for the audit table."""
        return {
            "occurred_at": self.occurred_at,
            "view": self.view,
            "record_key": self.record_key,
            "record_title": self.record_title,
            "event": self.event.value,
            "action": self.action,
            "batch": self.batch,
            "user": self.user,
            "changes": {name: list(pair) for name, pair in self.changes.items()},
            "message": self.message,
            "user_key": self.user_key,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "error": self.error,
            "inputs": dict(self.inputs) or None,
        }

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> "AuditEntry":
        """Read an entry back from a row of the audit table."""
        return cls(
            id=row["id"],
            occurred_at=row["occurred_at"],
            view=row["view"],
            record_key=row["record_key"],
            record_title=row["record_title"],
            event=AuditEvent(row["event"]),
            action=row["action"],
            batch=row["batch"],
            user=row["user"],
            changes={
                name: (pair[0], pair[1])
                for name, pair in (row["changes"] or {}).items()
            },
            message=row["message"],
            user_key=row.get("user_key"),
            ip=row.get("ip"),
            user_agent=row.get("user_agent"),
            error=row.get("error"),
            inputs=row.get("inputs") or {},
        )


def as_json(value: Any) -> Any:
    """Turn a column value into something JSON can hold, readably."""
    # An enum comes first: a string enum is also a str, and the name is what
    # the admin shows and stores.
    if isinstance(value, Enum):
        return value.name
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple | set):
        return [as_json(item) for item in value]
    return str(value)


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Change]:
    """The fields whose value changed, with what they were and are now."""
    return {
        name: (before.get(name), after.get(name))
        for name in after
        if before.get(name) != after.get(name)
    }
