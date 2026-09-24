from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from adminsite.exceptions import AdminSiteError
from adminsite.fields.base import Field
from adminsite.text import as_text

# Where the values a loader worked out wait on each record, for the rest of
# the request. The record's own __dict__, so nothing mapped is touched.
LOADED = "_adminsite_loaded"

# Given the session and the records on a page, a loader answers with each
# record's value by its primary key: a tuple of values for a composite key.
Loader = Callable[[Any, Sequence[Any]], Awaitable[Mapping[Any, Any]]]


class Computed(Field):
    """A value the view works out from a record, rather than a column.

    ```python
    class ProductView(ModelView, model=Product):
        list_display = ("name", "capacity")
        fields = (
            Computed(
                "capacity",
                lambda product: f"{len(product.slots)}/{product.limit}",
                label="Capacity",
                needs=("slots",),
            ),
        )
    ```

    It shows in the list, on the record page and in the export, and is
    never written, sorted or filtered. `needs` names the paths the function
    reads, so they are loaded with the page instead of one query per row.

    A value that takes a query, such as a count of related rows, comes from
    `load` instead: an async function given the session and every record on
    the page, which answers with each record's value by its primary key. It
    runs once for the page, however many rows it holds. A record it leaves
    out gets `default`.

    ```python
    async def order_counts(session, customers):
        rows = await session.execute(
            select(Order.customer_id, func.count())
            .where(Order.customer_id.in_([customer.id for customer in customers]))
            .group_by(Order.customer_id)
        )
        return dict(rows.all())


    Computed("orders", load=order_counts, default=0)
    ```
    """

    widget = "text"
    stored = False

    def __init__(
        self,
        name: str,
        getter: Callable[[Any], Any] | None = None,
        *,
        needs: Sequence[str] = (),
        load: Loader | None = None,
        **options: Any,
    ) -> None:
        if (getter is None) == (load is None):
            raise AdminSiteError(
                f"Computed({name!r}) takes a function of the record or load=, "
                "one of the two."
            )
        options.setdefault("readonly", True)
        super().__init__(name, **options)
        self.getter = getter
        self.needs = tuple(needs)
        self.load = load

    def value_for(self, record: Any) -> Any:
        """The value for a record: worked out now, or found where load left it."""
        if self.getter is not None:
            return self.getter(record)
        return vars(record).get(LOADED, {}).get(self.name, self.default)

    def text_for(self, record: Any, value: Any = None) -> str:
        """Work the value out from the record, as text."""
        if record is None:
            return ""
        return self.display(self.value_for(record))

    def display(self, value: Any) -> str:
        """An empty value shows as nothing, anything else as text."""
        if value is None:
            return ""
        return as_text(value)
