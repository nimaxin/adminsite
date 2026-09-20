from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from adminsite.security import Permission
from adminsite.text import humanize

MARKER = "__adminsite_action__"

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

    @property
    def needs_confirming(self) -> bool:
        """Whether the user is asked before it runs."""
        return bool(self.confirm)


def action(
    label: str = "",
    *,
    name: str = "",
    confirm: str = "",
    permission: str = Permission.EDIT,
    dangerous: bool = False,
) -> Callable[[Handler], Handler]:
    """Mark a method as a bulk action.

    ```python
    @action("Mark as shipped", confirm="Mark these orders as shipped?")
    async def ship(self, selection: Selection) -> str:
        await selection.update(status="shipped")
        return f"{await selection.count()} orders marked as shipped."
    ```
    """

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
            ),
        )
        return handler

    return mark


def action_of(candidate: object) -> Action | None:
    """The action a method carries, if it was marked as one."""
    found = getattr(candidate, MARKER, None)
    return found if isinstance(found, Action) else None
