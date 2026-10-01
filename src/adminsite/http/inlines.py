from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from adminsite.fields import EnumField, RelationField
from adminsite.http.forms import title_for
from adminsite.http.rows import Choice, FormRow
from adminsite.views import Inline, ModelView
from adminsite.views.picker import PICKER_LIMIT, Picker

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.backends.sqlalchemy.session import SessionAdapter

__all__ = [
    "BLANK_INDEX",
    "InlineTable",
    "InlineTableRow",
    "build_inline_tables",
    "child_tables",
]

# Stands in for the row number in the blank row that "add another" copies.
BLANK_INDEX = "__index__"


@dataclass
class InlineTableRow:
    """One child in the inline table: its key and a cell per field."""

    key: str
    cells: list[FormRow]
    delete: bool = False


@dataclass
class InlineTable:
    """One inline as the form template draws it."""

    inline: Inline
    label: str
    headers: list[str]
    rows: list[InlineTableRow] = field(default_factory=list)
    blank: InlineTableRow | None = None

    @property
    def name(self) -> str:
        """The inline's name, which prefixes every input it draws."""
        return self.inline.name


@dataclass
class _RelationOptions:
    choices: list[Choice]
    searchable: bool


@dataclass
class _Row:
    """A child row to draw: where it sits, its key, and what it holds."""

    index: int | str
    key: str
    # The child it shows, or None for a new row.
    found: Any = None
    # What was typed in it, when the form comes back.
    typed: Mapping[str, Any] | None = None
    delete: bool = False
    # The fields it has an input for, and those among them it only shows.
    fields: Sequence[str] = ()
    readonly: set[str] = field(default_factory=set)


@dataclass
class _CellMaker:
    """Builds the input for one field of one child row."""

    admin: "Admin"
    inline: Inline
    child: ModelView[Any]
    options: Mapping[str, _RelationOptions]
    errors: Mapping[str, str]

    def row(self, row: _Row, paths: Sequence[str]) -> InlineTableRow:
        """A row's cells: one for each column, empty where the row has no such field."""
        cells = []
        for path in paths:
            if path not in row.fields:
                cells.append(self(row.index, path, None, readonly=True))
                continue
            readonly = path in row.readonly
            current = None
            if row.found is not None:
                current = self.child._value_at(row.found, path)
            raw = None
            if row.typed is not None and not readonly:
                raw = _text(row.typed.get(self.inline.input_name(row.index, path)))
            cells.append(self(row.index, path, current, raw, readonly=readonly))
        return InlineTableRow(key=row.key, cells=cells, delete=row.delete)

    def __call__(
        self,
        index: int | str,
        path: str,
        current: Any,
        raw: Any = None,
        *,
        readonly: bool = False,
    ) -> FormRow:
        item = self.child._field_for(path)
        name = self.inline.input_name(index, path)
        row = FormRow(
            path=name,
            field=item,
            value=str(raw) if raw is not None else item.serialize(current),
            display=item.display(current),
            error=self.errors.get(name, ""),
            readonly=readonly,
            lookup_path=f"{self.inline.name}.{path}",
            browser_required=False,
        )
        if isinstance(item, RelationField) and raw is None and current is not None:
            # A linked record is named as the form names it, not by its bare
            # text, which for a model is only its class and address.
            row.display = title_for(self.admin, item, current)
        if isinstance(item, EnumField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = (row.value,) if row.value else ()
        elif isinstance(item, RelationField) and path in self.options:
            found = self.options[path]
            row.choices = found.choices
            row.searchable = found.searchable
            if raw is None and current is not None:
                picker = Picker(self.admin.views, self.admin.inspector, item)
                row.value = picker.key_of(current)
                row.picked_label = row.display
                row.picked = (Choice(row.value, row.picked_label),)
            row.selected = (row.value,) if row.value else ()
        return row


async def build_inline_tables(
    admin: "Admin",
    view: ModelView[Any],
    session: "SessionAdapter",
    *,
    record: Any = None,
    submitted: Mapping[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
    request: Any = None,
) -> list[InlineTable]:
    """Build every inline table, from the record or from what was submitted."""
    errors = errors or {}
    tables = []
    for inline in view.get_inlines(request, record):
        child = view._inline_view(inline.name)
        children = list(getattr(record, inline.name, None) or []) if record else []
        if submitted is not None:
            rows = _rows_from_form(inline, child, children, submitted)
        else:
            rows = [
                _Row(index, child._identity_of(found), found)
                for index, found in enumerate(children)
            ]
            # Blank rows only where there are no rows yet: a record that has
            # its children needs no empty line under them, only a way to add one.
            for extra in range(0 if children else inline.blank_rows):
                rows.append(_Row(len(children) + extra, ""))
        blank = _Row(BLANK_INDEX, "")

        # A new row has the create page's fields and an existing child the
        # edit page's, as a view's forms do. The table has a column for each.
        locked = view._inline_readonly(inline, request, record)
        for row in (*rows, blank):
            row.fields = child._form_fields(request, row.found)
            row.readonly = locked | set(child._readonly_paths(request, row.found))
        drawn = {path for row in (*rows, blank) for path in row.fields}
        paths = [path for path in child._candidates() if path in drawn]

        options = {
            path: await _relation_options(
                admin, session, child._field_for(path), request
            )
            for path in paths
            if isinstance(child._field_for(path), RelationField)
        }
        table = InlineTable(
            inline=inline,
            label=inline.label or view._label_for(inline.name),
            headers=[child._label_for(path) for path in paths],
        )
        cell = _CellMaker(
            admin=admin,
            inline=inline,
            child=child,
            options=options,
            errors=errors,
        )
        table.rows = [cell.row(row, paths) for row in rows]
        table.blank = cell.row(blank, paths)
        tables.append(table)
    return tables


def _rows_from_form(
    inline: Inline,
    child: ModelView[Any],
    children: Sequence[Any],
    submitted: Mapping[str, Any],
) -> list[_Row]:
    """The rows as they were typed, so nothing is lost on an error."""
    try:
        count = int(_text(submitted.get(f"{inline.name}-count")) or 0)
    except ValueError:
        count = 0
    by_key = {child._identity_of(found): found for found in children}
    rows = []
    for index in range(count):
        key = _text(submitted.get(inline.input_name(index, "key")))
        rows.append(
            _Row(
                index,
                key,
                by_key.get(key.strip()),
                typed=submitted,
                delete=submitted.get(inline.input_name(index, "delete")) is not None,
            )
        )
    return rows


async def _relation_options(
    admin: "Admin", session: "SessionAdapter", item: Any, request: Any = None
) -> _RelationOptions:
    """Load a link's choices once, for every row of the table to share."""
    picker = Picker(admin.views, admin.inspector, item, request)
    page = await picker.offered(session, limit=PICKER_LIMIT)
    if page is None:
        # The user may see none of these records: an empty picker, not a
        # search box that would find nothing either.
        return _RelationOptions(choices=[], searchable=False)
    if page.has_next:
        return _RelationOptions(choices=[], searchable=True)
    return _RelationOptions(
        choices=[
            Choice(picker.key_of(found), title_for(admin, item, found))
            for found in page.rows
        ],
        searchable=False,
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return str(value[0]) if value else ""
    return str(value)


def child_tables(
    view: ModelView[Any], record: Any, request: Any = None
) -> list[dict[str, Any]]:
    """The children of a record, read only, for its detail page."""
    tables = []
    for inline in view.get_inlines(request, record):
        child = view._inline_view(inline.name)
        paths = child._detail_fields(request)
        children = list(getattr(record, inline.name, None) or [])
        tables.append(
            {
                "label": inline.label or view._label_for(inline.name),
                "headers": [child._label_for(path) for path in paths],
                "numeric": [
                    child._field_for(path).widget == "number" for path in paths
                ],
                "rows": [
                    [child._display(found, path) for path in paths]
                    for found in children
                ],
            }
        )
    return tables
