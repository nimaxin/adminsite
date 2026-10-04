"""The forms that add and change a record, and deleting one."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.document_forms import document_form
from adminsite._http.form_rows import FormRow
from adminsite._http.forms import build_rows, fill_document
from adminsite._http.inline_tables import build_inline_tables
from adminsite._http.requests import (
    field_or_404,
    find_view,
    load_or_404,
    read_form,
    read_key,
)
from adminsite._http.urls import Urls
from adminsite.exceptions import (
    AdminSiteError,
    FieldValidationError,
    RefusedError,
)
from adminsite.fields import JSONField
from adminsite.fields.documents import DocumentError
from adminsite.i18n import gettext as _
from adminsite.messages import add_message
from adminsite.permissions import Permission
from adminsite.views import ModelView
from adminsite.views.writing import FormResult

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.database import SessionAdapter


if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "after_save",
    "create_form",
    "create_record",
    "delete_record",
    "document",
    "edit_form",
    "edit_record",
    "form_again",
    "form_context",
]


async def create_form(admin: "Admin", request: Request) -> Response:
    """The empty form for adding a record."""
    view = find_view(admin, request)
    await view._ensure(Permission.CREATE, request=request)

    async with admin.database.session() as session:
        rows = await build_rows(admin, view, session, request=request)
        inlines = await build_inline_tables(admin, view, session, request=request)

    context = form_context(view, rows, request)
    context["inlines"] = inlines
    return await admin.render("form.html", request, context)


async def create_record(admin: "Admin", request: Request) -> Response:
    """Save a new record, or show the form again with what went wrong."""
    view = find_view(admin, request)
    await view._ensure(Permission.CREATE, request=request)

    submitted = await read_form(request)
    result = view._forms.parse(submitted, request=request)
    async with admin.database.session() as session:
        if not result.ok:
            return await form_again(
                admin, view, session, request, result, submitted=submitted
            )
        try:
            record = await view._saver.save(
                session,
                result.values,
                request=request,
                inline_rows=result.inline_rows,
            )
        except AdminSiteError as error:
            return await form_again(
                admin, view, session, request, result, error, submitted=submitted
            )

        key = view._fields.identity_of(record)

    add_message(request, _("{thing} created.", thing=view.label))
    return RedirectResponse(await after_save(admin, view, request, key), 303)


async def edit_form(admin: "Admin", request: Request) -> Response:
    """The form for changing a record."""
    view = find_view(admin, request)
    record = await load_or_404(admin, view, request)
    await view._ensure(Permission.EDIT, request=request, record=record)

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
        record = await view._reader.fetch_record(
            session, key, paths=view._pages.load_paths(request), request=request
        )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        await view._ensure(Permission.EDIT, request=request, record=record)

        result = view._forms.parse(submitted, record=record, request=request)
        if not result.ok:
            return await form_again(
                admin, view, session, request, result, None, record, submitted
            )
        try:
            await view._saver.save(
                session,
                result.values,
                record=record,
                request=request,
                inline_rows=result.inline_rows,
            )
        except AdminSiteError as error:
            # The rollback expired the record, and the form is about to
            # read it again, which an async session cannot do on the fly.
            record = await view._reader.fetch_record(
                session, key, paths=view._pages.load_paths(request), request=request
            )
            return await form_again(
                admin, view, session, request, result, error, record, submitted
            )
        # A hook may have changed the key, so the page follows the record.
        saved = view._fields.identity_of(record)

    add_message(request, _("{thing} saved.", thing=view.label))
    return RedirectResponse(await after_save(admin, view, request, saved), 303)


async def delete_record(admin: "Admin", request: Request) -> Response:
    """Delete one record and go back to the list."""
    view = find_view(admin, request)
    await read_form(request)

    async with admin.database.session() as session:
        record = await view._reader.fetch_record(
            session,
            read_key(request),
            paths=view._pages.load_paths(request),
            request=request,
        )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        await view._ensure(Permission.DELETE, request=request, record=record)
        try:
            await view._saver.delete(session, record, request=request)
        except RefusedError as error:
            add_message(request, str(error), kind="error")
            return RedirectResponse(Urls(request).list(view), status_code=303)

    add_message(request, _("{thing} deleted.", thing=view.label))
    return RedirectResponse(Urls(request).list(view), status_code=303)


def form_context(
    view: ModelView[Any],
    rows: list[FormRow],
    request: Request,
    record: Any = None,
) -> dict[str, Any]:
    """What both the create form and the edit form need."""
    urls = Urls(request)
    editing = record is not None
    key = view._fields.identity_of(record) if editing else ""
    return {
        "view": view,
        "rows": rows,
        # Where the view places each field: its panels, rows and tabs.
        "layout": view._pages.arranged([row.path for row in rows], form=True),
        "record": record,
        "key": key,
        "heading": view.get_record_title(record)
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
    view: ModelView[Any],
    session: "SessionAdapter",
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
    # A refusal about one field belongs next to that field, not above the
    # form, so it reads like any other problem with what was typed. One
    # about a child row's input, named like items-2-product, goes in its cell.
    refused = error.field if isinstance(error, RefusedError) else ""
    context["inlines"] = await build_inline_tables(
        admin,
        view,
        session,
        record=record,
        submitted=submitted or {},
        errors={**result.errors, refused: str(error)} if refused else result.errors,
        request=request,
    )
    in_cell = any(
        cell.path == refused
        for table in context["inlines"]
        for item in table.rows
        for cell in item.cells
    )
    beside_field = bool(refused) and (in_cell or refused in {row.path for row in rows})
    for row in rows:
        if refused and row.path == refused:
            row.error = str(error)
    context["form_error"] = "" if beside_field or error is None else str(error)
    if record is not None:
        context["can_delete"] = await view.allows(
            Permission.DELETE, request=request, record=record
        )
    return await admin.render("form.html", request, context, status_code=422)


async def after_save(
    admin: "Admin", view: ModelView[Any], request: Request, key: str
) -> str:
    """Where a save lands: the record's page, or the list without one."""
    urls = Urls(request)
    if await view.allows(Permission.VIEW_DETAIL, request=request, record=None):
        return urls.detail(view, key)
    return urls.list(view)


async def document(admin: "Admin", request: Request) -> Response:
    """The document a JSON column's form stands for, for the form's JSON view.

    The form is read as Save reads it, so this shows what Save would write,
    or what needs another look first. With `?show=form` it is the field
    drawn again instead, for a new record whose schema follows what the
    rest of the form holds. It needs what saving the form needs.
    """
    view = find_view(admin, request)
    path = request.path_params["path"]
    item = field_or_404(view, path)
    if not isinstance(item, JSONField) or item.schema is None:
        raise HTTPException(
            status_code=404, detail=_("{path} has no schema.", path=repr(path))
        )
    submitted = await read_form(request)
    record = None
    if "key" in request.path_params:
        record = await load_or_404(admin, view, request)
        await view._ensure(Permission.EDIT, request=request, record=record)
    else:
        await view._ensure(Permission.CREATE, request=request)
    # Only a field this user edits on this form, so no other is read back.
    editable = set(view._pages.form_fields(request, record)) - set(
        view._pages.readonly_paths(request, record)
    )
    if path not in editable:
        raise HTTPException(
            status_code=404, detail=_("No field at {path}.", path=repr(path))
        )
    if record is None and item.schema_from_record:
        record = view._forms.draft_record(submitted, request)

    if request.query_params.get("show") == "form":
        row = FormRow(path=path, field=item)
        raw = submitted.get(path)
        row.value = raw if isinstance(raw, str) else ""
        fill_document(row, item, record, None, submitted, {})
        if "key" not in request.path_params:
            row.redraw_url = f"{Urls(request).document(view, path)}?show=form"
        drawn = {"row": row, "view": view, "key": ""}
        return await admin.render("_field.html", request, drawn)

    document = item.document_for(record)
    context: dict[str, Any] = {"problems": []}
    try:
        value = item.read_form(submitted, path, record=record)
    except DocumentError as error:
        if document is None:
            raise
        entry = document_form(document, submitted, error.errors, path)
        context["problems"] = entry.problems([])
    except FieldValidationError as error:
        context["problems"] = [("", item.label, error.message)]
    else:
        context["outline"] = item.outline(value)
        context["pretty"] = item.laid_out(value)
    return await admin.render("_document_json.html", request, context)
