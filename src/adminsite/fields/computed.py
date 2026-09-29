from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass
from typing import Any, Generic, TypeVar

from adminsite.columns import ColumnReference
from adminsite.exceptions import AdminSiteError
from adminsite.fields.base import BaseField
from adminsite.text import as_text

__all__ = [
    "LOADED",
    "ComputedField",
    "Loader",
]

M = TypeVar("M")
V = TypeVar("V")

# Where the values a loader worked out wait on each record, for the rest of
# the request. The record's own __dict__, so nothing mapped is touched.
LOADED = "_adminsite_loaded"

# Given the session and the records on a page, a loader answers with each
# record's value by its primary key: a tuple of values for a composite key.
Loader = Callable[[Any, Sequence[M]], Awaitable[Mapping[Any, V]]]


@dataclass(eq=False, repr=False)
class ComputedField(BaseField, Generic[M, V]):
    """A value the view works out from a record, rather than a column.

    ```python
    def capacity(product: Product) -> str:
        return f"{len(product.slots)}/{product.limit}"


    class ProductView(ModelView[Product]):
        fields = [
            Product.name,
            ComputedField("capacity", capacity, needs=[Product.slots]),
        ]
    ```

    It shows in the list, on the record page and in the export, and is
    never written, sorted or filtered. `needs` names what the function
    reads, so it is loaded with the page instead of one query per row. The
    function takes the view's model, so one written for another model is a
    type error.

    A value that takes a query, such as a count of related rows, comes from
    `load` instead: an async function given the session and every record on
    the page, which answers with each record's value by its primary key. It
    runs once for the page, however many rows it holds. A record it leaves
    out gets `default`.

    ```python
    from collections.abc import Sequence


    async def order_counts(
        session: SessionAdapter, customers: Sequence[Customer]
    ) -> dict[int, int]:
        rows = await session.execute(
            select(Order.customer_id, func.count())
            .where(Order.customer_id.in_([customer.id for customer in customers]))
            .group_by(Order.customer_id)
        )
        return {customer_id: count for customer_id, count in rows.all()}


    ComputedField("orders", load=order_counts, default=0)
    ```
    """

    name: str
    getter: Callable[[M], V] | None = None
    _: KW_ONLY
    needs: Sequence[ColumnReference] = ()
    load: Loader[M, V] | None = None
    default: V | None = None
    read_only: bool = True

    widget = "text"
    stored = False

    def __post_init__(self) -> None:
        if (self.getter is None) == (self.load is None):
            raise AdminSiteError(
                f"ComputedField({self.name!r}) takes a function of the record or "
                "load=, one of the two."
            )
        super().__post_init__()

    def value_for(self, record: Any) -> Any:
        """The value for a record: worked out now, or found where load left it."""
        if self.getter is not None:
            return self.getter(record)
        return vars(record).get(LOADED, {}).get(self.name, self.default)

    def text_for(self, record: Any, value: Any = None) -> str:
        """Work the value out from the record, as text, in the field's `format`."""
        if record is None:
            return ""
        return super().text_for(record, self.value_for(record))

    def display(self, value: Any) -> str:
        """An empty value shows as nothing, anything else as text."""
        if value is None:
            return ""
        return as_text(value)
