from collections.abc import Sequence
from enum import Enum
from typing import Any

from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields.base import Field
from adminsite.fields.tones import NEUTRAL, Tones, tone_number
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.text import humanize

# How many badge tones the stylesheet has; choices past the sixth start over.
TONES = 6


class ChoiceField(Field):
    """A value picked from a fixed set, such as a status.

    With `multiple=True` it holds several options at once, in the order they
    were picked, which suits a JSON column and an action that asks for a few
    categories.

    `tones` says which colour each value's badge is, by name, so a failed
    status is rose wherever it sits among the choices. A value left out is
    grey, and None draws it with no badge. One name, such as "grey", colours
    every value alike. Without it, a value takes the tone of its place.
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
        tones: Tones | None = None,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.enum_class = enum_class
        self.multiple = multiple
        self.choices = tuple(choices) or self._choices_from_enum(enum_class)
        self.tones = tones
        self._tones = self._read_tones(tones)

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

    def tone_of(self, value: Any) -> int | None:
        """Which of the six badge tones a value is drawn in, or None for no badge.

        The field's `tones` decide, where it has them. Otherwise the tone
        follows the value's place among the choices, so a status keeps its
        colour on every page and every list.
        """
        if isinstance(self._tones, int):
            return self._tones
        if value is None or isinstance(value, list | tuple | set):
            return NEUTRAL
        wanted = self._spellings(value)
        if self._tones is not None:
            for spelling in wanted:
                if spelling in self._tones:
                    return self._tones[spelling]
            return NEUTRAL
        for index, (option, _label) in enumerate(self.choices):
            if option.lower() in wanted:
                return index % TONES
        return NEUTRAL

    def _read_tones(self, tones: Tones | None) -> int | dict[str, int | None] | None:
        """The tones worked out once: one for every value, one each, or none."""
        if tones is None:
            return None
        if isinstance(tones, str):
            return tone_number(self.name, tones)
        known = {option.lower() for option, _label in self.choices}
        read: dict[str, int | None] = {}
        for value, tone in tones.items():
            spellings = self._spellings(value)
            if known and not spellings & known:
                listed = ", ".join(option for option, _label in self.choices)
                raise AdminSiteError(
                    f"The field {self.name!r} gives a tone to {value!r}, which is "
                    f"not one of its choices: {listed}."
                )
            number = None if tone is None else tone_number(self.name, tone)
            for spelling in spellings:
                read[spelling] = number
        return read

    def _spellings(self, value: Any) -> set[str]:
        """The ways an option can be written for a value, in lower case.

        An enum column may list its members by value while the record holds
        the member, so either spelling of it counts.
        """
        wanted = {self._stored_value(value).lower()}
        if isinstance(value, Enum):
            wanted.add(str(value.value).lower())
        return wanted

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
