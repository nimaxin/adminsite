"""Running an action over rows, one record or the whole view."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from adminsite._http.listing import read_list_request
from adminsite._http.requests import find_view, key_of, read_form
from adminsite._http.urls import Urls
from adminsite.actions import Selection
from adminsite.exceptions import (
    IntegrityError,
    RefusedError,
)
from adminsite.i18n import gettext as _
from adminsite.messages import add_message
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.database import SessionAdapter

__all__ = [
    "back_from_action",
    "back_to_list",
    "perform",
    "run_action",
]


async def run_action(admin: "Admin", request: Request) -> Response:
    """Run an action: over the chosen rows, over one record, or over the view."""
    view = find_view(admin, request)
    found = view._actions.find(request.path_params["name"], request)
    if found is None:
        raise HTTPException(status_code=404, detail=_("No such action."))

    submitted = await read_form(request)
    inputs = view._forms.parse_action_inputs(found, submitted)
    if not inputs.ok:
        problems = "; ".join(
            f"{found.input_label(item)}: {inputs.errors[item.name]}"
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
            # Anything that fails rolls the action back before the error page,
            # so the audit log can write the attempt down as failed.
            async with session.transaction():
                answer = await perform(
                    admin, request, view, found, session, submitted, inputs.values
                )
        except RefusedError as error:
            add_message(request, str(error), kind="error")
            return back_from_action(admin, request, view, found, submitted)
        except IntegrityError as error:
            add_message(
                request,
                _(
                    "{action} was not done. {reason}",
                    action=found.label,
                    reason=str(error),
                ),
                kind="error",
            )
            return back_from_action(admin, request, view, found, submitted)
        # An action that answers with a file or JSON sends it as it is.
        if isinstance(answer, Response):
            return answer

    add_message(request, answer)
    return back_from_action(admin, request, view, found, submitted)


async def perform(
    admin: "Admin",
    request: Request,
    view: ModelView[Any],
    found: Any,
    session: "SessionAdapter",
    submitted: Mapping[str, Any],
    values: Mapping[str, Any],
) -> Any:
    """Run one action, whatever it acts on."""
    if found.on_view:
        return await view._actions.run_on_view(
            found, session, request=request, values=values
        )

    keys = submitted.get("keys", [])
    chosen = [str(key) for key in (keys if isinstance(keys, list) else [keys])]

    if found.on_record:
        record = None
        if chosen:
            record = await view._reader.fetch_record(
                session,
                key_of(chosen[0]),
                paths=view._pages.load_paths(request),
                request=request,
            )
        if record is None:
            raise HTTPException(status_code=404, detail=_("No such record."))
        return await view._actions.run_on_record(
            found, record, session, request=request, values=values
        )

    read = read_list_request(request, view)
    spec = view._reader.build_spec(
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
    return await view._actions.run_on_selection(
        found, selection, request=request, values=values
    )


def back_from_action(
    admin: "Admin",
    request: Request,
    view: ModelView[Any],
    found: Any,
    submitted: Mapping[str, Any],
) -> RedirectResponse:
    """Where an action lands: the record it ran on, or the list it came from."""
    keys = submitted.get("keys", [])
    chosen = keys if isinstance(keys, list) else [keys]
    if found.on_record and chosen and view.can_view_detail:
        return RedirectResponse(Urls(request).detail(view, str(chosen[0])), 303)
    return back_to_list(request, view)


def back_to_list(request: Request, view: ModelView[Any]) -> RedirectResponse:
    """Return to the list the action was started from."""
    query = request.url.query
    target = Urls(request).list(view)
    return RedirectResponse(f"{target}?{query}" if query else target, status_code=303)
