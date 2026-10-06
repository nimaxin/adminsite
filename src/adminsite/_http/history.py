from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from adminsite._text import humanize
from adminsite.audit import AuditEntry, AuditEvent
from adminsite.exceptions import AdminSiteError
from adminsite.fields import DateTimeField
from adminsite.i18n import gettext as _
from adminsite.permissions import RequestAction

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.views import ModelView

__all__ = [
    "ChangeLine",
    "GivenLine",
    "HistoryItem",
    "describe",
]

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


def describe(
    admin: "Admin", entries: Sequence[AuditEntry], request: Request
) -> list[HistoryItem]:
    """Turn audit entries into lines a person can read.

    A field the entry's view keeps from this user on the record page is left
    out, its old and new values with it.
    """
    items = []
    filters: dict[str, dict[str, str]] = {}
    for entry in entries:
        view = admin.views.find(entry.view)
        # An export names each filter as the URL does, which need not be the
        # path of the field it reads, such as customer__email.
        reads: dict[str, str] = {}
        if entry.event is AuditEvent.EXPORTED and view is not None:
            if view.name not in filters:
                filters[view.name] = _filter_fields(view, request)
            reads = filters[view.name]
        items.append(
            HistoryItem(
                entry=entry,
                when=_WHEN.display(_in_utc(entry.occurred_at)),
                who=entry.user or _("Someone"),
                what=_verb(entry, view),
                lines=[
                    ChangeLine(
                        label=_label(view, name),
                        before=_change_text(view, name, before),
                        after=_change_text(view, name, after),
                    )
                    for name, (before, after) in entry.changes.items()
                    if _readable(view, request, name)
                ],
                view_label=view.label if view is not None else humanize(entry.view),
                given=[
                    GivenLine(label=_given_label(view, name), value=_given_text(value))
                    for name, value in entry.inputs.items()
                    if _readable(view, request, reads.get(name, name))
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


def _readable(view: "ModelView[Any] | None", request: Request, name: str) -> bool:
    """Whether this user sees the field an entry names on the record page."""
    return view is None or view._pages.can_access_path(
        request, name, RequestAction.DETAIL
    )


def _filter_fields(view: "ModelView[Any]", request: Request) -> dict[str, str]:
    """The path each of the view's filters reads, by the filter's name."""
    named = view.get_list_filters(request)
    # Built before can_access_field leaves any out, so a filter kept from
    # this user still maps to the field it reads.
    offered = (
        ()
        if named is view.list_filters
        else view._settings.filters("get_list_filters", named)
    )
    return {item.name: item.path for item in (*view._settings.list_filters, *offered)}


def _label(view: "ModelView[Any] | None", name: str) -> str:
    if view is None:
        return humanize(name)
    try:
        return view._fields.label_for(name)
    except AdminSiteError:
        # The field has gone from the model since the entry was written.
        return humanize(name)


def _text(value: object) -> str:
    if value is None or value == "":
        return _("empty")
    return str(value)


def _in_utc(moment: datetime) -> datetime:
    # The log keeps its times in UTC, whatever zone the database keeps.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _change_text(view: "ModelView[Any] | None", name: str, value: object) -> str:
    """What a field held before or after a change, as the log wrote it down.

    The log writes a time in UTC whoever saved it, and says so.
    """
    text = _text(value)
    if value is None or value == "" or view is None:
        return text
    try:
        item = view._fields.field_for(name)
    except AdminSiteError:
        # The field has gone from the model since the entry was written.
        return text
    if isinstance(item, DateTimeField):
        return _("{when} UTC", when=text)
    return text


def _given_label(view: "ModelView[Any] | None", name: str) -> str:
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
