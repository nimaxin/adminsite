from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.views.inline import InlineRow

FormData = Mapping[str, str | Sequence[str]]


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


@dataclass
class SaveContext:
    """What a save hook is given. Everything here is inside one transaction.

    In `before_save` the values have not been written onto the record yet,
    so that is where to change them: `context.values` is the dictionary
    that is about to be applied, and `set` writes into it. Setting an
    attribute on `context.record` there would be overwritten a moment
    later by the value from the form.
    """

    session: SessionAdapter
    record: Any
    values: dict[str, Any]
    created: bool
    request: Any = None

    def set(self, path: str, value: Any) -> None:
        """Change a value before it is stored."""
        self.values[path] = value


@dataclass
class DeleteContext:
    """What a delete hook is given, inside the same transaction."""

    session: SessionAdapter
    record: Any
    request: Any = None
