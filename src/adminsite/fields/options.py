from typing import Any


class FieldOptions:
    """Changes to the field adminsite worked out for a path.

    Replaced by `Field(Product.name, label="Product name")`, or the kind
    with its options, in `fields`; read until 0.1.0a10 refuses it.

    It takes whatever the field takes, so a link can be given a
    `display_template` without naming its target again.
    """

    def __init__(self, name: str, **changes: Any) -> None:
        self.name = name
        # The name read_only had before the fields became typed.
        if "readonly" in changes:
            changes["read_only"] = changes.pop("readonly")
        self.changes = changes
