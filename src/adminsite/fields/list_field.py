from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.fields.scalars import StringField
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema


@dataclass(eq=False, repr=False)
class ListField(Field[Sequence[Any] | None]):
    """A list of values, such as a Postgres ARRAY column: one value per line.

    Chosen for an array column by itself, with each value read by the field
    its type calls for, so `ARRAY(Integer)` takes whole numbers and
    `ARRAY(String(2))` two letters at most. Give `item` to choose it:

    ```python
    ListField(Player.scores, item=IntegerField("scores"))
    ```
    """

    _: KW_ONLY
    # The field that reads each value. Left out, the one the column's type
    # of value calls for, or plain text.
    item: Field[Any] | None = None

    widget = "list"
    python_type = list

    def column_options(self, schema: FieldSchema) -> dict[str, Any]:
        """The column's options, except that a list is never required by it.

        An empty list is a value, so a column that cannot be null still
        takes one.
        """
        options = super().column_options(schema)
        if self.required is None:
            options["required"] = False
        return options

    @property
    def reader(self) -> Field[Any]:
        """The field each value is read and shown by."""
        return self.item if self.item is not None else StringField(self.name)

    def hint(self) -> str:
        """How to fill the input in."""
        return _("One value per line.")

    def display(self, value: Any) -> str:
        """The values on one line, each shown the way its field shows it."""
        if value is None:
            return ""
        return ", ".join(self.reader.display(one) for one in value)

    def serialize(self, value: Any) -> str:
        """The values for the box, one per line."""
        if value is None:
            return ""
        return "\n".join(self.reader.serialize(one) for one in value)

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
                values.append(self.reader.parse(text))
            except FieldValidationError as error:
                raise FieldValidationError(
                    self.name,
                    _("Line {number}: {problem}", number=number, problem=error.message),
                ) from None
        if not values and self.required:
            raise FieldValidationError(self.name, _("This field is required."))
        return values
