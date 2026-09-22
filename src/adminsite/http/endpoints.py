from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, StreamingResponse

from adminsite.actions import Selection
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.dashboard import load_dashboard
from adminsite.exceptions import AdminSiteError, PermissionDeniedError, RefusedError
from adminsite.fields import RelationField
from adminsite.http.export import stream_csv
from adminsite.http.forms import Choice, FormRow, build_rows, title_for
from adminsite.http.history import describe
from adminsite.http.inlines import build_inline_tables, child_tables
from adminsite.http.listing import (
    active_view,
    as_context,
    build_panels,
    read_list_request,
    wants_partial,
)
from adminsite.http.saved import delete_view, owner_of, save_view, saved_for
from adminsite.http.templating import add_message
from adminsite.http.urls import Urls
from adminsite.query import CountMode, QuerySpec
from adminsite.security import Permission
from adminsite.security.csrf import FIELD_NAME, TOKEN_HEADER, is_valid
from adminsite.views import ModelView
from adminsite.views.writing import FormResult

if TYPE_CHECKING:
    from adminsite.admin import Admin


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
    )
    async with admin.database.session() as session:
        page = await view.fetch_page(session, spec, request=request)
        panels = await build_panels(view, session, spec, request)

    context = as_context(view, request, spec, page, panels, read)
    context["can_create"] = await view.allows(Permission.CREATE, request=request)
    # Offer only the actions this user may run.
    allowed = [
        item
        for item in context["actions"]
        if await view.allows(item.permission, request=request)
    ]
    context["actions"] = allowed
    context["saving_views"] = admin.saved_views is not None
    context["saved_views"] = await saved_for(admin, view, request)
    context["view_owner"] = owner_of(admin, request)
    context["active_view"] = active_view(context["saved_views"], request.url.query)

    template = "_table.html" if wants_partial(request) else "list.html"
    return await admin.render(template, request, context)


async def custom_page(admin: "Admin", request: Request) -> Response:
    """A page of the project's own, shown or sent a form."""
    name = request.path_params["page"]
    page = admin.pages.get(name)
    if page is None:
        raise HTTPException(status_code=404, detail=f"No page at {name!r}.")
    if not await page.allows(request):
        raise PermissionDeniedError("open", page.label)
    if request.method == "POST":
        return await page.post(request, await read_form(request))
    return await page.get(request)


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

    add_message(request, f"{view.label} created.")
    return RedirectResponse(Urls(request).detail(view, key), status_code=303)


async def detail(admin: "Admin", request: Request) -> Response:
    """One record, read only."""
    view = find_view(admin, request)
    record = await load_or_404(admin, view, request)

    paths = view.get_form_fields(request, record)
    rows = [(path, view.label_for(path), view.display(record, path)) for path in paths]
    key = view.identity_of(record)

    history = None
    if admin.audit is not None and await view.allows(
        Permission.HISTORY, request=request, record=record
    ):
        history = describe(admin, await admin.audit.history(view.name, key))

    return await admin.render(
        "detail.html",
        request,
        {
            "view": view,
            "record": record,
            "key": key,
            "heading": view.title_of(record),
            "rows": rows,
            "children": child_tables(view, record, request),
            "history": history,
            "can_edit": await view.allows(
                Permission.EDIT, request=request, record=record
            ),
        },
    )


async def activity(admin: "Admin", request: Request) -> Response:
    """The latest changes across the admin, newest first."""
    if admin.audit is None:
        raise HTTPException(status_code=404, detail="Auditing is not switched on.")

    # Filtering in the query, not afterwards, so the page still shows the
    # latest entries this user may read rather than a few of the latest 200.
    readable = await admin.history_views(request)
    allowed = [view.name for view in readable]
    if not allowed:
        raise PermissionDeniedError(Permission.HISTORY.value, "the activity")

    chosen = request.query_params.get("view") or None
    if chosen is not None and chosen not in allowed:
        chosen = None
    entries = await admin.audit.recent(view=chosen, views=allowed, limit=200)

    return await admin.render(
        "activity.html",
        request,
        {
            "items": describe(admin, entries),
            "history_views": readable,
            "chosen": chosen,
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
            raise HTTPException(status_code=404, detail="No such record.")
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
            return await form_again(
                admin, view, session, request, result, error, record, submitted
            )

    add_message(request, f"{view.label} saved.")
    return RedirectResponse(Urls(request).detail(view, key_text(key)), status_code=303)


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
            raise HTTPException(status_code=404, detail="No such record.")
        await view.ensure(Permission.DELETE, request=request, record=record)
        try:
            await view.delete(session, record, request=request)
            await session.commit()
        except RefusedError as error:
            add_message(request, str(error), kind="error")
            return RedirectResponse(Urls(request).list(view), status_code=303)

    add_message(request, f"{view.label} deleted.")
    return RedirectResponse(Urls(request).list(view), status_code=303)


async def lookup(admin: "Admin", request: Request) -> Response:
    """The records a relation field offers, narrowed by what was typed."""
    view = find_view(admin, request)
    path = request.path_params["path"]
    item = view.field_for(path)
    if not isinstance(item, RelationField):
        raise HTTPException(status_code=404, detail=f"{path!r} is not a link.")

    target = SQLAlchemyRepository(item.target, admin.inspector)
    schema = admin.inspector.inspect(item.target)
    spec = QuerySpec(
        search=request.query_params.get("q", "").strip(),
        search_paths=tuple(
            name
            for name, found in schema.fields.items()
            if found.python_type is str and not found.primary_key
        ),
        limit=20,
        count=CountMode.NONE,
    )

    async with admin.database.session() as session:
        page = await target.list(session, spec)
        choices = [
            Choice(target.identity_of(record), title_for(admin, item, record))
            for record in page
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
        "heading": view.title_of(record) if editing else f"New {view.label.lower()}",
        "submit_label": "Save changes" if editing else f"Create {view.label.lower()}",
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
    context["form_error"] = str(error) if error is not None else ""
    if record is not None:
        context["can_delete"] = await view.allows(
            Permission.DELETE, request=request, record=record
        )
    return await admin.render("form.html", request, context, status_code=422)


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
        raise HTTPException(status_code=404, detail="No such record.")
    return record


def find_view(admin: "Admin", request: Request) -> ModelView:
    """The view the URL names, or a 404."""
    name = request.path_params.get("view", "")
    view = admin.views.find(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No page at {name!r}.")
    return view


def read_key(request: Request) -> Any:
    """The primary key out of the URL, as one value or a tuple."""
    raw = request.path_params["key"]
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
        raise HTTPException(status_code=403, detail="This form has expired.")
    return data


async def login_form(admin: "Admin", request: Request) -> Response:
    """The sign in page."""
    return await admin.render("login.html", request, {"error": ""})


async def login(admin: "Admin", request: Request) -> Response:
    """Check the details and let the user in."""
    if admin.auth is None:
        raise HTTPException(status_code=404, detail="Signing in is not set up.")

    submitted = await read_form(request)
    user = await admin.auth.sign_in(
        request,
        str(submitted.get("username", "")),
        str(submitted.get("password", "")),
    )
    if user is None:
        return await admin.render(
            "login.html",
            request,
            {"error": "That username and password do not match."},
            status_code=401,
        )
    return RedirectResponse(Urls(request).index(), status_code=303)


async def logout(admin: "Admin", request: Request) -> Response:
    """Sign the user out."""
    await read_form(request)
    if admin.auth is not None:
        await admin.auth.sign_out(request)
    return RedirectResponse(Urls(request).login(), status_code=303)


async def run_action(admin: "Admin", request: Request) -> Response:
    """Run a bulk action over the chosen rows, or over every match."""
    view = find_view(admin, request)
    found = view.action_named(request.path_params["name"])

    submitted = await read_form(request)
    keys = submitted.get("keys", [])
    chosen = keys if isinstance(keys, list) else [keys]
    read = read_list_request(request, view)
    spec = view.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
    )

    inputs = view.parse_action_inputs(found, submitted)
    if not inputs.ok:
        problems = "; ".join(
            f"{item.label}: {inputs.errors[item.name]}"
            for item in found.inputs
            if item.name in inputs.errors
        )
        add_message(request, f"{found.label} was not done. {problems}", kind="error")
        return back_to_list(request, view)

    async with admin.database.session() as session:
        selection = Selection(
            view=view,
            session=session,
            spec=spec,
            keys=tuple(chosen),
            everything=submitted.get("everything") == "1",
            request=request,
        )
        try:
            message = await view.run_action(
                found, selection, request=request, values=inputs.values
            )
            await session.commit()
        except RefusedError as error:
            await session.rollback()
            add_message(request, str(error), kind="error")
            return back_to_list(request, view)
        except IntegrityError:
            await session.rollback()
            add_message(
                request,
                f"{found.label} was not done, because other records still "
                "refer to some of these.",
                kind="error",
            )
            return back_to_list(request, view)

    add_message(request, message)
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

    filename = f"{view.name}.csv"
    return StreamingResponse(
        stream_csv(admin, view, spec, request),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def back_to_list(request: Request, view: ModelView) -> RedirectResponse:
    """Return to the list the action was started from."""
    query = request.url.query
    target = Urls(request).list(view)
    return RedirectResponse(f"{target}?{query}" if query else target, status_code=303)
