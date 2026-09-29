from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import QueryableAttribute

from adminsite.columns import ColumnReference


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
    fields: Sequence[ColumnReference] = ()
    readonly_fields: Sequence[ColumnReference] = ()
    label: str = ""
    extra: int = 1
    can_delete: bool = True
    display_template: str = ""

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

    @property
    def is_new(self) -> bool:
        """Whether this row adds a child rather than changing one."""
        return not self.key
