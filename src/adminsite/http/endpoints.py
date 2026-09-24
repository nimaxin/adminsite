from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, StreamingResponse

from adminsite.actions import Selection
from adminsite.audit import AuditEntry, AuditEvent, AuditQuery, actor_of
from adminsite.audit.actor import NAME_LIMIT
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.dashboard import load_dashboard
from adminsite.exceptions import (
    AdminSiteError,
    PermissionDeniedError,
    RefusedError,
    SignInRefused,
)
from adminsite.fields import FileField, RelationField
from adminsite.http import importing
from adminsite.http.activity import (
    ACTIVITY_PAGE,
    event_choices,
    position_of,
    read_filters,
)
from adminsite.http.export import stream_csv
from adminsite.http.forms import (
    Choice,
    FormRow,
    build_rows,
    rows_for_inputs,
    title_for,
)
from adminsite.http.history import describe
from adminsite.http.inlines import build_inline_tables, child_tables
from adminsite.http.listing import (
    active_view,
    as_context,
    build_panels,
    read_list_request,
    wants_partial,
)
from adminsite.http.picker import RESULT_LIMIT, Picker
from adminsite.http.saved import delete_view, owner_of, save_view, saved_for
from adminsite.http.templating import add_message
from adminsite.http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.query import CountMode
from adminsite.saved_views import clean_query
from adminsite.security import Permission
from adminsite.security.csrf import FIELD_NAME, TOKEN_HEADER, is_valid
from adminsite.views import ModelView
from adminsite.views.writing import FormResult

if TYPE_CHECKING:
    from adminsite.admin import Admin

# How many entries a record's History tab shows before sending the rest
# to the Activity page, which pages through them.
HISTORY_LIMIT = 20


async def index(admin: "Admin", request: Request) -> Response:
    """The overview: the dashboard cards this user may see."""
    cards = await load_dashboard(admin, request, admin.dashboard)
    return await admin.render("index.html", request, {"cards": cards, "view": None})


async def list_records(admin: "Admin", request: Request) -> Response:
    """One page of records, with the search, filters and sort applied."""
    view = find_view(admin, request)
    read = read_list_request(request, view)

    spec = view.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        page=read.page,
        after=read.after,
        before=read.before,
        paths=read.columns,
        size=read.size,
    )
    async with admin.database.session() as session:
        page = await view.fetch_page(session, spec, request=request)
        panels = await build_panels(view, session, spec, request)

    context = as_context(view, request, spec, page, panels, read)
    context["can_create"] = await view.allows(Permission.CREATE, request=request)
    context["can_export"] = await view.allows(Permission.EXPORT, request=request)
    context["can_detail"] = await view.allows(Permission.DETAIL, request=request)
    context["can_edit"] = await view.allows(Permission.EDIT, request=request)
    context["can_import"] = await view.allows(Permission.IMPORT, request=request)
    # Offer only the actions this user may run.
    allowed = [
        item
        for item in context["actions"]
        if await view.allows(item.permission, request=request)
    ]
    context["actions"] = allowed
    context["view_actions"] = [
        item
        for item in context["view_actions"]
        if await view.allows(item.permission, request=request)
    ]
    record_actions = [
        item
        for item in context["record_actions"]
        if await view.allows(item.permission, request=request)
    ]
    context["record_actions"] = record_actions
    # A record action can be refused for one record and allowed for the next.
    context["row_actions"] = {
        view.identity_of(record): [
            item
            for item in record_actions
            if await view.allows(item.permission, request=request, record=record)
        ]
        for record in page
    }
    context["single_actions"] = [*record_actions, *context["view_actions"]]
    context["saving_views"] = admin.saved_views is not None
    context["saved_views"] = await saved_for(admin, view, request)
    context["view_owner"] = owner_of(admin, request)
    context["active_view"] = active_view(context["saved_views"], request.url.query)

    template = "_table.html" if wants_partial(request) else "list.html"
    return await admin.render(template, request, context)


async def stored_file(admin: "Admin", request: Request) -> Response:
    """A file kept by one of a view's file fields, for whoever may open the view."""
    view = find_view(admin, request)
    await view.ensure(Permission.VIEW, request=request)
    item = field_or_404(view, request.path_params["path"])
    if not isinstance(item, FileField):
        raise HTTPException(status_code=404, detail=_("No such file."))
    key = request.path_params["key"]
    return await item.storage.response(key, item.content_type(key))


async def choose_language(admin: "Admin", request: Request) -> Response:
    """Switch the admin to another language, and go back where we were."""
    form = await read_form(request)
    wanted = str(form.get("language", ""))
    back = str(form.get("next", "")) or Urls(request).index()
    # Only a path inside this site, so the form cannot send anyone elsewhere.
    if not back.startswith("/") or back.startswith("//"):
        back = Urls(request).index()
    response = RedirectResponse(back, status_code=303)
    if wanted in admin.languages:
        response.set_cookie(
            "adminsite_language",
            wanted,
            max_age=365 * 24 * 3600,
            path=Urls(request).index(),
            samesite="lax",
        )
    return response


async def custom_page(admin: "Admin", request: Request) -> Response:
    """A page of the project's own, shown or sent a form."""
    name = request.path_params["page"]
    page = admin.pages.get(name)
    if page is None:
        raise HTTPException(
            status_code=404, detail=_("No page at {name}.", name=repr(name))
        )
    if not await page.allows(request):
        raise PermissionDeniedError("open", page.label)
    if request.method == "POST":
        return await page.post(request, await read_form(request))
    return await page.get(request)


async def import_form(admin: "Admin", request: Request) -> Response:
    """The page for choosing a file to import."""
    return await importing.show_form(admin, request, find_view(admin, request))


async def import_template(admin: "Admin", request: Request) -> Response:
    """An empty CSV with the columns an import takes."""
    view = find_view(admin, request)
    await view.ensure(Permission.IMPORT, request=request)
    return importing.template_response(view, request)


async def import_preview(admin: "Admin", request: Request) -> Response:
    """Check an uploaded file and show what importing it would do."""
    view = find_view(admin, request)
    form = await read_form(request)
    return await importing.preview(admin, request, view, form)


async def import_records(admin: "Admin", request: Request) -> Response:
    """Import a file that was previewed."""
    view = find_view(admin, request)
    await read_form(request)
    return await importing.run(admin, request, view)


async def save_list_view(admin: "Admin", request: Request) -> Response:
    """Keep the current search, filters, sort and columns under a name."""
    view = find_view(admin, request)
    form = await read_form(request)
    return await save_view(admin, request, view, form)


async def delete_list_view(admin: "Admin", request: Request) -> Response:
    """Remove a saved view."""
    view = find_view(admin, request)
    await read_form(request)
    return await delete_view(admin, request, view)


async def create_form(admin: "Admin", request: Request) -> Response:
    """The empty form for adding a record."""
    view = find_view(admin, request)
    await view.ensure(Permission.CREATE, request=request)

    async with admin.database.session() as session:
        rows = await build_rows(admin, view, session, request=request)
        inlines = await build_inline_tables(admin, view, session, request=request)

    context = form_context(view, rows, request)
    context["inlines"] = inlines
    return await admin.render("form.html", request, context)


async def create_record(admin: "Admin", request: Request) -> Response:
    """Save a new record, or show the form again with what went wrong."""
    view = find_view(admin, request)
    await view.ensure(Permission.CREATE, request=request)

    submitted = await read_form(request)
    result = view.parse_form(submitted, request=request)
    async with admin.database.session() as session:
        if not result.ok:
            return await form_again(
                admin, view, session, request, result, submitted=submitted
            )
        try:
            record = await view.save(
                session,
                result.values,
                request=request,
                inline_rows=result.inline_rows,
            )
            await session.commit()
        except AdminSiteError as error:
            return await form_again(
                admin, view, session, request, result, error, submitted=submitted
            )

        key = view.identity_of(record)

    add_message(request, _("{thing} created.", thing=view.label))
    return RedirectResponse(await after_save(admin, view, request, key), 303)


async def detail(admin: "Admin", request: Request) -> Response:
    """One record, read only."""
    view = find_view(admin, request)
    await view.ensure(Permission.DETAIL, request=request)
    record = await load_or_404(admin, view, request)

    paths = view.get_detail_fields(request, record)
    links = await linked_records(admin, view, record, paths, request)
    beside = {link.path for link in links}
    rows = [
        (path, view.label_for(path), view.display(record, path))
        for path in paths
        if path not in beside
    ]
    key = view.identity_of(record)

    allowed_actions = [
        item
        for item in view.actions_on("record", request)
        if await view.allows(item.permission, request=request, record=record)
    ]

    history = None
    older_history = None
    if admin.audit is not None and await view.allows(
        Permission.HISTORY, request=request, record=record
    ):
        query = AuditQuery(view=view.name, record_key=key)
        found = await admin.audit.find(query, limit=HISTORY_LIMIT + 1)
        history = describe(admin, found[:HISTORY_LIMIT])
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
            "heading": view.title_of(record),
            "rows": rows,
            "links": links,
            "children": child_tables(view, record, request),
            "history": history,
            "older_history": older_history,
            "record_actions": allowed_actions,
            "single_actions": allowed_actions,
            "action_rows": {
                item.name: rows_for_inputs(item.inputs) for item in allowed_actions
            },
            "can_edit": await view.allows(
                Permission.EDIT, request=request, record=record
            ),
            "can_delete": await view.allows(
                Permission.DELETE, request=request, record=record
            ),
        },
    )


@dataclass(frozen=True)
class LinkedRecord:
    """A record this one points at, shown beside its details."""

    path: str
    label: str
    title: str
    url: str

    @property
    def initials(self) -> str:
        """Up to two letters to stand for the record, taken from its name."""
        letters = [word[0] for word in self.title.split() if word[:1].isalpha()]
        return "".join(letters[:2]).upper()


async def linked_records(
    admin: "Admin",
    view: ModelView,
    record: Any,
    paths: Sequence[str],
    request: Request,
) -> list[LinkedRecord]:
    """The single records a page links to, each with a way to open it.

    A link to one record reads better as a card beside the details than as
    one more line among them. It opens the record's own page where its view
    lets this user see one.
    """
    urls = Urls(request)
    found = []
    for path in paths:
        item = view.field_for(path)
        if not isinstance(item, RelationField) or item.collection or "." in path:
            continue
        value = view.value_at(record, path)
        if value is None:
            continue
        target = admin.views.for_model(item.target)
        opens = ""
        if target is not None and await target.allows(
            Permission.DETAIL, request=request, record=value
        ):
            opens = urls.detail(target, target.identity_of(value))
        found.append(
            LinkedRecord(path, view.label_for(path), view.display(record, path), opens)
        )
    return found


async def activity(admin: "Admin", request: Request) -> Response:
    """The log across the admin, newest first, filtered and a page at a time."""
    if admin.audit is None:
        raise HTTPException(status_code=404, detail=_("Auditing is not switched on."))

    # Filtering in the query, not afterwards, so every page is full of
    # entries this person may read.
    readable = await admin.history_views(request)
    allowed = [view.name for view in readable]
    # Signing in happens to no record, so its entries have no view.
    sign_ins = admin.auth is not None and await admin.auth.may_read_sign_ins(
        request, reads_everything=len(readable) == len(admin.views.views)
    )
    if sign_ins:
        allowed.append("")
    if not allowed:
        raise PermissionDeniedError(Permission.HISTORY.value, _("the activity"))

    choices = event_choices(sign_ins)
    filters = read_filters(
        request.query_params, allowed, [event for event, _label in choices]
    )
    # One more than a page says whether there is a page after this one.
    found = await admin.audit.find(filters.query(allowed), limit=ACTIVITY_PAGE + 1)
    entries = found[:ACTIVITY_PAGE]

    urls = Urls(request)
    older = (
        urls.activity(**filters.params(older=position_of(entries[-1])))
        if len(found) > ACTIVITY_PAGE
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
            "items": describe(admin, entries),
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
                    request, Permission.VIEW, Permission.DETAIL
                )
            },
            "view": None,
            "on_activity": True,
        },
    )


async def edit_form(admin: "Admin", request: Request) -> Response:
    """The form for changing a record."""
    view = find_view(admin, request)
    record = await load_or_404(admin, view, request)
    await view.ensure(Permission.EDIT, request=request, record=record)

    async with admin.database.session() as session:
        rows = await build_rows(admin, view, session, record=record, request=request)
        inlines = await build_inline_tables(
            admin, view, session, record=record, request=request
        )

    context = form_context(view, rows, request, record)
    context["inlines"] = inlines
    context["can_delete"] = await view.allows(
        Permission.DELETE, request=request, record=record
    )
    return await admin.render("form.html", request, context)


async def edit_record(admin: "Admin", request: Request) -> Response:
    """Save a change, or show the form again with what went wrong."""
    view = find_view(admin, request)
    key = read_key(request)
    submitted = await read_form(request)

    async with admin.database.session() as session:
        record = await view.fetch_record(
            session, key, paths=view.get_load_paths(request), request=request
        )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        await view.ensure(Permission.EDIT, request=request, record=record)

        result = view.parse_form(submitted, record=record, request=request)
        if not result.ok:
            return await form_again(
                admin, view, session, request, result, None, record, submitted
            )
        try:
            await view.save(
                session,
                result.values,
                record=record,
                request=request,
                inline_rows=result.inline_rows,
            )
            await session.commit()
        except AdminSiteError as error:
            # The rollback expired the record, and the form is about to
            # read it again, which an async session cannot do on the fly.
            record = await view.fetch_record(
                session, key, paths=view.get_load_paths(request), request=request
            )
            return await form_again(
                admin, view, session, request, result, error, record, submitted
            )

    add_message(request, _("{thing} saved.", thing=view.label))
    return RedirectResponse(await after_save(admin, view, request, key_text(key)), 303)


async def delete_record(admin: "Admin", request: Request) -> Response:
    """Delete one record and go back to the list."""
    view = find_view(admin, request)
    await read_form(request)

    async with admin.database.session() as session:
        record = await view.fetch_record(
            session,
            read_key(request),
            paths=view.get_load_paths(request),
            request=request,
        )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        await view.ensure(Permission.DELETE, request=request, record=record)
        try:
            await view.delete(session, record, request=request)
            await session.commit()
        except RefusedError as error:
            add_message(request, str(error), kind="error")
            return RedirectResponse(Urls(request).list(view), status_code=303)

    add_message(request, _("{thing} deleted.", thing=view.label))
    return RedirectResponse(Urls(request).list(view), status_code=303)


async def lookup(admin: "Admin", request: Request) -> Response:
    """The records a relation field offers, narrowed by what was typed.

    A picker only appears on a form, so this needs the permission that
    opens one. The records themselves come through the target's own view,
    so its scope and its permissions apply here as on any other page.
    """
    view = find_view(admin, request)
    if not await view.allows(Permission.CREATE, request=request):
        await view.ensure(Permission.EDIT, request=request)

    path = request.path_params["path"]
    item = field_or_404(view, path)
    if not isinstance(item, RelationField):
        raise HTTPException(
            status_code=404, detail=_("{path} is not a link.", path=repr(path))
        )

    picker = Picker(admin, item, request)
    async with admin.database.session() as session:
        page = await picker.page(
            session,
            search=request.query_params.get("q", "").strip(),
            limit=RESULT_LIMIT,
        )
        choices = [
            Choice(
                picker.repository.identity_of(record),
                title_for(admin, item, record),
            )
            for record in page.rows
        ]

    return await admin.render(
        "_lookup.html", request, {"view": view, "choices": choices}
    )


def form_context(
    view: ModelView,
    rows: list[FormRow],
    request: Request,
    record: Any = None,
) -> dict[str, Any]:
    """What both the create form and the edit form need."""
    urls = Urls(request)
    editing = record is not None
    key = view.identity_of(record) if editing else ""
    return {
        "view": view,
        "rows": rows,
        "record": record,
        "key": key,
        "heading": view.title_of(record)
        if editing
        else _("New {thing}", thing=view.label.lower()),
        "submit_label": _("Save changes")
        if editing
        else _("Create {thing}", thing=view.label.lower()),
        "action": urls.edit(view, key) if editing else urls.create(view),
        "cancel_url": urls.detail(view, key) if editing else urls.list(view),
        "can_delete": view.can_delete,
        "form_error": "",
    }


async def form_again(
    admin: "Admin",
    view: ModelView,
    session: SessionAdapter,
    request: Request,
    result: FormResult,
    error: Exception | None = None,
    record: Any = None,
    submitted: Mapping[str, Any] | None = None,
) -> Response:
    """Show the form again, keeping what was typed and saying what failed."""
    rows = await build_rows(
        admin,
        view,
        session,
        record=record,
        submitted=result.values,
        typed=submitted,
        errors=result.errors,
        request=request,
    )
    context = form_context(view, rows, request, record)
    context["inlines"] = await build_inline_tables(
        admin,
        view,
        session,
        record=record,
        submitted=submitted or {},
        errors=result.errors,
        request=request,
    )
    # A refusal about one field belongs next to that field, not above the
    # form, so it reads like any other problem with what was typed.
    beside_field = (
        isinstance(error, RefusedError)
        and error.field in {row.path for row in rows}
        and error.field
    )
    if beside_field:
        for row in rows:
            if row.path == beside_field:
                row.error = str(error)
    context["form_error"] = "" if beside_field or error is None else str(error)
    if record is not None:
        context["can_delete"] = await view.allows(
            Permission.DELETE, request=request, record=record
        )
    return await admin.render("form.html", request, context, status_code=422)


async def after_save(
    admin: "Admin", view: ModelView, request: Request, key: str
) -> str:
    """Where a save lands: the record's page, or the list without one."""
    urls = Urls(request)
    if await view.allows(Permission.DETAIL, request=request):
        return urls.detail(view, key)
    return urls.list(view)


async def load_or_404(admin: "Admin", view: ModelView, request: Request) -> Any:
    """Load the record the URL names, or raise a 404."""
    async with admin.database.session() as session:
        record = await view.fetch_record(
            session,
            read_key(request),
            paths=view.get_load_paths(request),
            request=request,
        )
    if record is None:
        raise HTTPException(status_code=404, detail=_("No such record."))
    return record


def find_view(admin: "Admin", request: Request) -> ModelView:
    """The view the URL names, or a 404."""
    name = request.path_params.get("view", "")
    view = admin.views.find(name)
    if view is None:
        raise HTTPException(
            status_code=404, detail=_("No page at {name}.", name=repr(name))
        )
    return view


def field_or_404(view: ModelView, path: str) -> Any:
    """The field a path in the URL names, or a 404 when it names none."""
    try:
        return view.field_for(path)
    except AdminSiteError:
        raise HTTPException(
            status_code=404, detail=_("No field at {path}.", path=repr(path))
        ) from None


def read_key(request: Request) -> Any:
    """The primary key out of the URL, as one value or a tuple."""
    return key_of(request.path_params["key"])


def key_of(raw: str) -> Any:
    """A key written as text, as one value or a tuple for a composite key."""
    parts = raw.split(",")
    return tuple(parts) if len(parts) > 1 else raw


def key_text(key: Any) -> str:
    """Write a key back the way it appears in a URL."""
    return ",".join(key) if isinstance(key, tuple) else str(key)


async def read_form(request: Request) -> dict[str, Any]:
    """Read a submitted form, after checking it came from the admin."""
    form = await request.form()
    data: dict[str, Any] = {}
    for key in form:
        values = form.getlist(key)
        data[key] = values if len(values) > 1 else values[0]

    submitted = data.pop(FIELD_NAME, None) or request.headers.get(TOKEN_HEADER)
    token = submitted if isinstance(submitted, str) else None
    if not is_valid(request, token):
        raise HTTPException(status_code=403, detail=_("This form has expired."))
    return data


async def login_form(admin: "Admin", request: Request) -> Response:
    """The sign in page."""
    return await admin.render("login.html", request, {"error": ""})


async def login(admin: "Admin", request: Request) -> Response:
    """Check the details and let the user in."""
    if admin.auth is None:
        raise HTTPException(status_code=404, detail=_("Signing in is not set up."))

    submitted = await read_form(request)
    username = str(submitted.get("username", ""))
    try:
        user = await admin.auth.sign_in(
            request, username, str(submitted.get("password", ""))
        )
    except SignInRefused as refused:
        await note_sign_in(
            admin,
            request,
            AuditEvent.SIGN_IN_FAILED,
            user=refused.user,
            tried=username,
            reason=refused.reason,
        )
        user = None
    else:
        if user is None:
            await note_sign_in(
                admin,
                request,
                AuditEvent.SIGN_IN_FAILED,
                tried=username,
                reason=_("The details did not match."),
            )
    if user is None:
        message = await admin.auth.sign_in_failed(request, username)
        return await admin.render(
            "login.html", request, {"error": message}, status_code=401
        )
    await note_sign_in(admin, request, AuditEvent.SIGNED_IN, user=user)
    return RedirectResponse(Urls(request).index(), status_code=303)


async def logout(admin: "Admin", request: Request) -> Response:
    """Sign the user out."""
    await read_form(request)
    if admin.auth is not None:
        user = await admin.auth.current_user(request)
        await admin.auth.sign_out(request)
        if user is not None:
            await note_sign_in(admin, request, AuditEvent.SIGNED_OUT, user=user)
    return RedirectResponse(Urls(request).login(), status_code=303)


async def note_sign_in(
    admin: "Admin",
    request: Request,
    event: AuditEvent,
    *,
    user: Any = None,
    tried: str = "",
    reason: str | None = None,
) -> None:
    """Write down a sign in, a failed one or a sign out, if auditing is on.

    It happens to no record, so the entry has no view. A failed attempt
    without an account behind it is filed under the name that was tried.
    """
    if admin.audit is None or admin.auth is None:
        return
    actor = actor_of(request)
    if user is not None:
        actor["user"] = str(user)[:NAME_LIMIT]
        actor["user_key"] = admin.auth.identity(user)
    else:
        actor["user"] = tried[:NAME_LIMIT] or None
    await admin.audit.record(
        [AuditEntry(view="", record_key="", event=event, error=reason, **actor)]
    )


async def run_action(admin: "Admin", request: Request) -> Response:
    """Run an action: over the chosen rows, over one record, or over the view."""
    view = find_view(admin, request)
    try:
        found = view.action_named(request.path_params["name"], request)
    except AdminSiteError:
        raise HTTPException(status_code=404, detail=_("No such action.")) from None

    submitted = await read_form(request)
    inputs = view.parse_action_inputs(found, submitted)
    if not inputs.ok:
        problems = "; ".join(
            f"{item.label}: {inputs.errors[item.name]}"
            for item in found.inputs
            if item.name in inputs.errors
        )
        add_message(
            request,
            _(
                "{action} was not done. {problems}",
                action=found.label,
                problems=problems,
            ),
            kind="error",
        )
        return back_from_action(admin, request, view, found, submitted)

    async with admin.database.session() as session:
        try:
            answer = await perform(
                admin, request, view, found, session, submitted, inputs.values
            )
            await session.commit()
        except RefusedError as error:
            await session.rollback()
            add_message(request, str(error), kind="error")
            return back_from_action(admin, request, view, found, submitted)
        except IntegrityError:
            await session.rollback()
            add_message(
                request,
                _(
                    "{action} was not done, because other records still refer to "
                    "some of these.",
                    action=found.label,
                ),
                kind="error",
            )
            return back_from_action(admin, request, view, found, submitted)
        except Exception:
            # Undone before the error page, so the audit log can write the
            # attempt down as failed.
            await session.rollback()
            raise
        # An action that answers with a file or JSON sends it as it is.
        if isinstance(answer, Response):
            return answer

    add_message(request, answer)
    return back_from_action(admin, request, view, found, submitted)


async def perform(
    admin: "Admin",
    request: Request,
    view: ModelView,
    found: Any,
    session: SessionAdapter,
    submitted: Mapping[str, Any],
    values: Mapping[str, Any],
) -> Any:
    """Run one action, whatever it acts on."""
    if found.on_view:
        return await view.run_view_action(
            found, session, request=request, values=values
        )

    keys = submitted.get("keys", [])
    chosen = [str(key) for key in (keys if isinstance(keys, list) else [keys])]

    if found.on_record:
        record = None
        if chosen:
            record = await view.fetch_record(
                session,
                key_of(chosen[0]),
                paths=view.get_load_paths(request),
                request=request,
            )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        return await view.run_record_action(
            found, record, session, request=request, values=values
        )

    read = read_list_request(request, view)
    spec = view.build_spec(
        request=request, search=read.search, filters=read.values, sort=read.sort
    )
    selection = Selection(
        view=view,
        session=session,
        spec=spec,
        keys=tuple(chosen),
        everything=submitted.get("everything") == "1",
        request=request,
    )
    return await view.run_action(found, selection, request=request, values=values)


def back_from_action(
    admin: "Admin",
    request: Request,
    view: ModelView,
    found: Any,
    submitted: Mapping[str, Any],
) -> RedirectResponse:
    """Where an action lands: the record it ran on, or the list it came from."""
    keys = submitted.get("keys", [])
    chosen = keys if isinstance(keys, list) else [keys]
    if found.on_record and chosen and view.can_detail:
        return RedirectResponse(Urls(request).detail(view, str(chosen[0])), 303)
    return back_to_list(request, view)


async def export_records(admin: "Admin", request: Request) -> Response:
    """Stream the current list as CSV, filters and all."""
    view = find_view(admin, request)
    await view.ensure(Permission.EXPORT, request=request)

    read = read_list_request(request, view)
    spec = view.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        paths=read.columns,
    ).replace(limit=None, offset=0, count=CountMode.NONE, keyset=False)

    if admin.audit is not None:
        # Written when the download starts: the list is the search and the
        # filters, since the rows of a large export are too many to name.
        await admin.audit.record(
            [
                AuditEntry(
                    view=view.name,
                    record_key="",
                    event=AuditEvent.EXPORTED,
                    inputs=list_query(request.url.query),
                    **actor_of(request),
                )
            ]
        )

    filename = f"{view.name}.csv"
    return StreamingResponse(
        stream_csv(admin, view, spec, request, read.columns),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def list_query(query: str) -> dict[str, str | list[str]]:
    """The search, filters, sort and columns a list was asked for, by name."""
    found: dict[str, list[str]] = {}
    for key, value in parse_qsl(clean_query(query)):
        found.setdefault(key, []).append(value)
    return {
        key: values[0] if len(values) == 1 else values for key, values in found.items()
    }


def back_to_list(request: Request, view: ModelView) -> RedirectResponse:
    """Return to the list the action was started from."""
    query = request.url.query
    target = Urls(request).list(view)
    return RedirectResponse(f"{target}?{query}" if query else target, status_code=303)
