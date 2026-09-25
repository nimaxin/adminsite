from collections.abc import Sequence
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.fields.scalars import StringField
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema


class ListField(Field):
    """A list of values, such as a Postgres ARRAY column: one value per line.

    Chosen for an array column by itself, with each value read by the field
    its type calls for, so `ARRAY(Integer)` takes whole numbers and
    `ARRAY(String(2))` two letters at most. Give `item` to choose it:

    ```python
    ListField("scores", item=IntegerField("scores"))
    ```
    """

    widget = "list"
    python_type = list

    def __init__(self, name: str, *, item: Field | None = None, **options: Any) -> None:
        super().__init__(name, **options)
        self.item = item if item is not None else StringField(name)

    @classmethod
    def from_schema(cls, schema: FieldSchema, **overrides: Any) -> "Field":
        """Build the field from an array column.

        It is never required by the column alone: an empty list is a value,
        so a column that cannot be null still takes one.
        """
        overrides.setdefault("required", False)
        return super().from_schema(schema, **overrides)

    def hint(self) -> str:
        """How to fill the input in."""
        return _("One value per line.")

    def display(self, value: Any) -> str:
        """The values on one line, each shown the way its field shows it."""
        if value is None:
            return ""
        return ", ".join(self.item.display(one) for one in value)

    def serialize(self, value: Any) -> str:
        """The values for the box, one per line."""
        if value is None:
            return ""
        return "\n".join(self.item.serialize(one) for one in value)

    def parse(self, raw: str | None) -> Any:
        """Read one value from each line. Empty lines are skipped."""
        return self.parse_values((raw or "").splitlines())

    def parse_values(self, texts: Sequence[str]) -> list[Any]:
        """Read each text as one value, naming the line of any that fails."""
        values = []
        for number, text in enumerate(texts, start=1):
            if not text.strip():
                continue
            try:
                values.append(self.item.parse(text))
            except FieldValidationError as error:
                raise FieldValidationError(
                    self.name,
                    _("Line {number}: {problem}", number=number, problem=error.message),
                ) from None
        if not values and self.required:
            raise FieldValidationError(self.name, _("This field is required."))
        return values
