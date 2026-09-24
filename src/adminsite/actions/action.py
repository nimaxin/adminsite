from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from adminsite.exceptions import AdminSiteError
from adminsite.security import Permission
from adminsite.text import humanize

if TYPE_CHECKING:
    from adminsite.fields import Field

MARKER = "__adminsite_action__"

# What an action acts on.
ON_SELECTION = "selection"
ON_RECORD = "record"
ON_VIEW = "view"
TARGETS = frozenset({ON_SELECTION, ON_RECORD, ON_VIEW})

# Names the action form already uses for itself.
RESERVED_INPUTS = frozenset({"keys", "everything", "_csrf"})

Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])


@dataclass(frozen=True, slots=True)
class Action:
    """A button that runs over records, over one record, or over the view."""

    name: str
    label: str
    method: str
    confirm: str = ""
    permission: str = Permission.EDIT
    dangerous: bool = False
    inputs: tuple["Field", ...] = ()
    on: str = ON_SELECTION
    # Whether the audit log keeps what the action answered. Switch it off
    # for an answer that holds a secret shown once, such as a new API key.
    audit_answer: bool = True
    # Set on the built-in delete, which writes an entry for each record it
    # deletes, so the log does not also say it "ran Delete".
    writes_own_audit: bool = False

    @property
    def needs_confirming(self) -> bool:
        """Whether the user is asked before it runs."""
        return bool(self.confirm)

    @property
    def on_selection(self) -> bool:
        """Whether it runs over the rows the user ticked."""
        return self.on == ON_SELECTION

    @property
    def on_record(self) -> bool:
        """Whether it runs on one record, from its row or its page."""
        return self.on == ON_RECORD

    @property
    def on_view(self) -> bool:
        """Whether it runs on the view, with nothing ticked."""
        return self.on == ON_VIEW

    @property
    def needs_dialog(self) -> bool:
        """Whether a dialog opens first, to confirm or to ask for values."""
        return bool(self.confirm or self.inputs)


def action(
    label: str = "",
    *,
    name: str = "",
    confirm: str = "",
    permission: str = Permission.EDIT,
    dangerous: bool = False,
    inputs: Sequence["Field"] = (),
    on: str = ON_SELECTION,
    audit_answer: bool = True,
) -> Callable[[Handler], Handler]:
    """Mark a method as an action.

    ```python
    @action(
        "Mark as shipped",
        confirm="Mark the chosen orders as shipped?",
        inputs=[ChoiceField("carrier", choices=CARRIERS, required=True)],
    )
    async def ship(self, selection: Selection, carrier: str) -> str:
        changed = await selection.update(status="shipped", carrier=carrier)
        return f"{changed} orders sent with {carrier}."
    ```

    Each input is asked for in a dialog before the action runs, checked
    like a form field, and passed to the method by name.

    `on` says what it acts on, and what the method is given:

    - `"selection"`, the default: the rows the user ticked, as a
      `Selection`.
    - `"record"`: one record, from its row in the list or from its page,
      as `(record, session)`.
    - `"view"`: nothing in particular, as `(session)`. For work about the
      whole table, such as fetching from another system.

    A method returns the message to show, or a response to send instead,
    such as a file to download.

    The audit log keeps that message with the entry for the run. Give
    `audit_answer=False` when it holds something shown only once, such as
    a new API key: the entry then says who ran it, on what and when, and
    keeps nothing of the answer.
    """
    if on not in TARGETS:
        raise AdminSiteError(
            f"An action runs on 'selection', 'record' or 'view', not {on!r}."
        )
    for item in inputs:
        if item.name in RESERVED_INPUTS:
            raise AdminSiteError(
                f"An action input cannot be called {item.name!r}, "
                "the action form already uses that name."
            )

    def mark(handler: Handler) -> Handler:
        chosen = name or handler.__name__
        setattr(
            handler,
            MARKER,
            Action(
                name=chosen,
                label=label or humanize(chosen),
                method=handler.__name__,
                confirm=confirm,
                permission=permission,
                dangerous=dangerous,
                inputs=tuple(inputs),
                on=on,
                audit_answer=audit_answer,
            ),
        )
        return handler

    return mark


def action_of(candidate: object) -> Action | None:
    """The action a method carries, if it was marked as one."""
    found = getattr(candidate, MARKER, None)
    return found if isinstance(found, Action) else None
