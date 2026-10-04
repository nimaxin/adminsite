from enum import StrEnum

__all__ = [
    "Permission",
    "RequestAction",
    "permission_name",
]


class Permission(StrEnum):
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
    """The list."""
    DETAIL = "detail"
    """The record page."""
    CREATE = "create"
    """The form for a new record."""
    EDIT = "edit"
    """The form for a record that exists."""
    EXPORT = "export"
    """The CSV export."""


def permission_name(permission: "Permission | str") -> str:
    """Read a permission as plain text, so custom ones work too."""
    return permission.value if isinstance(permission, Permission) else permission
