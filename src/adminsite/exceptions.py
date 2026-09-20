class AdminSiteError(Exception):
    """Base class for every error adminsite raises."""


class NotAModelError(AdminSiteError):
    """Raised when a class is not a mapped model of the backend in use."""

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


class RecordNotFoundError(AdminSiteError):
    """Raised when a key does not match any record."""

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
