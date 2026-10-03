from collections.abc import Sequence
from copy import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import QueryableAttribute

from adminsite.columns import describe, path_of
from adminsite.exceptions import AdminSiteError
from adminsite.fields import BaseField, Field
from adminsite.schema import RelationDirection
from adminsite.security import RequestAction
from adminsite.views.checks import check_title
from adminsite.views.settings import default_paths

if TYPE_CHECKING:
    from adminsite.backends.sqlalchemy.inspector import SQLAlchemyInspector
    from adminsite.columns import ColumnReference
    from adminsite.fields import FieldRegistry
    from adminsite.schema import ModelSchema
    from adminsite.views.model_view import ModelView
    from adminsite.views.settings import SettingsReader

__all__ = [
    "Inline",
    "InlineRow",
    "InlineViews",
]


@dataclass(frozen=True)
class Inline:
    """Child records edited inside their parent's form, such as order lines.

    ```python
    class OrderView(ModelView[Order]):
        inlines = [
            Inline(
                Order.items,
                fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
            )
        ]
    ```

    The relation is a relationship on the parent that holds many records,
    by attribute or by name. Each child shows as a row of inputs, with a box
    to delete it, and new rows can be added in the form.
    """

    relation: str | QueryableAttribute[Any]
    # The child's fields, in order, as a view's fields are written:
    # Field(OrderItem.added_at, read_only=True) shows a value it never edits,
    # so a new row takes it from a default on the model.
    fields: Sequence["ColumnReference | Field[Any]"] = ()
    label: str = ""
    # How many blank rows the table starts with while it has no rows yet.
    blank_rows: int = 1
    can_delete: bool = True
    # How a child is named, as a view's record_title.
    record_title: str = ""

    @property
    def name(self) -> str:
        """The relationship's name, which the inline's inputs start with."""
        if isinstance(self.relation, str):
            return self.relation
        return self.relation.key

    def input_name(self, index: int | str, path: str) -> str:
        """The name a child's input carries in the submitted form."""
        return f"{self.name}-{index}-{path}"


@dataclass
class InlineRow:
    """One child as it came back from the form."""

    key: str
    values: dict[str, Any] = field(default_factory=dict)
    delete: bool = False
    # Where the row sat in the form, which names its inputs, so a refusal
    # about one of them can be shown beside it.
    index: int | None = None

    @property
    def is_new(self) -> bool:
        """Whether this row adds a child rather than changing one."""
        return not self.key


class InlineViews(dict[str, "ModelView[Any]"]):
    """The views that read and write a view's inline children, by inline name.

    Each is built when the view starts, so a mistake in an inline stops the
    admin starting. Asking for an inline the view does not have names the
    view, rather than raising a bare KeyError.
    """

    def __init__(
        self,
        view: "ModelView[Any]",
        settings: "SettingsReader",
        schema: "ModelSchema",
        inspector: "SQLAlchemyInspector",
        registry: "FieldRegistry",
    ) -> None:
        super().__init__()
        self._view = view
        self._settings = settings
        self._schema = schema
        self._inspector = inspector
        self._registry = registry
        for index, inline in enumerate(settings.entries("inlines", view.inlines)):
            self[inline.name] = self._build(inline, f"inlines[{index}]")

    def __missing__(self, name: str) -> "ModelView[Any]":
        raise AdminSiteError(
            f"{type(self._view).__name__} has no inline called {name!r}."
        )

    def _build(self, inline: Inline, setting: str) -> "ModelView[Any]":
        """Build the view that reads and writes one inline's children."""
        # model_view imports this module for Inline, so ModelView is
        # imported here, once both modules are loaded.
        from adminsite.views.model_view import ModelView

        self._settings.converted(setting, inline.relation, path_of)
        self._settings.check(setting, inline.name, self._view.model, "paths")
        relation = self._schema.relation_named(inline.name)
        if not relation.collection:
            raise AdminSiteError(
                f"{type(self._view).__name__}.inlines names {inline.name!r}, which "
                "holds one record. An inline needs a relationship holding many."
            )
        if inline.record_title:
            check_title(
                f"{type(self._view).__name__}.{setting}.record_title: "
                f"{describe(inline.record_title)}",
                inline.record_title,
                relation.target,
                self._inspector,
            )
        # The relationship fills in the child's columns it joins on, and so
        # every link of the child made of them, the link back among them. None
        # of them appears in the child rows. Another link to the parent does.
        target = self._inspector.inspect(relation.target)
        joined = set(relation.remote_columns)
        filled = [
            *relation.remote_columns,
            *(
                found.name
                for found in target.relations.values()
                if found.direction is RelationDirection.MANY_TO_ONE
                and found.local_columns
                and set(found.local_columns) <= joined
            ),
        ]
        # Checked here, so a mistake names the parent's setting. The child view
        # takes the entries as they are, fields with their options included.
        entries = self._settings.entries(f"{setting}.fields", inline.fields)
        named = self._settings.paths(
            f"{setting}.fields",
            [
                entry.column if isinstance(entry, Field) else entry
                for entry in entries
                if not (isinstance(entry, Field) and entry.form_only)
            ],
            relation.target,
        )
        # Left off only where the rows show it, since an exclude list names
        # only fields the view shows.
        shown = named if entries else default_paths(target)
        filled = [name for name in filled if name in shown]

        def can_access_field(
            child: ModelView[Any], request: Any, field: BaseField, action: RequestAction
        ) -> bool:
            # This view answers for its children, asked about each field by
            # its path from here, such as items.unit_price.
            asked = copy(field)
            asked.name = f"{inline.name}.{field.name}"
            return self._view.can_access_field(request, asked, action)

        namespace: dict[str, Any] = {
            "model": relation.target,
            "name": f"{self._view.name}__{inline.name}",
            "fields": list(entries),
            "exclude_fields_from_detail": filled,
            "exclude_fields_from_create": filled,
            "exclude_fields_from_edit": filled,
            "record_title": inline.record_title,
            "can_access_field": can_access_field,
        }
        child_class = type(f"{relation.target.__name__}Inline", (ModelView,), namespace)
        try:
            built: ModelView[Any] = child_class(self._inspector, self._registry)
        except AdminSiteError as error:
            # The child's class is made here, so a mistake names the setting
            # it was written in rather than a class nobody wrote.
            said = str(error)
            generated = f"{child_class.__name__}."
            if said.startswith(generated):
                said = f".{said.removeprefix(generated)}"
            else:
                said = f": {said}"
            raise AdminSiteError(
                f"{type(self._view).__name__}.{setting}{said}"
            ) from None
        return built
