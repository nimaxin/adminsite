from enum import StrEnum


class Action(StrEnum):
    """The things a user can be allowed to do with a view."""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    EXPORT = "export"


def action_name(action: "Action | str") -> str:
    """Read an action as plain text, so custom actions work too."""
    return action.value if isinstance(action, Action) else action
