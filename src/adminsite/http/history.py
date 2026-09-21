from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from adminsite.audit import AuditEntry, AuditEvent
from adminsite.exceptions import AdminSiteError
from adminsite.fields import DateTimeField
from adminsite.text import humanize

if TYPE_CHECKING:
    from adminsite.admin import Admin

_WHEN = DateTimeField("occurred_at")


@dataclass(frozen=True, slots=True)
class ChangeLine:
    """One field that changed, ready to show."""

    label: str
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """One audit entry, worded for a person."""

    entry: AuditEntry
    when: str
    who: str
    what: str
    lines: Sequence[ChangeLine]
    view_label: str


def describe(admin: "Admin", entries: Sequence[AuditEntry]) -> list[HistoryItem]:
    """Turn audit entries into lines a person can read."""
    items = []
    for entry in entries:
        view = admin.views.find(entry.view)
        items.append(
            HistoryItem(
                entry=entry,
                when=_WHEN.display(entry.occurred_at),
                who=entry.user or "Someone",
                what=_verb(entry),
                lines=[
                    ChangeLine(
                        label=_label(view, name),
                        before=_text(before),
                        after=_text(after),
                    )
                    for name, (before, after) in entry.changes.items()
                ],
                view_label=view.label if view is not None else humanize(entry.view),
            )
        )
    return items


def _verb(entry: AuditEntry) -> str:
    if entry.event is AuditEvent.CREATED:
        return "created"
    if entry.event is AuditEvent.DELETED:
        return "deleted"
    if entry.event is AuditEvent.ACTION:
        return f"ran {entry.action}" if entry.action else "ran an action"
    return "changed"


def _label(view: object, name: str) -> str:
    label_for = getattr(view, "label_for", None)
    if label_for is None:
        return humanize(name)
    try:
        return str(label_for(name))
    except AdminSiteError:
        # The field has gone from the model since the entry was written.
        return humanize(name)


def _text(value: object) -> str:
    if value is None or value == "":
        return "empty"
    return str(value)
