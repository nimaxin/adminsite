"""A value changed straight from the list: the editor a cell opens, and its save."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from adminsite._http.forms import build_rows
from adminsite._http.listing import read_columns, rows_context
from adminsite._http.requests import find_view, read_form, read_key
from adminsite._http.urls import Urls
from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.messages import stored_message
from adminsite.permissions import Permission
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.database import SessionAdapter

__all__ = [
    "edit_value",
    "save_value",
]


async def edit_value(admin: "Admin", request: Request) -> Response:
    """The editor a list cell opens: its field's input, holding the record's value."""
    view = find_view(admin, request)
    path = request.path_params["path"]
    columns = read_columns(request, view)

    async with admin.database.session() as session:
        record = await _changeable(view, session, request, path, columns)
        return await _editor(admin, view, session, request, record, path)


async def save_value(admin: "Admin", request: Request) -> Response:
    """Save the value a list cell's editor sends, and draw the record's row again.

    A value that cannot be read, or that a hook or the database refuses,
    gets the editor back instead, with the reason under the input.
    """
    view = find_view(admin, request)
    path = request.path_params["path"]
    submitted = await read_form(request)
    columns = read_columns(request, view)

    async with admin.database.session() as session:
        record = await _changeable(view, session, request, path, columns)
        result = view._forms.parse(
            submitted, record=record, request=request, paths=[path]
        )
        if not result.ok:
            return await _editor(
                admin,
                view,
                session,
                request,
                record,
                path,
                values=result.values,
                typed=submitted,
                errors=result.errors,
            )
        try:
            await view._saver.save(
                session, result.values, record=record, request=request
            )
        except AdminSiteError as error:
            # The rollback expired the record, and the editor is about to
            # read it again, which an async session cannot do on the fly.
            record = await _fetch(view, session, request, columns)
            # The editor holds this one input, so a refusal of any field is
            # said under it.
            return await _editor(
                admin,
                view,
                session,
                request,
                record,
                path,
                values=result.values,
                typed=submitted,
                errors={path: str(error)},
            )
        # The row's computed columns are read as the list reads them, now
        # that they can follow from the value saved.
        await view._reader.load_values(session, [record], columns, request=request)

    context = await rows_context(view, request, [record], columns)
    context.update(
        view=view,
        row=record,
        columns=columns,
        message=stored_message(
            _("{thing} saved.", thing=view.get_record_title(record)), "info"
        ),
    )
    return await admin.render("_value_saved.html", request, context)


async def _changeable(
    view: ModelView[Any],
    session: "SessionAdapter",
    request: Request,
    path: str,
    columns: Sequence[str],
) -> Any:
    """The record the URL names, once this user may change this value of it.

    A value no cell offers this user answers as missing, so the address
    tells nobody which fields the list keeps from them.
    """
    record = await _fetch(view, session, request, columns)
    await view._ensure(Permission.EDIT, request=request, record=record)
    if path not in view._pages.editable_in_list(request, record, columns):
        raise HTTPException(
            status_code=404, detail=_("No field at {path}.", path=repr(path))
        )
    return record


async def _fetch(
    view: ModelView[Any],
    session: "SessionAdapter",
    request: Request,
    columns: Sequence[str],
) -> Any:
    """Load the record the URL names, with what its form and its row read."""
    paths = [*view._pages.load_paths(request), *view._pages.loadable(columns)]
    record = await view._reader.fetch_record(
        session, read_key(request), paths=list(dict.fromkeys(paths)), request=request
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_("No such record."))
    return record


async def _editor(
    admin: "Admin",
    view: ModelView[Any],
    session: "SessionAdapter",
    request: Request,
    record: Any,
    path: str,
    *,
    values: Mapping[str, Any] | None = None,
    typed: Mapping[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
) -> Response:
    """The editor of one value, saying what went wrong with it, if anything.

    `values` holds what was read from the editor, and `typed` the text it
    was read from, which an input that failed shows again.
    """
    rows = await build_rows(
        admin,
        view,
        session,
        record=record,
        submitted=values,
        typed=typed,
        errors=errors,
        request=request,
        paths=[path],
    )
    key = view._fields.identity_of(record)
    address = Urls(request).edit_value(view, key, path)
    # The list's query goes along, so the saved row has the columns on show.
    query = request.url.query
    context = {
        "row": rows[0],
        "view": view,
        "key": key,
        "save_url": f"{address}?{query}" if query else address,
        "record_title": view.get_record_title(record),
    }
    return await admin.render(
        "_value_editor.html", request, context, status_code=422 if errors else 200
    )
