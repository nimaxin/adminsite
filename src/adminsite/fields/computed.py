from collections.abc import Callable, Sequence
from typing import Any

from adminsite.fields.base import Field
from adminsite.text import as_text


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
    """

    widget = "text"
    stored = False

    def __init__(
        self,
        name: str,
        getter: Callable[[Any], Any],
        *,
        needs: Sequence[str] = (),
        **options: Any,
    ) -> None:
        options.setdefault("readonly", True)
        super().__init__(name, **options)
        self.getter = getter
        self.needs = tuple(needs)

    def text_for(self, record: Any, value: Any = None) -> str:
        """Work the value out from the record, as text."""
        if record is None:
            return ""
        return self.display(self.getter(record))

    def display(self, value: Any) -> str:
        """An empty value shows as nothing, anything else as text."""
        if value is None:
            return ""
        return as_text(value)
