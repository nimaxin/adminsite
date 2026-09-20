import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.fields import ChoiceField, Field, RelationField
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
) -> list[FormRow]:
    """Build the form, filled from the record or from what was submitted."""
    readonly = set(view.get_readonly_fields(request, record))
    errors = errors or {}
    submitted = submitted or {}

    rows = []
    for path in view.get_form_fields(request, record):
        item = view.field_for(path)
        current = (
            submitted[path]
            if path in submitted
            else (view.value_at(record, path) if record is not None else None)
        )
        row = FormRow(
            path=path,
            field=item,
            value=item.serialize(current),
            display=item.display(current),
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
    row.picked_label = item.display(current)
    row.searchable = total > PICKER_LIMIT
    row.value = row.selected[0] if row.selected else ""

    if not row.searchable:
        page = await repository.list(
            session, QuerySpec(limit=PICKER_LIMIT, count=CountMode.NONE)
        )
        row.choices = [
            Choice(repository.identity_of(found), title_for(admin, item, found))
            for found in page
        ]


def _keys_of(repository: SQLAlchemyRepository, current: Any) -> list[str]:
    if current is None:
        return []
    records = current if isinstance(current, list | tuple | set) else [current]
    return [repository.identity_of(record) for record in records]


def title_for(admin: "Admin", item: RelationField, record: Any) -> str:
    """Name a related record, preferring the view registered for its model."""
    view = admin.views.for_model(item.target)
    if view is not None and view.display_template:
        return view.title_of(record)
    return item.label_for(record)
