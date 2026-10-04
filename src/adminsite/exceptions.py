from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adminsite.columns import ColumnReference

__all__ = [
    "AdminSiteError",
    "FieldValidationError",
    "IntegrityError",
    "InvalidPathError",
    "NotAModelError",
    "PermissionDeniedError",
    "RecordNotFoundError",
    "RefusedError",
    "SignInRefusedError",
    "UnknownFieldError",
]


class AdminSiteError(Exception):
    """Base class for every error adminsite raises."""


class NotAModelError(AdminSiteError):
    """Raised when a class is not a mapped SQLAlchemy model."""

    def __init__(self, model: object) -> None:
        name = getattr(model, "__name__", repr(model))
        super().__init__(f"{name} is not a mapped model.")


class UnknownFieldError(AdminSiteError):
    """Raised when a name does not match any field or relationship."""

    def __init__(self, model: type[object], name: str, path: str = "") -> None:
        where = f" while resolving {path!r}" if path and path != name else ""
        super().__init__(
            f"{model.__name__} has no field or relationship named {name!r}{where}."
        )
        self.model = model
        self.name = name
        self.path = path or name


class RefusedError(AdminSiteError):
    """Raise this from a hook to refuse a save or a delete.

    The message is shown to the user on the form or above the list, and
    the transaction is rolled back. Any other exception is a fault, and
    is reported as one.

    Name a field and the message appears next to that input instead of
    above the form:

    ```python
    raise RefusedError("Keep this above the check delay.", field=Check.validation_delay)
    ```

    The field is one of the view's own, not one of an inline's rows.

    Args:
        message: What to tell the user, such as "This customer's account is
            closed.".
        field: The field to put the message beside, as a setting names it.
            Left empty, the message goes above the form.
    """

    def __init__(self, message: str, *, field: "ColumnReference" = "") -> None:
        # columns imports this module, so it is imported here, when needed.
        from adminsite.columns import written_path

        super().__init__(message)
        # The field's path, such as "delay", or "" for none.
        self.field = field if isinstance(field, str) else written_path(field)


class PermissionDeniedError(AdminSiteError):
    """Raised when the current user may not do this.

    The admin answers it with a "Not allowed" page and a 403, or a 403 in
    JSON from the API.

    Args:
        action: The permission refused, such as "edit".
        subject: What it was refused on, such as "Orders".
    """

    def __init__(self, action: str, subject: str = "") -> None:
        from adminsite.i18n import gettext as _

        # The action is one of the permission names, such as "edit".
        doing = _(action)
        super().__init__(
            _("You cannot {action} {subject}.", action=doing, subject=subject)
            if subject
            else _("You cannot {action}.", action=doing)
        )
        self.action = action
        self.subject = subject


class RecordNotFoundError(AdminSiteError):
    """Raised when a key does not match any record.

    Args:
        model: The model searched.
        key: The key that matched nothing.
    """

    def __init__(self, model: type[object], key: object) -> None:
        super().__init__(f"No {model.__name__} has the key {key!r}.")
        self.model = model
        self.key = key


class FieldValidationError(AdminSiteError):
    """Raised when a submitted value cannot be stored in a field."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field_name = field_name
        self.message = message


class InvalidPathError(AdminSiteError):
    """Raised when a dotted path cannot be walked to the end."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Cannot resolve {path!r}: {reason}")
        self.path = path


class IntegrityError(AdminSiteError):
    """Raised when the database refuses a change.

    A value that must be unique is already taken, a required one is missing,
    or other records still refer to the one being deleted.
    `SessionAdapter.transaction()` and `commit()` raise this in place of
    SQLAlchemy's own error, which stays attached as the cause. Its message
    is worded for people and holds none of the values. A save or a delete
    through the view turns it into a `RefusedError`, which the form or the
    list shows.
    """


class SignInRefusedError(AdminSiteError):
    """Raise this from `AuthProvider.verify` to refuse a sign in, saying why.

    The reason goes to the audit log, never to the person signing in, who
    sees only what `sign_in_failed` returns. Pass the account when there is
    one, such as an inactive user, so the attempt is filed under them.
    """

    def __init__(self, reason: str, user: object = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.user = user
