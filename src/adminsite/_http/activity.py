from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.history import describe
from adminsite._http.urls import Urls
from adminsite.audit import AuditEntry, AuditEvent, AuditQuery, AuditStore
from adminsite.audit.entry import SIGN_IN_EVENTS
from adminsite.exceptions import PermissionDeniedError
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.views import ModelView

__all__ = [
    "ACTIVITY_PAGE",
    "LARGEST_READ",
    "READS_PER_PAGE",
    "ActivityFilters",
    "activity",
    "event_choices",
    "position_of",
    "read_filters",
    "read_page",
    "read_position",
    "readable_entries",
]

# How many entries one page of the Activity page shows.
ACTIVITY_PAGE = 50
# A page whose entries are mostly kept from the person reading reads on,
# each read twice the one before, up to the largest, and stops after so
# many reads: the next page goes on from there.
LARGEST_READ = 400
READS_PER_PAGE = 6


@dataclass(frozen=True, slots=True)
class ActivityFilters:
    """What the Activity page was asked to show, read from its address."""

    view: str | None = None
    event: AuditEvent | None = None
    user: str | None = None
    since: date | None = None
    until: date | None = None
    record: str | None = None
    older: str | None = None

    def query(self, allowed: Sequence[str]) -> AuditQuery:
        """The entries these filters ask for, among the views one may read."""
        return AuditQuery(
            views=allowed,
            view=self.view,
            record_key=self.record,
            user=self.user,
            events=[self.event] if self.event is not None else (),
            since=_start_of(self.since),
            # The last day counts in full.
            until=_start_of(self.until + timedelta(days=1) if self.until else None),
            older_than=read_position(self.older),
        )

    def params(self, **changes: Any) -> dict[str, Any]:
        """The filters as the address holds them, with some changed."""
        found: dict[str, Any] = {
            "view": self.view,
            "event": self.event.value if self.event is not None else None,
            "user": self.user,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "record": self.record,
            "older": self.older,
        }
        found.update(changes)
        return found

    @property
    def narrowed(self) -> bool:
        """Whether anything beyond the view narrows the list."""
        return any((self.event, self.user, self.since, self.until, self.record))


def read_filters(
    params: QueryParams, allowed: Sequence[str], events: Sequence[AuditEvent]
) -> ActivityFilters:
    """Read the filters, dropping any the person may not use or that do not parse."""
    view = params.get("view") or None
    event = _event(params.get("event"))
    return ActivityFilters(
        view=view if view in allowed and view else None,
        event=event if event in events else None,
        user=_text(params.get("user")),
        since=_day(params.get("since")),
        until=_day(params.get("until")),
        record=_text(params.get("record")),
        older=params.get("older") if read_position(params.get("older")) else None,
    )


def event_choices(sign_ins: bool) -> list[tuple[AuditEvent, str]]:
    """The kinds of entry to filter by, with their names, sign ins if allowed."""
    choices = [
        (AuditEvent.CREATED, _("Created")),
        (AuditEvent.UPDATED, _("Changed")),
        (AuditEvent.DELETED, _("Deleted")),
        (AuditEvent.ACTION, _("Actions")),
        (AuditEvent.EXPORTED, _("Exports")),
        (AuditEvent.SIGNED_IN, _("Sign ins")),
        (AuditEvent.SIGN_IN_FAILED, _("Failed sign ins")),
        (AuditEvent.SIGNED_OUT, _("Sign outs")),
    ]
    return [item for item in choices if sign_ins or item[0] not in SIGN_IN_EVENTS]


def position_of(entry: AuditEntry) -> str:
    """Where a page ends, for the address of the page after it."""
    return f"{entry.occurred_at.isoformat()}_{entry.id}"


def read_position(text: str | None) -> tuple[datetime, int] | None:
    """Read back what `position_of` wrote, or nothing if it does not parse."""
    if not text or "_" not in text:
        return None
    when, _separator, key = text.rpartition("_")
    try:
        return datetime.fromisoformat(when), int(key)
    except ValueError:
        return None


def _event(value: str | None) -> AuditEvent | None:
    try:
        return AuditEvent(value) if value else None
    except ValueError:
        return None


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:200] or None


def _start_of(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min) if day is not None else None


async def readable_entries(
    admin: "Admin",
    entries: Sequence[AuditEntry],
    views: Sequence["ModelView[Any]"],
    request: Request,
) -> list[AuditEntry]:
    """The entries whose record this person may read the history of.

    Each view is asked once about all of its records among the entries. An
    entry about no record, such as an export, needs only its view.
    """
    by_name = {view.name: view for view in views}
    keys: dict[str, set[str]] = {}
    for entry in entries:
        if entry.record_key and entry.view in by_name:
            keys.setdefault(entry.view, set()).add(entry.record_key)
    readable: dict[str, set[str]] = {}
    if keys:
        async with admin.database.session() as session:
            for name, wanted in keys.items():
                readable[name] = await by_name[name]._reader.readable_history(
                    session, wanted, request=request
                )
    return [
        entry
        for entry in entries
        if not entry.record_key or entry.record_key in readable.get(entry.view, ())
    ]


async def read_page(
    admin: "Admin",
    store: AuditStore,
    query: AuditQuery,
    views: Sequence["ModelView[Any]"],
    request: Request,
) -> tuple[list[AuditEntry], AuditEntry | None]:
    """A page of the entries this person may read, and the one the next page follows.

    The query keeps to the views they may read. Which records they may read
    the history of is only known once the entries are read, since the log
    may be kept apart from the records, so a page reads on past the entries
    it leaves out until it is full, the log runs out, or it has read
    READS_PER_PAGE times.
    """
    kept: list[AuditEntry] = []
    found: list[AuditEntry] = []
    position = query.older_than
    for read in range(READS_PER_PAGE):
        # One more than a page says whether there is a page after this one.
        size = min((ACTIVITY_PAGE + 1) * 2**read, LARGEST_READ)
        found = await store.find(replace(query, older_than=position), limit=size)
        kept += await readable_entries(admin, found, views, request)
        if len(kept) > ACTIVITY_PAGE:
            return kept[:ACTIVITY_PAGE], kept[ACTIVITY_PAGE - 1]
        if len(found) < size or found[-1].id is None:
            return kept, None
        position = (found[-1].occurred_at, found[-1].id)
    return kept, found[-1]


async def activity(admin: "Admin", request: Request) -> Response:
    """The log across the admin, newest first, filtered and a page at a time."""
    if admin.audit is None:
        raise HTTPException(status_code=404, detail=_("Auditing is not switched on."))

    readable = await admin.history_views(request)
    allowed = [view.name for view in readable]
    # Signing in happens to no record, so its entries have no view, and are
    # for someone who reads every record of every view.
    everything = len(readable) == len(admin.views.views) and not any(
        view._reader.narrows(request) for view in readable
    )
    sign_ins = admin.auth is not None and await admin.auth.may_read_sign_ins(
        request, reads_everything=everything
    )
    if sign_ins:
        allowed.append("")
    if not allowed:
        raise PermissionDeniedError(Permission.HISTORY.value, _("the activity"))

    choices = event_choices(sign_ins)
    filters = read_filters(
        request.query_params, allowed, [event for event, _label in choices]
    )
    entries, last = await read_page(
        admin, admin.audit, filters.query(allowed), readable, request
    )

    urls = Urls(request)
    older = (
        urls.activity(**filters.params(older=position_of(last)))
        if last is not None
        else None
    )
    tabs = [
        (_("Everything"), urls.activity(**filters.params(view=None, older=None)), None),
        *(
            (
                item.label_plural,
                urls.activity(**filters.params(view=item.name, older=None)),
                item.name,
            )
            for item in readable
        ),
    ]
    return await admin.render(
        "activity.html",
        request,
        {
            "items": describe(admin, entries, request),
            "tabs": tabs,
            "filters": filters,
            "event_choices": choices,
            "older_url": older,
            "newest_url": urls.activity(**filters.params(older=None))
            if filters.older
            else None,
            "clear_url": urls.activity(view=filters.view),
            "detail_views": {
                view.name
                for view in await admin.views_allowing(
                    request, Permission.VIEW, Permission.VIEW_DETAIL
                )
            },
            "view": None,
            "on_activity": True,
        },
    )
