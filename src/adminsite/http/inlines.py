from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.fields import ChoiceField, RelationField
from adminsite.http.forms import PICKER_LIMIT, Choice, FormRow, title_for
from adminsite.query import CountMode, QuerySpec
from adminsite.views import Inline, ModelView

if TYPE_CHECKING:
    from adminsite.admin import Admin

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
class _CellMaker:
    """Builds the input for one field of one child row."""

    admin: "Admin"
    inline: Inline
    child: ModelView
    readonly: set[str]
    options: Mapping[str, _RelationOptions]
    errors: Mapping[str, str]

    def __call__(
        self, index: int | str, path: str, current: Any, raw: Any = None
    ) -> FormRow:
        item = self.child.field_for(path)
        name = self.inline.input_name(index, path)
        row = FormRow(
            path=name,
            field=item,
            value=str(raw) if raw is not None else item.serialize(current),
            display=item.display(current),
            error=self.errors.get(name, ""),
            readonly=path in self.readonly,
            lookup_path=f"{self.inline.name}.{path}",
            browser_required=False,
        )
        if isinstance(item, ChoiceField):
            row.choices = [Choice(value, label) for value, label in item.choices]
            row.selected = (row.value,) if row.value else ()
        elif isinstance(item, RelationField) and path in self.options:
            found = self.options[path]
            row.choices = found.choices
            row.searchable = found.searchable
            if raw is None and current is not None:
                repository = SQLAlchemyRepository(item.target, self.admin.inspector)
                row.value = repository.identity_of(current)
                row.picked_label = item.display(current)
            row.selected = (row.value,) if row.value else ()
        return row


async def build_inline_tables(
    admin: "Admin",
    view: ModelView,
    session: SessionAdapter,
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
        child = view.inline_view(inline.name)
        paths = child.get_form_fields(request)
        readonly = set(child.get_readonly_fields(request))
        options = {
            path: await _relation_options(admin, session, child.field_for(path))
            for path in paths
            if isinstance(child.field_for(path), RelationField)
        }
        table = InlineTable(
            inline=inline,
            label=inline.label or view.label_for(inline.name),
            headers=[child.label_for(path) for path in paths],
        )

        cell = _CellMaker(
            admin=admin,
            inline=inline,
            child=child,
            readonly=readonly,
            options=options,
            errors=errors,
        )

        if submitted is not None:
            table.rows = _rows_from_form(inline, paths, submitted, cell)
        else:
            children = list(getattr(record, inline.name, None) or []) if record else []
            for index, found in enumerate(children):
                table.rows.append(
                    InlineTableRow(
                        key=child.identity_of(found),
                        cells=[
                            cell(index, path, child.value_at(found, path))
                            for path in paths
                        ],
                    )
                )
            for extra in range(inline.extra):
                index = len(children) + extra
                table.rows.append(
                    InlineTableRow(
                        key="", cells=[cell(index, path, None) for path in paths]
                    )
                )

        table.blank = InlineTableRow(
            key="", cells=[cell(BLANK_INDEX, path, None) for path in paths]
        )
        tables.append(table)
    return tables


def _rows_from_form(
    inline: Inline,
    paths: Sequence[str],
    submitted: Mapping[str, Any],
    cell: _CellMaker,
) -> list[InlineTableRow]:
    """Rebuild the rows as they were typed, so nothing is lost on an error."""
    try:
        count = int(_text(submitted.get(f"{inline.name}-count")) or 0)
    except ValueError:
        count = 0
    rows = []
    for index in range(count):
        rows.append(
            InlineTableRow(
                key=_text(submitted.get(inline.input_name(index, "key"))),
                delete=submitted.get(inline.input_name(index, "delete")) is not None,
                cells=[
                    cell(
                        index,
                        path,
                        None,
                        _text(submitted.get(inline.input_name(index, path))),
                    )
                    for path in paths
                ],
            )
        )
    return rows


async def _relation_options(
    admin: "Admin", session: SessionAdapter, item: Any
) -> _RelationOptions:
    """Load a link's choices once, for every row of the table to share."""
    repository = SQLAlchemyRepository(item.target, admin.inspector)
    total = await repository.count(session, QuerySpec(count=CountMode.EXACT))
    if total > PICKER_LIMIT:
        return _RelationOptions(choices=[], searchable=True)
    page = await repository.list(
        session, QuerySpec(limit=PICKER_LIMIT, count=CountMode.NONE)
    )
    return _RelationOptions(
        choices=[
            Choice(repository.identity_of(found), title_for(admin, item, found))
            for found in page
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
    view: ModelView, record: Any, request: Any = None
) -> list[dict[str, Any]]:
    """The children of a record, read only, for its detail page."""
    tables = []
    for inline in view.get_inlines(request, record):
        child = view.inline_view(inline.name)
        paths = child.get_form_fields(request)
        children = list(getattr(record, inline.name, None) or [])
        tables.append(
            {
                "label": inline.label or view.label_for(inline.name),
                "headers": [child.label_for(path) for path in paths],
                "rows": [
                    [child.display(found, path) for path in paths] for found in children
                ],
            }
        )
    return tables
