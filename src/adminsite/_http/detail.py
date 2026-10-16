"""A record's own page, read only, with its links and its history."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.forms import rows_for_actions
from adminsite._http.history import describe
from adminsite._http.inline_tables import child_tables
from adminsite._http.requests import find_view, key_of, load_or_404
from adminsite._http.urls import Urls
from adminsite.audit import AuditQuery
from adminsite.exceptions import PermissionDeniedError
from adminsite.fields import RelationField
from adminsite.i18n import format_number, listed
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "HISTORY_LIMIT",
    "MANY_LINKS_SHOWN",
    "LinkedMany",
    "computed_needs",
    "detail",
    "link_urls",
    "many_links",
    "many_links_named",
    "view_that_opens",
]


# How many entries a record's History tab shows before sending the rest
# to the Activity page, which pages through them.
HISTORY_LIMIT = 20


async def detail(admin: "Admin", request: Request) -> Response:
    """One record, read only."""
    view = find_view(admin, request)
    await view._ensure(Permission.VIEW_DETAIL, request=request)
    # A to-many link is read a few records at a time below, never loaded
    # whole: an invoice may cover thousands of records.
    counted = many_links(view, view._pages.detail_fields(request), request)
    # Unless a computed field on the page reads it, which needs it whole.
    needed = computed_needs(
        view, (*view._pages.form_fields(request), *view._pages.detail_fields(request))
    )
    record = await load_or_404(
        admin,
        view,
        request,
        paths=[
            path
            for path in view._pages.load_paths(request)
            if path not in counted or path in needed
        ],
    )

    paths = view._pages.detail_fields(request, record)
    async with admin.database.session() as session:
        await view._reader.load_values(session, [record], paths, request=request)
    opens = await link_urls(admin, view, record, paths, request)
    many = await many_links_named(admin, view, record, counted, request)
    # A to-many link was named above, even when it holds nothing: the record
    # never loaded it, so reading it now would fail.
    rows = [
        (
            path,
            view._fields.label_for(path),
            many[path].text if path in many else view._fields.display(record, path),
            opens.get(path, ""),
        )
        for path in paths
    ]
    key = view._fields.identity_of(record)

    allowed_actions = [
        item
        for item in view._actions.on("record", request)
        if await view.allows(item.permission, request=request, record=record)
    ]

    history = None
    older_history = None
    if admin.audit is not None and await view.allows(
        Permission.HISTORY, request=request, record=record
    ):
        query = AuditQuery(view=view.name, record_key=key)
        found = await admin.audit.find(query, limit=HISTORY_LIMIT + 1)
        history = describe(admin, found[:HISTORY_LIMIT], request)
        # The rest are paged through on the Activity page.
        if len(found) > HISTORY_LIMIT:
            older_history = Urls(request).activity(view=view.name, record=key)

    return await admin.render(
        "detail.html",
        request,
        {
            "view": view,
            "record": record,
            "key": key,
            "heading": view.get_record_title(record),
            "rows": rows,
            "layout": view._pages.arranged(paths, form=False),
            "many": many,
            "children": child_tables(view, record, request),
            "history": history,
            "older_history": older_history,
            "record_actions": allowed_actions,
            "single_actions": allowed_actions,
            "action_rows": await rows_for_actions(
                admin, view, allowed_actions, request
            ),
            "can_edit": await view.allows(
                Permission.EDIT, request=request, record=record
            ),
            "can_delete": await view.allows(
                Permission.DELETE, request=request, record=record
            ),
        },
    )


# How many records of a to-many link the record page names before it
# says how many more there are.
MANY_LINKS_SHOWN = 20


def many_links(
    view: ModelView[Any], paths: Sequence[str], request: Request
) -> set[str]:
    """The paths that are to-many links of the record, shown as a list of names.

    A child table the view edits inline is left alone: it is shown whole.
    """
    inlines = {inline.name for inline in view.get_inlines(request, None)}
    found = set()
    for path in paths:
        if "." in path or path in inlines or path not in view._schema.relations:
            continue
        item = view._fields.field_for(path)
        if isinstance(item, RelationField) and item.collection:
            found.add(path)
    return found


def computed_needs(view: ModelView[Any], paths: Sequence[str]) -> set[str]:
    """What the computed fields among these paths read from the record, as paths."""
    # A need may be written as an attribute, Order.items, which is not "items".
    return {
        needed
        for path in paths
        for needed in view._fields.needs_of(view._fields.field_for(path))
    }


@dataclass(frozen=True)
class LinkedMany:
    """The first records a link to many holds, and how many more it holds."""

    # Each record's name, and its page, or "" where no view opens it.
    names: list[tuple[str, str]]
    more: int

    @property
    def text(self) -> str:
        """The names as one line, saying how many more there are."""
        text = listed(name for name, _url in self.names)
        if self.more > 0:
            text += _(" and {count} more", count=format_number(self.more))
        return text


async def many_links_named(
    admin: "Admin",
    view: ModelView[Any],
    record: Any,
    paths: set[str],
    request: Request,
) -> dict[str, LinkedMany]:
    """The first records of each to-many link, named, each with its own page."""
    if not paths:
        return {}
    urls = Urls(request)
    shown = {}
    async with admin.database.session() as session:
        for path in sorted(paths):
            item = view._fields.field_for(path)
            if not isinstance(item, RelationField):
                continue
            held, total = await view._reader.fetch_related(
                session, record, path, limit=MANY_LINKS_SHOWN, request=request
            )
            names = [
                (
                    view._fields.name_linked(item, one),
                    urls.detail(opener, opener._fields.identity_of(one))
                    if opener is not None
                    else "",
                )
                for one, opener in held
            ]
            shown[path] = LinkedMany(names, total - len(held))
    return shown


async def link_urls(
    admin: "Admin",
    view: ModelView[Any],
    record: Any,
    paths: Sequence[str],
    request: Request,
) -> dict[str, str]:
    """Where each link to one record leads, by path, as its row's value links there.

    A link is left out where no view lets this user open the record.
    """
    urls = Urls(request)
    found = {}
    for path in paths:
        item = view._fields.field_for(path)
        if not isinstance(item, RelationField) or item.collection or "." in path:
            continue
        value = view._fields.value_at(record, path, seen=True)
        # A record its view keeps from this user shows as Hidden, with no link.
        if not value:
            continue
        target = await view_that_opens(admin, item, value, request)
        if target is not None:
            found[path] = urls.detail(target, target._fields.identity_of(value))
    return found


async def view_that_opens(
    admin: "Admin", item: RelationField, value: Any, request: Request
) -> ModelView[Any] | None:
    """The view a link to this related record opens, if any will.

    The relation's own view when it names one. Otherwise the first view of
    the model, unless the model has several: then the first whose scope
    holds this record, so a record another view leaves out still opens
    rather than answering 404.
    """
    candidates = (
        [admin.views.for_relation(item)]
        if item.view is not None
        else admin.views.all_for_model(item.related_model)
    )
    allowed = [
        candidate
        for candidate in candidates
        if candidate is not None
        and await candidate.allows(
            Permission.VIEW_DETAIL, request=request, record=value
        )
    ]
    if len(allowed) <= 1 or item.view is not None:
        return allowed[0] if allowed else None
    async with admin.database.session() as session:
        for candidate in allowed:
            try:
                found = await candidate._reader.fetch_record(
                    session,
                    key_of(candidate._fields.identity_of(value)),
                    request=request,
                )
            except PermissionDeniedError:
                continue
            if found is not None:
                return candidate
    return None
