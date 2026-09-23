import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.fields import ChoiceField, Field, FileField, RelationField
from adminsite.query import CountMode, QuerySpec
from adminsite.views import ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

# Above this many records a relation is searched rather than listed.
PICKER_LIMIT = 100


@dataclass
class Choice:
    """One option in a select or a relation picker."""

    value: str
    label: str


@dataclass
class FormRow:
    """One field as the form template needs it."""

    path: str
    field: Field
    value: str = ""
    display: str = ""
    error: str = ""
    readonly: bool = False
    choices: Sequence[Choice] = dataclasses.field(default_factory=tuple)
    selected: Sequence[str] = dataclasses.field(default_factory=tuple)
    searchable: bool = False
    picked_label: str = ""
    # The records a searchable link already holds, each with its name, so a
    # relationship holding many of them can be edited one chip at a time.
    picked: Sequence[Choice] = dataclasses.field(default_factory=tuple)
    # The path the lookup searches by, when it differs from the input name,
    # as it does for a link inside an inline row.
    lookup_path: str = ""
    # False where an empty value is allowed in the browser even for a
    # required field, as in a blank inline row that may be left unused.
    browser_required: bool = True

    @property
    def picked_pairs(self) -> list[dict[str, str]]:
        """The records already held, as the picker's script reads them."""
        return [{"value": one.value, "label": one.label} for one in self.picked]

    @property
    def widget(self) -> str:
        """Which template renders this field."""
        return self.field.widget

    @property
    def label(self) -> str:
        """The text above the input."""
        return self.field.label

    @property
    def required(self) -> bool:
        """Whether a value has to be given."""
        return self.field.required


async def build_rows(
    admin: "Admin",
    view: ModelView,
    session: SessionAdapter,
    *,
    record: Any = None,
    submitted: Mapping[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
    request: Any = None,
    typed: Mapping[str, Any] | None = None,
) -> list[FormRow]:
    """Build the form, filled from the record or from what was submitted.

    `submitted` holds the values that were read; `typed` holds the text they
    were read from, which is what a field that failed shows again.
    """
    readonly = set(view.get_readonly_fields(request, record))
    errors = errors or {}
    submitted = submitted or {}
    typed = typed or {}

    rows = []
    for path in view.get_form_fields(request, record):
        item = view.field_for(path)
        stored = view.value_at(record, path) if record is not None else None
        # A file cannot be put back into a file input, so after a failed
        # submit the field shows what is stored, not what was sent.
        current = (
            submitted[path]
            if path in submitted and not isinstance(item, FileField)
            else stored
        )
        row = FormRow(
            path=path,
            field=item,
            value=written_again(item, path, current, errors, typed),
            display=(
                item.text_for(record, current)
                if record is not None
                else item.display(current)
            ),
            error=errors.get(path, ""),
            readonly=path in readonly,
        )
        if isinstance(item, ChoiceField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = (row.value,) if row.value else ()
        elif isinstance(item, RelationField):
            await _fill_relation(admin, session, row, item, current)
        rows.append(row)
    return rows


def written_again(
    item: Field,
    path: str,
    current: Any,
    errors: Mapping[str, str],
    typed: Mapping[str, Any],
) -> str:
    """What an input shows: the value, or the text that could not be read.

    A value that failed to parse has no parsed form to show, so the words
    the person wrote go back into the input rather than being thrown away.
    """
    raw = typed.get(path)
    if path in errors and isinstance(raw, str):
        return raw
    return item.serialize(current)


async def _fill_relation(
    admin: "Admin",
    session: SessionAdapter,
    row: FormRow,
    item: RelationField,
    current: Any,
) -> None:
    """Give a relation field either a list of records or a search box."""
    repository = SQLAlchemyRepository(item.target, admin.inspector)
    total = await repository.count(session, QuerySpec(count=CountMode.EXACT))

    row.selected = tuple(_keys_of(repository, current))
    row.searchable = total > PICKER_LIMIT
    row.value = row.selected[0] if row.selected else ""

    if row.searchable:
        row.picked = await _picked(admin, session, repository, item, current)
        row.picked_label = row.picked[0].label if row.picked else ""

    if not row.searchable:
        page = await repository.list(
            session, QuerySpec(limit=PICKER_LIMIT, count=CountMode.NONE)
        )
        row.choices = [
            Choice(repository.identity_of(found), title_for(admin, item, found))
            for found in page
        ]


async def _picked(
    admin: "Admin",
    session: SessionAdapter,
    repository: SQLAlchemyRepository,
    item: RelationField,
    current: Any,
) -> list[Choice]:
    """The records a link already holds, each with the name to show.

    After a failed submit the values are keys rather than records, so the
    records are read back to name them.
    """
    found = current if isinstance(current, list | tuple | set) else [current]
    chosen: list[Choice] = []
    for one in found:
        record = one
        if record is not None and not isinstance(record, repository.model):
            record = await repository.get(session, str(one))
        if record is None:
            continue
        chosen.append(
            Choice(repository.identity_of(record), title_for(admin, item, record))
        )
    return chosen


def _keys_of(repository: SQLAlchemyRepository, current: Any) -> list[str]:
    if current is None or current == "":
        return []
    items = current if isinstance(current, list | tuple | set) else [current]
    # After a failed submit the values are the keys that were picked, not
    # records, so they are already what the picker needs.
    return [
        repository.identity_of(item)
        if isinstance(item, repository.model)
        else str(item)
        for item in items
    ]


def title_for(admin: "Admin", item: RelationField, record: Any) -> str:
    """Name a related record, preferring the view registered for its model."""
    view = admin.views.for_model(item.target)
    if view is not None and view.display_template:
        return view.title_of(record)
    return item.label_for(record)


def rows_for_inputs(fields: Sequence[Field]) -> list[FormRow]:
    """Build empty form rows for the values an action asks for."""
    rows = []
    for item in fields:
        row = FormRow(path=item.name, field=item)
        if isinstance(item, ChoiceField):
            row.choices = [Choice(value, label) for value, label in item.choices]
        rows.append(row)
    return rows
