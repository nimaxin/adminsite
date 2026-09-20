from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from adminsite.backends.sqlalchemy.session import SessionAdapter

FormData = Mapping[str, str | Sequence[str]]


@dataclass(frozen=True, slots=True)
class FormResult:
    """What a submitted form turned into: values, or messages to show."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the form can be saved."""
        return not self.errors


@dataclass
class SaveContext:
    """What a save hook is given. Everything here is inside one transaction."""

    session: SessionAdapter
    record: Any
    values: Mapping[str, Any]
    created: bool
    request: Any = None


@dataclass
class DeleteContext:
    """What a delete hook is given, inside the same transaction."""

    session: SessionAdapter
    record: Any
    request: Any = None
