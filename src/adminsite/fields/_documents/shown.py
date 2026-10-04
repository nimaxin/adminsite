"""A document as the record page shows it."""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from adminsite.fields._documents.inputs import DocumentCode, _python_value
from adminsite.fields._documents.shapes import (
    MISSING,
    Fixed,
    Group,
    Pairs,
    Rows,
    Shape,
    Value,
)
from adminsite.fields.base import Field

__all__ = [
    "Shown",
]


@dataclass
class Shown:
    """One part of a document as the record page shows it.

    A value has its `text`; a group its `entries`; rows and pairs their
    `columns` and `rows`; and a part the form writes as JSON its `code`.
    """

    kind: str
    label: str = ""
    text: str = ""
    field: Field[Any] | None = None
    value: Any = None
    entries: list["Shown"] = dataclasses.field(default_factory=list)
    columns: list[str] = dataclasses.field(default_factory=list)
    rows: list[list["Shown"]] = dataclasses.field(default_factory=list)
    code: str = ""
    # A field a partial document leaves out.
    unset: bool = False

    @property
    def unset_entries(self) -> list["Shown"]:
        """The fields a partial document leaves out, in the schema's order."""
        return [entry for entry in self.entries if entry.unset]

    @property
    def missing(self) -> bool:
        """Whether the document has no value here at all, not even an empty one."""
        return self.value is MISSING

    @property
    def empty(self) -> bool:
        """Whether the document holds nothing here."""
        if self.kind == "value":
            return self.value is MISSING or self.value is None or self.text == ""
        if self.kind == "code":
            return self.value is MISSING
        if self.kind in ("rows", "pairs"):
            return not self.rows
        return False


def _shown(shape: Shape, value: Any, label: str) -> Shown:
    if isinstance(shape, Group):
        given = value if isinstance(value, Mapping) else {}
        return Shown(
            "group",
            label,
            entries=[
                _shown(found.shape, given.get(found.key, MISSING), found.label)
                for found in shape.properties
                if not isinstance(found.shape, Fixed)
            ],
        )
    if isinstance(shape, Rows):
        columns = [
            one for one in shape.item.properties if not isinstance(one.shape, Fixed)
        ]
        items = value if isinstance(value, list) else []
        return Shown(
            "rows",
            label,
            value=value,
            columns=[one.label for one in columns],
            rows=[
                [
                    _shown(one.shape, item.get(one.key, MISSING), one.label)
                    for one in columns
                ]
                for item in items
                if isinstance(item, Mapping)
            ],
        )
    if isinstance(shape, Pairs):
        given = value if isinstance(value, Mapping) else {}
        return Shown(
            "pairs",
            label,
            value=value,
            rows=[
                [
                    Shown("key", text=shape.key.display(key), value=key),
                    _shown(Value(shape.value), one, ""),
                ]
                for key, one in given.items()
            ],
        )
    if isinstance(shape, Value):
        item = shape.field
        if isinstance(item, DocumentCode):
            code = "" if value is MISSING else item.display(value)
            return Shown("code", label, value=value, code=code)
        if value is MISSING or value is None:
            return Shown("value", label, field=item, value=value)
        python = _python_value(item, value)
        return Shown(
            "value", label, text=item.display(python), field=item, value=python
        )
    return Shown("value", label, value=MISSING)
