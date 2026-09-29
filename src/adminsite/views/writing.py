from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, overload

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import QueryableAttribute
from starlette.requests import Request

from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.columns import describe
from adminsite.exceptions import AdminSiteError
from adminsite.views.inline import InlineRow

__all__ = [
    "DeleteContext",
    "FormData",
    "FormResult",
    "SaveContext",
    "SaveValue",
    "SaveValues",
    "stored_values",
]

FormData = Mapping[str, str | Sequence[str]]

M = TypeVar("M")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FormResult:
    """What a submitted form turned into: values, or messages to show."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    # Child rows for each inline, keyed by the inline's name.
    inline_rows: dict[str, list[InlineRow]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the form can be saved."""
        return not self.errors


class SaveValues:
    """The values a save stores, each named by its column: `values[Order.note]`.

    A string names a column too, or a value the form asks for that is no
    column, such as a password to hash: `values["password"]`. `in` tells
    whether the save stores a value for a column at all.
    """

    def __init__(
        self,
        record: object,
        stored: Mapping[str, Any],
        *,
        form_only: Callable[[str], bool] = lambda path: False,
    ) -> None:
        self._record = record
        self._stored = dict(stored)
        self._form_only = form_only

    @overload
    def __getitem__(self, column: QueryableAttribute[T]) -> "SaveValue[T]": ...

    @overload
    def __getitem__(self, column: str) -> "SaveValue[Any]": ...

    def __getitem__(self, column: QueryableAttribute[Any] | str) -> "SaveValue[Any]":
        return SaveValue(self, self._path(column))

    def __contains__(self, column: object) -> bool:
        if not isinstance(column, str | QueryableAttribute):
            return False
        return self._path(column) in self._stored

    def __repr__(self) -> str:
        return f"SaveValues({self._stored!r})"

    def _path(self, column: QueryableAttribute[Any] | str) -> str:
        if isinstance(column, str):
            return column
        model = type(self._record)
        owner = column.class_
        if not (isinstance(owner, type) and issubclass(model, owner)):
            raise AdminSiteError(
                f"{describe(column)} is not a column of {model.__name__}. A save "
                f"stores the record's own columns, such as {model.__name__}.id."
            )
        return column.key

    def _value_of(self, path: str) -> Any:
        if path in self._stored:
            return self._stored[path]
        first = path.split(".", 1)[0]
        if not hasattr(type(self._record), first):
            if self._form_only(path):
                return None
            raise AdminSiteError(
                f"{type(self._record).__name__} has no column named {path!r}, "
                "and the view asks for no value by that name."
            )
        value: Any = self._record
        for part in path.split("."):
            if value is None:
                return None
            if isinstance(value, list | tuple | set):
                return [_loaded(item, part, path) for item in value]
            value = _loaded(value, part, path)
        return value


class SaveValue(Generic[T]):
    """One column's value in a save, which a hook reads and can change."""

    __slots__ = ("_values", "path")

    def __init__(self, values: SaveValues, path: str) -> None:
        self._values = values
        # The column's name, or a dotted path such as "customer.email".
        self.path = path

    def get(self) -> T:
        """The value the column has once saved.

        That is the value the save stores, or the record's own when the
        save leaves the column as it is, such as a read-only field.
        """
        value: T = self._values._value_of(self.path)
        return value

    def set(self, value: T) -> None:
        """Store this value instead of the one given."""
        self._values._stored[self.path] = value

    def __repr__(self) -> str:
        return f"SaveValue({self.path!r})"


def stored_values(values: SaveValues) -> dict[str, Any]:
    """What a save stores, by path, once its hooks have changed it."""
    return dict(values._stored)


def _loaded(record: object, key: str, path: str) -> Any:
    """An attribute of a record, refusing one that would have to be loaded now.

    Loading it here would query the database outside the session's control,
    which an async session refuses with an error that names nothing.
    """
    state = sqlalchemy_inspect(record, raiseerr=False)
    if state is not None and state.has_identity and key in state.unloaded:
        raise AdminSiteError(
            f"{path!r} is not loaded on the {type(record).__name__}, and reading "
            "it here would need a query. Put it in the view's fields, or read it "
            "with context.session."
        )
    return getattr(record, key)


@dataclass
class SaveContext(Generic[M]):
    """What a save hook is given: `SaveContext[Order]` for an order's save.

    `before_save` and `after_save` run inside the save's transaction,
    `after_save_committed` once it has committed. In `before_save` the
    values have not been written onto the record yet, so that is where to
    change them: `context.values[Order.slug].set(...)`. Setting an
    attribute on `context.record` there would be overwritten a moment
    later by the value from the form.
    """

    session: SessionAdapter
    record: M
    values: SaveValues
    # Whether the save adds a new record rather than changing one.
    created: bool
    # None when the save was not made from a request, such as in a test.
    request: Request | None = None


@dataclass
class DeleteContext(Generic[M]):
    """What a delete hook is given: `DeleteContext[Order]` for an order's delete.

    `before_delete` and `after_delete` run inside the delete's transaction,
    `after_delete_committed` once it has committed.
    """

    session: SessionAdapter
    record: M
    # None when the delete was not made from a request, such as in a test.
    request: Request | None = None
