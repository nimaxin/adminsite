"""A JSON document drawn as a form: the parts the template draws, with their inputs.

Every part is filled from form values, input name by input name: those the
stored document turns into, or those a failed save sent, so both are drawn
the same way.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from adminsite.fields import EnumField, Field
from adminsite.fields.documents import (
    SET,
    Document,
    Fixed,
    Group,
    Pairs,
    Rows,
    Shape,
    Value,
    row_numbers,
    segment,
)
from adminsite.http.rows import Choice, FormRow, chosen_in_order
from adminsite.i18n import gettext as _

# Stands for a row's number in the row "add" copies, until it is copied.
BLANK = "__index__"


@dataclass
class DocumentRow:
    """One row of a document's list or map: the inputs on one line."""

    marker: str
    cells: list[FormRow]


@dataclass
class DocumentEntry:
    """One part of a document form, as the template draws it.

    A value has its `row`, drawn like any other field. A group holds its
    `entries`. A list of objects, or a map, holds its `rows`, a `blank` row
    that "add" copies, and the number the next row gets.
    """

    kind: str
    name: str
    label: str = ""
    note: str = ""
    error: str = ""
    row: FormRow | None = None
    entries: list["DocumentEntry"] = field(default_factory=list)
    columns: list[FormRow] = field(default_factory=list)
    rows: list[DocumentRow] = field(default_factory=list)
    blank: DocumentRow | None = None
    next_number: int = 0
    # A map whose keys are fixed: how many there are, so no more rows are
    # added once each has one.
    key_count: int = 0
    # Whether the part may be left out of the document, and whether it is in.
    settable: bool = False
    is_set: bool = True

    @property
    def anchor(self) -> str:
        """The id a message about this part links to."""
        return self.row.input_id if self.row is not None else f"field-{self.name}"

    def problems(self, trail: list[str]) -> list[tuple[str, str, str]]:
        """Each message in this part, with where it sits, for the form's summary."""
        here = [*trail, self.label] if self.label else trail
        found = []
        if self.error:
            found.append((self.anchor, ", ".join(here), self.error))
        if self.row is not None and self.row.error:
            found.append((self.row.input_id, ", ".join(here), self.row.error))
        for entry in self.entries:
            found.extend(entry.problems(here))
        for number, item in enumerate(self.rows, start=1):
            for cell in item.cells:
                if cell.error:
                    where = [*here, _("row {number}", number=number), cell.label]
                    found.append((cell.input_id, ", ".join(where), cell.error))
        return found


def document_form(
    document: Document,
    values: Mapping[str, Any],
    errors: Mapping[str, str],
    name: str,
) -> DocumentEntry:
    """The form for a document, filled from form values by input name."""
    return _Builder(document, values, errors).entry(
        document.shape, name, label="", note="", top=True
    )


class _Builder:
    def __init__(
        self,
        document: Document,
        values: Mapping[str, Any],
        errors: Mapping[str, str],
    ) -> None:
        self.document = document
        self.values = values
        self.errors = errors

    def entry(
        self, shape: Shape, name: str, *, label: str, note: str, top: bool = False
    ) -> DocumentEntry:
        error = self.errors.get(name, "")
        if isinstance(shape, Group):
            entries = []
            for found in shape.properties:
                if isinstance(found.shape, Fixed):
                    continue
                child = f"{name}.{segment(found.key)}"
                entry = self.entry(
                    found.shape, child, label=found.label, note=found.description
                )
                if top and self.document.partial:
                    entry.settable = True
                    entry.is_set = child + SET in self.values
                entries.append(entry)
            return DocumentEntry(
                "group", name, label=label, note=note, error=error, entries=entries
            )
        if isinstance(shape, Rows):
            columns = [
                (segment(one.key), one.shape.field)
                for one in shape.item.properties
                if isinstance(one.shape, Value)
            ]
            numbers = row_numbers(self.values, name)

            def line(number: str) -> DocumentRow:
                row = f"{name}.{number}"
                return DocumentRow(
                    row, [self.cell(item, f"{row}.{key}") for key, item in columns]
                )

            return DocumentEntry(
                "rows",
                name,
                label=label,
                note=note,
                error=error,
                columns=[self.cell(item, "") for _key, item in columns],
                rows=[line(str(number)) for number in numbers],
                blank=line(BLANK),
                next_number=max(numbers, default=-1) + 1,
            )
        if isinstance(shape, Pairs):
            numbers = row_numbers(self.values, name)

            def pair(number: str) -> DocumentRow:
                row = f"{name}.{number}"
                return DocumentRow(
                    row,
                    [
                        self.cell(shape.key, f"{row}.key"),
                        self.cell(shape.value, f"{row}.value"),
                    ],
                )

            return DocumentEntry(
                "pairs",
                name,
                label=label,
                note=note,
                error=error,
                rows=[pair(str(number)) for number in numbers],
                blank=pair(BLANK),
                next_number=max(numbers, default=-1) + 1,
                key_count=len(shape.keys),
            )
        if isinstance(shape, Value):
            return DocumentEntry(
                "value", name, label=label, row=self.value_row(shape.field, name)
            )
        return DocumentEntry("value", name, label=label)

    def value_row(self, item: Field[Any], name: str) -> FormRow:
        """A single value, as any field on a form is drawn."""
        raw = self.values.get(name)
        row = FormRow(
            path=name,
            field=item,
            value=_text(raw),
            error=self.errors.get(name, ""),
        )
        if isinstance(item, EnumField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            chosen = raw if isinstance(raw, list) else ([raw] if raw else [])
            row.selected = tuple(str(one) for one in chosen)
            if item.multiple:
                row.picked = chosen_in_order(row.choices, row.selected)
        return row

    def cell(self, item: Field[Any], name: str) -> FormRow:
        """A value in one row of a table, which may be left empty until used."""
        row = self.value_row(item, name)
        row.browser_required = False
        return row


def _text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, list):
        return str(raw[-1]) if raw else ""
    return str(raw)
