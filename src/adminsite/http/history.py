from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from adminsite.audit import AuditEntry, AuditEvent
from adminsite.exceptions import AdminSiteError
from adminsite.fields import DateTimeField
from adminsite.i18n import gettext as _
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
class GivenLine:
    """One value something was run with, ready to show."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """One audit entry, worded for a person."""

    entry: AuditEntry
    when: str
    who: str
    what: str
    lines: Sequence[ChangeLine]
    view_label: str
    given: Sequence[GivenLine] = ()


def describe(admin: "Admin", entries: Sequence[AuditEntry]) -> list[HistoryItem]:
    """Turn audit entries into lines a person can read."""
    items = []
    for entry in entries:
        view = admin.views.find(entry.view)
        items.append(
            HistoryItem(
                entry=entry,
                when=_WHEN.display(entry.occurred_at),
                who=entry.user or _("Someone"),
                what=_verb(entry, view),
                lines=[
                    ChangeLine(
                        label=_label(view, name),
                        before=_text(before),
                        after=_text(after),
                    )
                    for name, (before, after) in entry.changes.items()
                ],
                view_label=view.label if view is not None else humanize(entry.view),
                given=[
                    GivenLine(label=_given_label(view, name), value=_given_text(value))
                    for name, value in entry.inputs.items()
                ],
            )
        )
    return items


def _verb(entry: AuditEntry, view: object) -> str:
    # An entry for no record in particular names the model instead.
    things = str(getattr(view, "label_plural", "") or humanize(entry.view))
    if entry.event is AuditEvent.EXPORTED:
        return _("exported {things}", things=things)
    if entry.event is AuditEvent.ACTION and entry.view and not entry.record_key:
        return _("ran {action} on {things}", action=entry.action or "", things=things)
    if entry.event is AuditEvent.SIGNED_IN:
        return _("signed in")
    if entry.event is AuditEvent.SIGN_IN_FAILED:
        return _("could not sign in")
    if entry.event is AuditEvent.SIGNED_OUT:
        return _("signed out")
    if entry.event is AuditEvent.CREATED:
        return _("created")
    if entry.event is AuditEvent.DELETED:
        return _("deleted")
    if entry.event is AuditEvent.ACTION:
        if entry.action:
            return _("ran {action}", action=entry.action)
        return _("ran an action")
    return _("changed")


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
        return _("empty")
    return str(value)


def _given_label(view: object, name: str) -> str:
    # The parts of a list's address an export names, beside its filters.
    if name == "q":
        return _("Search")
    if name == "sort":
        return _("Sort")
    if name == "columns":
        return _("Columns")
    return _label(view, name)


def _given_text(value: object) -> str:
    if isinstance(value, dict) and "file" in value:
        return _("{name}, {size} bytes", name=value["file"], size=value.get("size"))
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value)
    return _text(value)
