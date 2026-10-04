from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar

from adminsite._text import humanize
from adminsite.exceptions import AdminSiteError
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission

if TYPE_CHECKING:
    from adminsite.actions._parameters import ActionCall
    from adminsite.fields import BaseField

__all__ = [
    "MARKER",
    "ON_RECORD",
    "ON_SELECTION",
    "ON_VIEW",
    "RESERVED_INPUTS",
    "TARGETS",
    "Action",
    "ActionTarget",
    "Handler",
    "Input",
    "action",
    "action_of",
]

MARKER = "__adminsite_action__"

# What an action acts on.
ON_SELECTION: Final = "selection"
ON_RECORD: Final = "record"
ON_VIEW: Final = "view"
ActionTarget: TypeAlias = Literal["selection", "record", "view"]
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
    inputs: tuple["BaseField", ...] = ()
    on: ActionTarget = ON_SELECTION
    # Whether the audit log keeps what the action answered. Switch it off
    # for an answer that holds a secret shown once, such as a new API key.
    audit_answer: bool = True
    # Set on the built-in delete, which writes an entry for each record it
    # deletes, so the log does not also say it "ran Delete".
    writes_own_audit: bool = False
    # How the method is called, read from its parameters when the view is
    # built. None until then, and for an action built by hand.
    call: "ActionCall | None" = None

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

    @property
    def headings(self) -> dict[str, str]:
        """The heading each input of a group is drawn under, by input name."""
        if self.call is None:
            return {}
        return {
            group.input_name(part): group.label
            for group in self.call.groups
            for part in group.fields
        }

    def input_label(self, item: "BaseField") -> str:
        """An input as a message names it: a group's field with its heading."""
        heading = self.headings.get(item.name)
        if heading is None:
            return item.label
        return _("{group}, {field}", group=heading, field=item.label)


def action(
    label: str = "",
    *,
    name: str = "",
    confirm: str = "",
    permission: str = Permission.EDIT,
    dangerous: bool = False,
    inputs: Sequence["BaseField"] = (),
    on: ActionTarget = ON_SELECTION,
    audit_answer: bool = True,
) -> Callable[[Handler], Handler]:
    """Mark a method as an action.

    ```python
    from typing import Literal


    class OrderView(ModelView[Order]):
        @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
        async def ship(
            self, selection: Selection[Order], *, carrier: Literal["DHL", "UPS"]
        ) -> str:
            orders = await selection.records()
            for order in orders:
                order.status = OrderStatus.SHIPPED
            return f"{len(orders)} orders sent with {carrier}."
    ```

    Each parameter the method does not get handed is asked for in a
    dialog before the action runs, checked like a form field, and passed to
    the method by name. Its type picks the input, and
    `Annotated[str, Input(label="Reason")]` words it.

    `on` says what it acts on:

    - `"selection"`, the default: the rows the user ticked, handed to a
      parameter typed `Selection[Order]`.
    - `"record"`: one record, from its row in the list or from its page,
      handed to a parameter typed with the view's model.
    - `"view"`: nothing in particular. For work about the whole table, such
      as fetching from another system.

    A parameter typed `Request`, `AsyncSession` or `SessionAdapter` is
    handed the request or the session the action runs in.

    A method returns the message to show, a `Message` for more than a line
    that fades, or a response to send instead, such as a file to download.
    Raise `RefusedError` to stop it with a message; everything it did is
    rolled back.

    Args:
        label: The button's text. Left empty, the method's name in words:
            `mark_paid` reads "Mark paid".
        name: The action's name in its URL and the audit log. Left empty,
            the method's name.
        confirm: A question asked in a dialog before it runs.
        permission: What the user needs to run it, as `allows` decides:
            `Permission.EDIT` unless you say, or a name of your own.
        dangerous: Whether the button is drawn in red.
        inputs: Fields to ask for as they are, each passed to the parameter
            of its name. The parameters' types usually say enough.
        on: What it acts on: "selection", "record" or "view", as above.
        audit_answer: Whether the audit log keeps the answer. False for one
            that holds something shown only once, such as a new API key;
            the entry still says who ran it, on what and when.

    Returns:
        A decorator that marks the method and hands it back as it was.

    Raises:
        AdminSiteError: When `on` is none of the three, or an input takes a
            name the action form uses itself.
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


@dataclass(frozen=True, kw_only=True, slots=True)
class Input:
    """How an action asks for one value, written in the parameter's annotation.

    ```python
    from typing import Annotated

    from adminsite.actions import Input

    reason: Annotated[str, Input(label="Reason", multiline=True)]
    ```

    An option the type has no use for, such as `multiline` on an `int`,
    stops the admin when it starts.

    Args:
        label: The text above the input. Left empty, the parameter's name
            in words: `tracking_number` reads "Tracking number".
        help_text: A line under the input.
        multiline: A box of several lines, for a `str`.
        accept: For an `UploadFile`, the types offered, as a browser's
            `accept` takes them, such as ".csv".
        max_size: For an `UploadFile`, the largest file taken, in bytes.
        secret: Whether the audit log keeps `***` instead of the value.
            Left as None, a name such as `password` or `api_key` decides.
    """

    label: str = ""
    help_text: str = ""
    multiline: bool = False
    accept: str = ""
    max_size: int | None = None
    secret: bool | None = None
