from typing import Any


class FieldOptions:
    """Changes to the field adminsite worked out for a path.

    Put it in a view's `fields` where only a label, a line of help or a
    length changes and the field itself is already right:

    ```python
    class ProductView(ModelView, model=Product):
        fields = (
            FieldOptions("name", label="Product name"),
            FieldOptions("description", help_text="Shown on the shop page."),
        )
    ```

    It takes whatever the field takes, so a link can be given a
    `display_template` without naming its target again.
    """

    def __init__(self, name: str, **changes: Any) -> None:
        self.name = name
        self.changes = changes
