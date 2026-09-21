from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from adminsite.exceptions import AdminSiteError
from adminsite.security import Permission
from adminsite.text import humanize

if TYPE_CHECKING:
    from adminsite.fields import Field

MARKER = "__adminsite_action__"

# Names the action form already uses for itself.
RESERVED_INPUTS = frozenset({"keys", "everything", "_csrf"})

Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])


@dataclass(frozen=True, slots=True)
class Action:
    """A button that runs over the rows the user picked."""

    name: str
    label: str
    method: str
    confirm: str = ""
    permission: str = Permission.EDIT
    dangerous: bool = False
    inputs: tuple["Field", ...] = ()

    @property
    def needs_confirming(self) -> bool:
        """Whether the user is asked before it runs."""
        return bool(self.confirm)

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
) -> Callable[[Handler], Handler]:
    """Mark a method as a bulk action.

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
    """
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
            ),
        )
        return handler

    return mark


def action_of(candidate: object) -> Action | None:
    """The action a method carries, if it was marked as one."""
    found = getattr(candidate, MARKER, None)
    return found if isinstance(found, Action) else None
