from enum import StrEnum


class Permission(StrEnum):
    """The things a user can be allowed to do with a view."""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    EXPORT = "export"
    IMPORT = "import"
    # Reading the audit log: a record's History tab, and the view's entries
    # on the Activity page.
    HISTORY = "history"


def permission_name(permission: "Permission | str") -> str:
    """Read a permission as plain text, so custom ones work too."""
    return permission.value if isinstance(permission, Permission) else permission
