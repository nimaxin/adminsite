"""Child records edited inside their parent's form, such as order lines."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import QueryableAttribute

if TYPE_CHECKING:
    from adminsite.columns import ColumnReference
    from adminsite.fields import Field

__all__ = [
    "Inline",
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

    Each child shows as a row of inputs, with a button to remove it, and
    new rows can be added in the form. Everything is saved in one
    transaction with the parent.

    Args:
        relation: The relationship on the parent that holds the children,
            by attribute or by name, such as `Order.items`.
        fields: The child's fields, in order, written as a view's are, so
            `Field(OrderItem.added_at, read_only=True)` shows a value it
            never edits. Left empty, every field but those the relationship
            fills in.
        label: The heading above the rows. Left empty, the relationship's
            name.
        blank_rows: How many blank rows the table starts with while it has
            no rows yet.
        can_delete: Whether rows can be removed, which deletes the child.
        record_title: How a child is named, as a view's `record_title`.
    """

    relation: str | QueryableAttribute[Any]
    fields: Sequence["ColumnReference | Field[Any]"] = ()
    label: str = ""
    blank_rows: int = 1
    can_delete: bool = True
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
