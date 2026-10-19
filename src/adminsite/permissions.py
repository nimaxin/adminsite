from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "GRANTS_KEY",
    "Grants",
    "Permission",
    "RequestAction",
    "grants_of",
    "permission_name",
]

# Where the admin keeps the signed in user's grants for the request, beside
# the user itself.
GRANTS_KEY = "adminsite_grants"


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


@dataclass(frozen=True, slots=True)
class Grants:
    """What one person may do, view by view, as their roles add up.

    An auth provider answers with these from `AuthProvider.grants_for`, and
    each view and page then follows them, unless its own `allows` says
    otherwise:

    ```python
    from adminsite import Grants

    Grants({"orders": {"view", "edit", "refund"}, "-/sales_report": {"view"}})
    ```

    A view is named as in its URL, such as "orders", and a page of your own
    by "-/" and its name. Any permission on a view lets the person see it,
    since there is nothing to do there otherwise.

    Args:
        permissions: The permissions granted on each view and page, by name:
            `Permission` values, such as "edit", or the permission an action
            asks for.
        everything: True to allow everything, as for a superuser.
    """

    permissions: Mapping[str, Collection[str]] = field(default_factory=dict)
    """The permissions granted on each view and page, by name."""
    everything: bool = False
    """Whether everything is allowed, as for a superuser."""

    def allows(self, name: str, permission: Permission | str) -> bool:
        """Whether the view or page with this name grants the permission.

        Args:
            name: The view's name, such as "orders", or a page's, such as
                "-/sales_report".
            permission: What the person wants to do, such as
                `Permission.EDIT`.

        Returns:
            True when it is granted. Seeing the view, and opening a record's
            page, come with any permission on it.
        """
        if self.everything:
            return True
        granted = self.permissions.get(name, ())
        wanted = permission_name(permission)
        if wanted in (Permission.VIEW, Permission.VIEW_DETAIL):
            return bool(granted)
        return wanted in granted


def grants_of(request: Any) -> Grants | None:
    """The grants the admin keeps with this request's user, if they have any."""
    scope = getattr(request, "scope", None)
    found = scope.get(GRANTS_KEY) if isinstance(scope, dict) else None
    return found if isinstance(found, Grants) else None
