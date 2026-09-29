from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from adminsite.actions.action import Action
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.fields import (
    BaseField,
    EnumField,
    FileField,
    JSONField,
    RelationField,
)
from adminsite.fields.documents import DRAWN
from adminsite.http.documents import document_form
from adminsite.http.picker import PICKER_LIMIT, Picker
from adminsite.http.rows import Choice, FormRow, chosen_in_order
from adminsite.http.urls import Urls
from adminsite.views import ModelView
from adminsite.views.naming import name_linked

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "build_rows",
    "fill_document",
    "rows_for_actions",
    "rows_for_inputs",
    "title_for",
    "written_again",
]


async def build_rows(
    admin: "Admin",
    view: ModelView[Any],
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
    readonly = set(view._readonly_paths(request, record))
    errors = errors or {}
    submitted = submitted or {}
    typed = typed or {}
    if record is not None:
        # A computed value on the form, read only, may come from a loader.
        await view._load_values(
            session, [record], view._form_fields(request, record), request=request
        )

    starting = await view.form_only_values(session, record, request=request)
    draft: Any = None

    rows = []
    for path in view._form_fields(request, record):
        item = view._field_for(path)
        if item.form_only:
            stored = starting.get(path)
        else:
            stored = view._value_at(record, path) if record is not None else None
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
            keeps_when_blank=item.keeps_value_when_blank and record is not None,
        )
        if isinstance(item, EnumField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = item.values_of(current)
            if item.multiple:
                row.picked = chosen_in_order(row.choices, row.selected)
        elif isinstance(item, RelationField):
            await _fill_relation(admin, session, row, item, current, request)
        elif isinstance(item, JSONField) and path not in readonly:
            owner = record
            if record is None and item.schema_from_record:
                # A new record's schema may follow what the form holds, as
                # a setting's key decides the shape of its value.
                if draft is None:
                    draft = view._draft_record(typed, request)
                owner = draft
                row.redraw_url = f"{Urls(request).document(view, path)}?show=form"
            fill_document(row, item, owner, current, typed, errors)
        rows.append(row)
    return rows


def fill_document(
    row: FormRow,
    item: JSONField,
    record: Any,
    current: Any,
    typed: Mapping[str, Any],
    errors: Mapping[str, str],
) -> None:
    """Draw a JSON column as a form, where it has a schema its value fits."""
    if row.path + DRAWN in typed:
        # Shown again after a failed save: each input as it was sent.
        document = item.document_for(record)
        values: Mapping[str, Any] = typed
    else:
        document = item.form_document(record, current)
        values = document.form_values(current, row.path) if document else {}
    if document is not None and document.drawn:
        row.document = document_form(document, values, errors, row.path)


def written_again(
    item: BaseField,
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
    if item.collection:
        row.picked = chosen_in_order(row.choices, row.selected)


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


def _keys_of(repository: SQLAlchemyRepository[Any], current: Any) -> list[str]:
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


def rows_for_inputs(
    fields: Sequence[BaseField],
    *,
    prefix: str = "",
    headings: Mapping[str, str] | None = None,
) -> list[FormRow]:
    """Build the form rows for the values an action asks for.

    Each starts at the field's default, so a dialog of five switches that
    are usually on opens with them on. `headings` names the group an input
    is drawn under, by the input's name.
    """
    rows = []
    for item in fields:
        row = FormRow(
            path=item.name,
            field=item,
            value=item.serialize(item.default),
            id_prefix=prefix,
            group=(headings or {}).get(item.name, ""),
        )
        if isinstance(item, EnumField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = item.values_of(item.default)
            if item.multiple:
                row.picked = chosen_in_order(row.choices, row.selected)
        rows.append(row)
    return rows


async def rows_for_actions(
    admin: "Admin",
    view: ModelView[Any],
    actions: Sequence[Action],
    request: Any = None,
) -> dict[str, list[FormRow]]:
    """The rows each action's dialog asks for, by the action's name.

    A link among them offers the records its target's view lets this user
    see, as a form's link does, and searches where there are too many to
    list. A session is opened only when some action asks for a link.
    """
    rows = {
        found.name: rows_for_inputs(
            found.inputs, prefix=f"{found.name}-", headings=found.headings
        )
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
