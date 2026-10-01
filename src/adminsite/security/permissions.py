from enum import EnumType, StrEnum
from typing import TYPE_CHECKING, Any

__all__ = [
    "Permission",
    "RequestAction",
    "permission_name",
]


class _Renamed(EnumType):
    """Names the new member when a permission is asked for by its old name."""

    if not TYPE_CHECKING:
        # Hidden from type checkers, so they go on reporting the old name as
        # missing. Called only for a name the enum does not have.
        def __getattr__(cls, name: str) -> Any:
            if name == "DETAIL":
                raise AttributeError(
                    "Permission calls it VIEW_DETAIL, not DETAIL. "
                    "Write Permission.VIEW_DETAIL."
                )
            return super().__getattribute__(name)


class Permission(StrEnum, metaclass=_Renamed):
    """The things a user can be allowed to do with a view."""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    EXPORT = "export"
    # Opening one record's page. A view whose list says everything can
    # switch it off.
    VIEW_DETAIL = "detail"
    IMPORT = "import"
    # Reading the audit log: a record's History tab, and the view's entries
    # on the Activity page.
    HISTORY = "history"


class RequestAction(StrEnum):
    """The page a field is asked for, as `can_access_field` is told it."""

    LIST = "list"
    DETAIL = "detail"
    CREATE = "create"
    EDIT = "edit"
    EXPORT = "export"


def permission_name(permission: "Permission | str") -> str:
    """Read a permission as plain text, so custom ones work too."""
    return permission.value if isinstance(permission, Permission) else permission
