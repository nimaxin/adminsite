"""A JSON API over the same views, permissions and scopes as the pages."""

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from adminsite.actions import Selection
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.exceptions import FieldValidationError, RefusedError
from adminsite.fields import FileField, RelationField
from adminsite.http.listing import read_list_request
from adminsite.http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.security import Permission
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

MAX_LIMIT = 500


class ApiError(Exception):
    """A request the API answers with an error and a status."""

    def __init__(
        self, status: int, message: str, errors: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or {}

    def response(self) -> JSONResponse:
        """The error as JSON."""
        body: dict[str, Any] = {"error": self.message}
        if self.errors:
            body["errors"] = self.errors
        return JSONResponse(body, status_code=self.status)


def find(admin: "Admin", request: Request) -> ModelView:
    """The view the URL names, or a 404."""
    view = admin.views.find(request.path_params.get("view", ""))
    if view is None:
        raise ApiError(404, _("No such view."))
    return view


def api_paths(view: ModelView, request: Any = None) -> tuple[str, ...]:
    """The fields a record carries in the API: the list's, then the form's."""
    paths: list[str] = []
    for path in (*view.get_list_display(request), *view.get_form_fields(request)):
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def to_json(
    view: ModelView, record: Any, paths: Sequence[str], urls: Urls
) -> dict[str, Any]:
    """One record as plain JSON values, keyed by path."""
    body: dict[str, Any] = {"key": view.identity_of(record)}
    for path in paths:
        body[path] = json_value(view, path, view.value_at(record, path), urls)
    return body


def json_value(view: ModelView, path: str, value: Any, urls: Urls) -> Any:
    """A value as JSON: links as keys, files as a name and an address."""
    item = view.field_for(path)
    if isinstance(item, RelationField):
        target = SQLAlchemyRepository(item.target, view.inspector)
        if value is None:
            return None
        if isinstance(value, list | tuple | set):
            return [target.identity_of(one) for one in value]
        return target.identity_of(value)
    if isinstance(item, FileField):
        if not value:
            return None
        return {"name": item.display(value), "url": urls.file(view, path, value)}
    return to_jsonable_python(value)


async def read_body(request: Request) -> dict[str, Any]:
    """The JSON object sent with the request."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise ApiError(400, _("Send a JSON object.")) from None
    if not isinstance(body, dict):
        raise ApiError(400, _("Send a JSON object."))
    return body


def as_text(value: Any) -> str | None:
    """A JSON value the way a form would have sent it."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else ""
    return str(value)


def read_values(
    view: ModelView, body: dict[str, Any], *, record: Any = None, request: Any = None
) -> dict[str, Any]:
    """Check the fields sent against the form's own fields.

    Only the fields sent are touched, so a PATCH with one field changes one
    field. A new record needs every required field.
    """
    writable = set(view.get_form_fields(request, record)) - set(
        view.get_readonly_fields(request, record)
    )
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for path, raw in body.items():
        if path not in writable:
            errors[path] = _("This field cannot be written.")
            continue
        item = view.field_for(path)
        if isinstance(item, FileField):
            errors[path] = _("Files are uploaded through the form, not the API.")
            continue
        try:
            if isinstance(item, RelationField) and item.collection:
                if not isinstance(raw, list):
                    raise FieldValidationError(path, _("Send a list of keys."))
                values[path] = item.parse_many([str(key) for key in raw])
            else:
                values[path] = item.parse(as_text(raw))
        except FieldValidationError as error:
            errors[path] = error.message
    if record is None:
        for path in writable - set(body):
            if view.field_for(path).required:
                errors[path] = _("This field is required.")
    if errors:
        raise ApiError(422, _("Some fields need another look."), errors)
    return values


async def index(admin: "Admin", request: Request) -> Response:
    """The views this user may open, with their fields and actions."""
    found = []
    for view in await admin.views_allowing(request):
        found.append(
            {
                "name": view.name,
                "label": view.label_plural,
                "fields": [
                    {
                        "path": path,
                        "label": view.label_for(path),
                        "widget": view.field_for(path).widget,
                        "required": view.field_for(path).required,
                    }
                    for path in api_paths(view, request)
                ],
                "actions": [
                    {"name": item.name, "label": item.label}
                    for item in view.get_actions(request)
                    if await view.allows(item.permission, request=request)
                ],
            }
        )
    return JSONResponse({"views": found})


async def collection(admin: "Admin", request: Request) -> Response:
    """List records, or add one."""
    view = find(admin, request)
    if request.method == "POST":
        return await create(admin, request, view)

    read = read_list_request(request, view)
    paths = api_paths(view, request)
    spec = view.build_spec(
        request=request,
        search=read.search,
        filters=read.values,
        sort=read.sort,
        page=read.page,
        after=read.after,
        before=read.before,
        paths=paths,
    )
    try:
        limit = int(request.query_params.get("limit", spec.limit or 25))
    except ValueError:
        raise ApiError(400, _("limit is a number.")) from None
    spec = spec.replace(limit=max(1, min(limit, MAX_LIMIT))).page(read.page)

    urls = Urls(request)
    async with admin.database.session() as session:
        page = await view.fetch_page(session, spec, request=request)
        items = [to_json(view, record, paths, urls) for record in page]
    return JSONResponse(
        {
            "items": items,
            "total": page.total,
            "estimated": page.estimated or page.at_least,
            "page": None if page.keyset else read.page,
            "has_next": page.has_next,
            "next": page.next_cursor or None,
            "previous": page.previous_cursor or None,
        }
    )


async def create(admin: "Admin", request: Request, view: ModelView) -> Response:
    """Add a record from a JSON object."""
    await view.ensure(Permission.CREATE, request=request)
    values = read_values(view, await read_body(request), request=request)
    urls = Urls(request)
    async with admin.database.session() as session:
        record = await save(view, session, values, None, request)
        key = view.identity_of(record)
        fresh = await view.fetch_record(
            session, key, paths=api_paths(view, request), request=request
        )
        return JSONResponse(
            to_json(view, fresh, api_paths(view, request), urls), status_code=201
        )


async def item(admin: "Admin", request: Request) -> Response:
    """Read, change or delete one record."""
    view = find(admin, request)
    paths = api_paths(view, request)
    load = tuple(dict.fromkeys((*view.get_load_paths(request), *paths)))
    urls = Urls(request)
    async with admin.database.session() as session:
        record = await view.fetch_record(
            session, read_key(request), paths=load, request=request
        )
        if record is None:
            raise ApiError(404, _("No such record."))

        if request.method == "DELETE":
            try:
                await view.delete(session, record, request=request)
            except RefusedError as error:
                raise ApiError(409, str(error)) from None
            except IntegrityError:
                raise ApiError(
                    409, _("Other records still refer to this one.")
                ) from None
            return Response(status_code=204)

        if request.method == "PATCH":
            await view.ensure(Permission.EDIT, request=request, record=record)
            values = read_values(
                view, await read_body(request), record=record, request=request
            )
            await save(view, session, values, record, request)
            record = await view.fetch_record(
                session, read_key(request), paths=load, request=request
            )

        return JSONResponse(to_json(view, record, paths, urls))


async def save(
    view: ModelView, session: Any, values: dict[str, Any], record: Any, request: Any
) -> Any:
    """Save through the view, turning refusals into API errors."""
    try:
        return await view.save(session, values, record=record, request=request)
    except RefusedError as error:
        raise ApiError(409, str(error)) from None


async def action(admin: "Admin", request: Request) -> Response:
    """Run a bulk action over some keys, or over everything that matches."""
    view = find(admin, request)
    try:
        found = view.action_named(request.path_params["name"])
    except Exception:
        raise ApiError(404, _("No such action.")) from None
    body = await read_body(request)
    keys = body.get("keys", [])
    if not isinstance(keys, list):
        raise ApiError(422, _("keys is a list."))

    raw_inputs = body.get("inputs", {})
    inputs = view.parse_action_inputs(
        found, {name: as_text(value) or "" for name, value in raw_inputs.items()}
    )
    if not inputs.ok:
        raise ApiError(422, _("Some values need another look."), inputs.errors)

    read = read_list_request(request, view)
    spec = view.build_spec(
        request=request, search=read.search, filters=read.values, sort=read.sort
    )
    async with admin.database.session() as session:
        selection = Selection(
            view=view,
            session=session,
            spec=spec,
            keys=tuple(str(key) for key in keys),
            everything=bool(body.get("everything")),
            request=request,
        )
        try:
            message = await view.run_action(
                found, selection, request=request, values=inputs.values
            )
            await session.commit()
        except RefusedError as error:
            await session.rollback()
            raise ApiError(409, str(error)) from None
        except IntegrityError:
            await session.rollback()
            raise ApiError(
                409, _("Other records still refer to some of these.")
            ) from None
    return JSONResponse({"message": message})


def read_key(request: Request) -> Any:
    """The primary key out of the URL, as one value or a tuple."""
    parts = request.path_params["key"].split(",")
    return tuple(parts) if len(parts) > 1 else parts[0]


def error_response(error: Exception) -> JSONResponse:
    """Any failure as JSON, never as a page."""
    if isinstance(error, ApiError):
        return error.response()
    if isinstance(error, HTTPException):
        return JSONResponse({"error": str(error.detail)}, status_code=error.status_code)
    return JSONResponse({"error": str(error)}, status_code=403)
