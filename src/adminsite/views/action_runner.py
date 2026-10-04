"""A view's actions: the one asked for, run, answered and written down."""

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import uuid4

from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import Response

from adminsite.actions.action import Action, action_of
from adminsite.actions.parameters import (
    ASYNC_SESSION,
    ActionCall,
    async_session_refused,
    read_call,
)
from adminsite.actions.selection import Selection
from adminsite.audit.actor import actor_of
from adminsite.audit.entry import AuditEntry, AuditEvent, diff
from adminsite.audit.inputs import recorded_inputs
from adminsite.database import SessionAdapter
from adminsite.exceptions import AdminSiteError, RefusedError
from adminsite.fields import RelationField
from adminsite.i18n import gettext as _
from adminsite.inspector import SQLAlchemyInspector
from adminsite.messages import Message
from adminsite.security import Permission
from adminsite.views.auditing import AuditRecorder
from adminsite.views.links import Links
from adminsite.views.naming import name_all_linked
from adminsite.views.pages import PageFields
from adminsite.views.view_fields import ViewFields

if TYPE_CHECKING:
    from adminsite.views.model_view import ModelView

__all__ = ["DELETE_ACTION", "ActionRunner"]

# The model of the view this part belongs to.
M = TypeVar("M")

# The name of the built-in action that deletes the chosen rows.
DELETE_ACTION = "delete_selected"


class ActionRunner(Generic[M]):
    """A view's actions: the one asked for, run, answered and written down."""

    def __init__(
        self,
        view: "ModelView[M]",
        fields: ViewFields[M],
        pages: PageFields[M],
        links: Links,
        audit: AuditRecorder[M],
        inspector: SQLAlchemyInspector,
    ) -> None:
        self._view = view
        self._model: type[M] = view.model
        self._fields = fields
        self._pages = pages
        self._links = links
        self._audit = audit
        self._inspector = inspector
        # The view's marked methods, by action name.
        self.marked = self._collect()

    def delete_action(self) -> Action:
        """The built-in delete, worded for this view in the current language."""
        return Action(
            name=DELETE_ACTION,
            label=_("Delete"),
            method="_delete_selected",
            confirm=_(
                "Delete the chosen {things}? This cannot be undone.",
                things=self._view.label_plural.lower(),
            ),
            permission=Permission.DELETE,
            dangerous=True,
            writes_own_audit=True,
        )

    def on(self, target: str, request: Request) -> tuple[Action, ...]:
        """The actions of one kind: over a selection, a record or the view."""
        return tuple(
            self._asking_all(item)
            for item in self._view.get_actions(request)
            if item.on == target
        )

    def _asking_all(self, item: Action) -> Action:
        """The action, asking for every value its method needs.

        `get_actions` may replace an action's inputs, offer it again under
        another name, or build one by hand. An input given there takes the
        place of the one of its name, and the method's other parameters are
        still asked for.
        """
        known = self.marked.get(item.name)
        if item is known:
            return item
        if known is None or known.method != item.method:
            known = next(
                (one for one in self.marked.values() if one.method == item.method),
                None,
            )
        if known is not None:
            call, asked = known.call, known.inputs
        else:
            call = self._read_call(item)
            asked = (*item.inputs, *call.inputs)
        given = {one.name: one for one in item.inputs}
        inputs = (*[given.pop(one.name, one) for one in asked], *given.values())
        return replace(item, inputs=inputs, call=call)

    def find(self, name: str, request: Request) -> Action | None:
        """The action of this name offered to this request, or None.

        It looks through `get_actions`, so an action built for this request,
        with its own choices or labels, is the one that runs. A mistake in
        the action itself is raised, never taken for a missing one.
        """
        for item in self._view.get_actions(request):
            if item.name == name:
                return self._asking_all(item)
        # No falling back to the class's own list: an action `get_actions`
        # leaves out for this user is not offered, so it cannot be run by
        # asking for it by name either.
        return None

    def named(self, name: str, request: Request) -> Action:
        """Find an action by name, or say it is not there."""
        found = self.find(name, request)
        if found is None:
            raise AdminSiteError(
                f"{type(self._view).__name__} has no action called {name!r}."
            )
        return found

    async def run_on_record(
        self,
        found: Action,
        record: M,
        session: SessionAdapter,
        *,
        request: Request,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action on one record, and say what to tell the user."""
        entry = self._entry(
            found,
            request,
            values,
            self._fields.identity_of(record),
            self._view.get_record_title(record),
        )
        auditing = self._view._audit_log is not None
        paths = list(self._pages.form_fields(request, record)) if auditing else []
        try:
            await self._view._ensure(found.permission, request=request, record=record)
            given, entry = await self._given(found, session, values, entry, request)
            before = self._audit.snapshot(record, paths)
            answer = await self._call(found, record, request, session, given)
            if auditing:
                # Anything the record refuses surfaces here, while the entry
                # can still be written down as failed.
                await session.flush()
        except Exception as error:
            self._audit.record_failure(session, [entry], error)
            raise

        text = self._answer_text(found, answer)
        changes = (
            self._audit.masked(diff(before, self._audit.snapshot(record, paths)))
            if auditing
            else {}
        )
        self._audit.write(
            session,
            [replace(entry, changes=changes, message=self._kept_answer(found, text))],
        )
        return self._shown(answer, text)

    async def run_on_view(
        self,
        found: Action,
        session: SessionAdapter,
        *,
        request: Request,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action that acts on the view, not on any record."""
        entry = self._entry(found, request, values, "", None)
        try:
            await self._view._ensure(found.permission, request=request)
            given, entry = await self._given(found, session, values, entry, request)
            answer = await self._call(found, None, request, session, given)
            if self._view._audit_log is not None:
                await session.flush()
        except Exception as error:
            self._audit.record_failure(session, [entry], error)
            raise

        text = self._answer_text(found, answer)
        self._audit.write(
            session, [replace(entry, message=self._kept_answer(found, text))]
        )
        return self._shown(answer, text)

    async def run_on_selection(
        self,
        found: Action,
        selection: Selection[M],
        *,
        request: Request,
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run an action over a selection, and say what to tell the user.

        The values the action asked for are passed to its method by name.
        """
        entry = replace(
            self._entry(found, request, values, "", None), batch=str(uuid4())
        )
        auditing = self._view._audit_log is not None and not found.writes_own_audit
        keys: list[str] = []
        try:
            await self._view._ensure(found.permission, request=request)
            # Read the keys before the action runs: afterwards the rows may no
            # longer match the filter they were chosen by.
            if auditing:
                keys = await selection.covered_keys()
            given, entry = await self._given(
                found, selection.session, values, entry, request
            )
            answer = await self._call(
                found, selection, request, selection.session, given
            )
            if self._view._audit_log is not None:
                await selection.session.flush()
        except Exception as error:
            if auditing:
                failed = [replace(entry, record_key=key) for key in keys] or [entry]
                self._audit.record_failure(selection.session, failed, error)
            raise

        text = self._answer_text(found, answer)
        self._audit.write(
            selection.session,
            [
                replace(
                    entry,
                    record_key=key,
                    changes=self._audit.masked(selection.changes.get(key, {})),
                    message=self._kept_answer(found, text),
                )
                for key in keys
            ],
        )
        return self._shown(answer, text)

    async def _resolve_inputs(
        self,
        found: Action,
        session: SessionAdapter,
        values: Mapping[str, Any],
        *,
        request: Request,
    ) -> dict[str, Any]:
        """The values an action was given, with the records its links name.

        A key is read through the target's own view, as a form's link is, so
        the method only ever gets a record this user may see. A key for any
        other record is refused, and nothing says whether it exists.
        """
        given = dict(values)
        for item in found.inputs:
            value = given.get(item.name)
            if not isinstance(item, RelationField) or value in (None, "", []):
                continue
            keys = value if isinstance(value, list | tuple | set) else [value]
            label = found.input_label(item)
            records = [
                await self._input_record(item, session, key, request, label)
                for key in keys
            ]
            given[item.name] = records if item.collection else records[0]
        return given

    async def _input_record(
        self,
        item: RelationField,
        session: SessionAdapter,
        key: Any,
        request: Request,
        label: str,
    ) -> Any:
        """The record a link input names, or a refusal naming the input."""
        target = (
            self._view._views.for_relation(item)
            if self._view._views is not None
            else None
        )
        if target is not None:
            record = await self._links.linked_through(target, session, key, request)
        else:
            record = await self._links.linked_directly(item, session, key)
        if record is None:
            raise RefusedError(
                _("{field}: choose from the records offered.", field=label),
                field=item.name,
            )
        return record

    async def _given(
        self,
        found: Action,
        session: SessionAdapter,
        values: Mapping[str, Any] | None,
        entry: AuditEntry,
        request: Request,
    ) -> tuple[dict[str, Any], AuditEntry]:
        """What the method is given, and the entry that writes it down.

        The entry keeps a linked record by its name, as the history names
        it everywhere else, rather than by its key.
        """
        given = await self._resolve_inputs(
            found, session, values or {}, request=request
        )
        named = dict(given)
        for item in found.inputs:
            if isinstance(item, RelationField) and named.get(item.name) is not None:
                named[item.name] = name_all_linked(
                    item,
                    named[item.name],
                    views=self._view._views,
                    inspector=self._inspector,
                )
        return given, replace(entry, inputs=recorded_inputs(found.inputs, named))

    async def _call(
        self,
        found: Action,
        subject: Any,
        request: Request,
        session: SessionAdapter,
        values: Mapping[str, Any],
    ) -> Any:
        """Call an action's method with what its parameters ask for.

        `subject` is the selection or the record it runs on, and `values`
        what the dialog asked for, by input name.
        """
        positional, named = self._call_of(found).arguments(
            subject=subject, request=request, session=session, values=values
        )
        return await getattr(self._view, found.method)(*positional, **named)

    def _call_of(self, found: Action) -> ActionCall:
        """How an action's method is called, read from its parameters.

        Read when the view is built for each marked method, and here for an
        action built by hand, such as the built-in delete.
        """
        if found.call is not None:
            return found.call
        return self._read_call(found)

    def _read_call(self, found: Action) -> ActionCall:
        return read_call(
            getattr(self._view, found.method),
            where=f"{type(self._view).__name__}.{found.method}",
            on=found.on,
            model=self._model,
            asked=found.inputs,
        )

    def check_database(self, is_async: bool) -> None:
        """Refuse an action asking for an AsyncSession of a database that is not async.

        The admin calls it when the view is registered, so the mistake stops
        it starting rather than the action's first run.
        """
        if is_async:
            return
        for found in self.marked.values():
            asking = found.call.handed_as(ASYNC_SESSION) if found.call else None
            if asking is not None:
                where = f"{type(self._view).__name__}.{found.method}"
                raise AdminSiteError(f"{where}: {async_session_refused(asking.name)}")

    def _entry(
        self,
        found: Action,
        request: Request,
        values: Mapping[str, Any] | None,
        key: str,
        title: str | None,
    ) -> AuditEntry:
        """The entry an action run is written down as, before it has run."""
        return AuditEntry(
            view=self._view.name,
            record_key=key,
            record_title=title,
            event=AuditEvent.ACTION,
            action=found.label,
            inputs=recorded_inputs(found.inputs, values or {}),
            **actor_of(request),
        )

    def _answer_text(self, found: Action, answer: Any) -> str | None:
        """What to tell the user; nothing when the action sent a response."""
        if isinstance(answer, Response):
            return None
        return str(answer) if answer else _("{action} done.", action=found.label)

    def _shown(self, answer: Any, text: str | None) -> Any:
        """What the page is given: the answer itself, when it says more than text.

        A response is sent as it is, and a `Message` or `Html` keeps what
        plain text would lose: a link, a value to copy, its markup.
        """
        if isinstance(answer, Response | Message | Markup):
            return answer
        return text

    def _kept_answer(self, found: Action, text: str | None) -> str | None:
        """What the audit log keeps of the answer: nothing, if it is secret."""
        return text if found.audit_answer else None

    def _collect(self) -> dict[str, Action]:
        """The marked methods, each with what its parameters are handed and ask for.

        Read now, so a parameter no dialog can ask for stops the admin
        starting rather than the action's first run.
        """
        found: dict[str, Action] = {}
        for name in dir(type(self._view)):
            marked = action_of(getattr(type(self._view), name, None))
            if marked is not None:
                call = self._read_call(marked)
                found[marked.name] = replace(
                    marked, inputs=(*marked.inputs, *call.inputs), call=call
                )
        return found
