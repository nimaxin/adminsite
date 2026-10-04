from enum import StrEnum

__all__ = [
    "Permission",
    "RequestAction",
    "permission_name",
]


class Permission(StrEnum):
    """The things a user can be allowed to do with a view."""

    VIEW = "view"
    """Opening the view's list, and seeing it in the sidebar."""
    CREATE = "create"
    """Adding a record."""
    EDIT = "edit"
    """Changing a record. The permission an action asks for, unless it says."""
    DELETE = "delete"
    """Deleting a record, alone or with others."""
    EXPORT = "export"
    """Downloading the list as CSV."""
    VIEW_DETAIL = "detail"
    """Opening one record's page.

    A view whose list says everything can switch it off.
    """
    IMPORT = "import"
    """Importing records from a file."""
    HISTORY = "history"
    """Reading the audit log.

    A record's History tab, and the view's entries on the Activity page.
    """


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
