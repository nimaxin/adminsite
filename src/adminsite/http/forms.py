import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adminsite.actions.action import Action
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.fields import ChoiceField, Field, FileField, RelationField
from adminsite.http.picker import PICKER_LIMIT, Picker
from adminsite.http.urls import Urls
from adminsite.i18n import gettext as _
from adminsite.views import ModelView
from adminsite.views.naming import name_linked

if TYPE_CHECKING:
    from adminsite.admin import Admin


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
    # True where leaving it empty keeps what the record has, as for a
    # password: then it is never required, and the form says so.
    keeps_when_blank: bool = False
    # Put before the path in the input's id, so two dialogs on one page that
    # both ask for a "product" do not share one.
    id_prefix: str = ""
    # Where a searchable link looks records up, when it is not the form's
    # own lookup, as for a link an action asks for.
    lookup_url: str = ""

    @property
    def input_id(self) -> str:
        """The id of the input itself."""
        return f"field-{self.id_prefix}{self.path}"

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
        return self.field.required and not self.keeps_when_blank

    @property
    def note(self) -> str:
        """The help under the input: the field's own, or how to fill it in."""
        if self.field.help_text:
            return self.field.help_text
        if self.keeps_when_blank:
            return _("Leave it empty to keep the current one.")
        # How to fill it in means nothing where it cannot be changed.
        return "" if self.readonly else self.field.hint()


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
    if record is not None:
        # A computed value on the form, read only, may come from a loader.
        await view.load_values(
            session, [record], view.get_form_fields(request, record), request=request
        )

    starting = await view.form_values(session, record, request=request)

    rows = []
    for path in view.get_form_fields(request, record):
        item = view.field_for(path)
        if item.form_only:
            stored = starting.get(path)
        else:
            stored = view.value_at(record, path) if record is not None else None
        # A file cannot be put back into a file input, so after a failed
        # submit the field shows what is stored, not what was sent.
        current = (
            submitted[path]
            if path in submitted and not isinstance(item, FileField)
            else stored
        )
        if current is None and record is None and path not in submitted:
            # A new record starts from whatever the field says it starts from.
            current = item.default
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
            keeps_when_blank=item.blank_keeps and record is not None,
        )
        if isinstance(item, ChoiceField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = item.values_of(current)
        elif isinstance(item, RelationField):
            await _fill_relation(admin, session, row, item, current, request)
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
    request: Any = None,
) -> None:
    """Give a relation field either a list of records or a search box."""
    picker = Picker(admin, item, request)
    row.selected = tuple(_keys_of(picker.repository, current))
    row.value = row.selected[0] if row.selected else ""

    # One page plus a probe row: enough to list them, or to know there are
    # too many to list.
    page = await picker.offered(session, limit=PICKER_LIMIT)
    if page is None:
        # The user may see none of these records, so there is nothing to
        # offer. The form still draws, with an empty picker.
        return

    row.searchable = page.has_next
    if row.searchable:
        row.picked = await _picked(admin, session, picker, item, current)
        row.picked_label = row.picked[0].label if row.picked else ""
        return

    row.choices = [
        Choice(picker.repository.identity_of(found), title_for(admin, item, found))
        for found in page.rows
    ]


async def _picked(
    admin: "Admin",
    session: SessionAdapter,
    picker: Picker,
    item: RelationField,
    current: Any,
) -> list[Choice]:
    """The records a link already holds, each with the name to show.

    After a failed submit the values are keys rather than records, so the
    records are read back to name them, through the target's own view.
    """
    found = current if isinstance(current, list | tuple | set) else [current]
    repository = picker.repository
    chosen: list[Choice] = []
    for one in found:
        record = one
        if record is not None and not isinstance(record, repository.model):
            record = await picker.get(session, str(one))
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
    """Name a related record in a picker, as it is named everywhere else."""
    return name_linked(item, record, views=admin.views, inspector=admin.inspector)


def rows_for_inputs(fields: Sequence[Field], *, prefix: str = "") -> list[FormRow]:
    """Build the form rows for the values an action asks for.

    Each starts at the field's default, so a dialog of five switches that
    are usually on opens with them on.
    """
    rows = []
    for item in fields:
        row = FormRow(
            path=item.name,
            field=item,
            value=item.serialize(item.default),
            id_prefix=prefix,
        )
        if isinstance(item, ChoiceField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = item.values_of(item.default)
        rows.append(row)
    return rows


async def rows_for_actions(
    admin: "Admin",
    view: ModelView,
    actions: Sequence[Action],
    request: Any = None,
) -> dict[str, list[FormRow]]:
    """The rows each action's dialog asks for, by the action's name.

    A link among them offers the records its target's view lets this user
    see, as a form's link does, and searches where there are too many to
    list. A session is opened only when some action asks for a link.
    """
    rows = {
        found.name: rows_for_inputs(found.inputs, prefix=f"{found.name}-")
        for found in actions
    }
    links = [
        (found, row, row.field)
        for found in actions
        for row in rows[found.name]
        if isinstance(row.field, RelationField)
    ]
    if not links:
        return rows
    urls = Urls(request)
    async with admin.database.session() as session:
        for found, row, item in links:
            await _fill_relation(admin, session, row, item, None, request)
            row.lookup_url = urls.action_lookup(view, found.name, row.path)
    return rows
