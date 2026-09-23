from collections.abc import Sequence
from enum import Enum
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.text import humanize


class ChoiceField(Field):
    """A value picked from a fixed set, such as a status.

    With `multiple=True` the select holds several options at once, which
    suits a JSON column and an action that asks for a few categories.
    """

    widget = "select"
    python_type = str
    error_message = "Choose one of the listed options."

    def __init__(
        self,
        name: str,
        *,
        choices: Sequence[tuple[str, str]] = (),
        enum_class: type[Enum] | None = None,
        multiple: bool = False,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.enum_class = enum_class
        self.multiple = multiple
        self.choices = tuple(choices) or self._choices_from_enum(enum_class)

    @classmethod
    def from_schema(cls, schema: FieldSchema, **overrides: Any) -> "ChoiceField":
        """Build the field from a column that has a fixed set of values."""
        enum_class = (
            schema.python_type
            if isinstance(schema.python_type, type)
            and issubclass(schema.python_type, Enum)
            else None
        )
        options: dict[str, Any] = {
            "label": schema.label,
            "required": schema.required,
            "readonly": schema.primary_key,
            "enum_class": enum_class,
            "choices": tuple(
                (value, humanize(value)) for value in schema.enum_values or ()
            ),
        }
        options.update(overrides)
        return cls(schema.name, **options)

    def display(self, value: Any) -> str:
        """Show the label of the chosen option, or of each of them."""
        if value is None:
            return ""
        if isinstance(value, list | tuple | set):
            return ", ".join(self.display(one) for one in value)
        stored = self._stored_value(value)
        for option, label in self.choices:
            if option == stored:
                return label
        return humanize(stored)

    def serialize(self, value: Any) -> str:
        """Show the stored value, which is what the select submits."""
        if value is None:
            return ""
        if isinstance(value, list | tuple | set):
            return ", ".join(self._stored_value(one) for one in value)
        return self._stored_value(value)

    def parse_many(self, raw: Sequence[str] | None) -> list[Any]:
        """Read every option chosen in a select that holds several."""
        if not raw:
            if self.required:
                raise FieldValidationError(self.name, _("This field is required."))
            return []
        return [self.to_python(text.strip()) for text in raw if text.strip()]

    def values_of(self, value: Any) -> tuple[str, ...]:
        """The options chosen, as the select writes them."""
        if value is None or value == "":
            return ()
        if isinstance(value, list | tuple | set):
            return tuple(self._stored_value(one) for one in value)
        return (self._stored_value(value),)

    def to_python(self, text: str) -> Any:
        """Accept a listed option and return it as the model stores it."""
        allowed = {option for option, _ in self.choices}
        if allowed and text not in allowed:
            match = self._match_ignoring_case(text, allowed)
            if match is None:
                raise FieldValidationError(self.name, _(self.error_message))
            text = match
        if self.enum_class is None:
            return text
        return self.enum_class[text]

    def _choices_from_enum(
        self, enum_class: type[Enum] | None
    ) -> tuple[tuple[str, str], ...]:
        if enum_class is None:
            return ()
        return tuple((member.name, humanize(member.name)) for member in enum_class)

    def _stored_value(self, value: Any) -> str:
        return value.name if isinstance(value, Enum) else str(value)

    def _match_ignoring_case(self, text: str, allowed: set[str]) -> str | None:
        lowered = text.lower()
        for option in allowed:
            if option.lower() == lowered:
                return option
        return None
