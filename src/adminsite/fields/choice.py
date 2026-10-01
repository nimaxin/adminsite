from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass, field
from enum import Enum
from typing import Any, Self

from adminsite.exceptions import AdminSiteError, FieldValidationError, renamed_keywords
from adminsite.fields.base import Field
from adminsite.fields.tones import NEUTRAL, Tones, tone_number
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.text import humanize

__all__ = [
    "TONES",
    "EnumField",
]

# How many badge tones the stylesheet has; choices past the sixth start over.
TONES = 6


@dataclass(eq=False, repr=False)
class EnumField(Field[Any]):
    """A value picked from a fixed set, such as a status.

    The set comes from the column, an Enum or a string column with its
    values listed, or from `enum` or `choices` given here:

    ```python
    EnumField(
        Order.status, tones={OrderStatus.PAID: "green", OrderStatus.FAILED: "rose"}
    )
    EnumField(Product.size, choices=[("S", "Small"), ("L", "Large")])
    ```

    With `multiple=True` it holds several options at once, in the order they
    were picked, which suits a JSON column and an action that asks for a few
    categories.

    On an Enum column, `choices` relabel or narrow its members, each named
    by its name or its value, and the field still hands over the member.
    `enum=` on a string column keys the options by each member's value,
    which is what the column stores.

    `tones` says which colour each value's badge is, by name, so a failed
    status is rose wherever it sits among the choices. A value left out is
    grey, and None draws it with no badge. One name, such as "grey", colours
    every value alike. Without it, a value takes the tone of its place.
    """

    _: KW_ONLY
    enum: type[Enum] | None = None
    choices: Sequence[tuple[str, str]] = ()
    multiple: bool = False
    tones: Tones | None = None

    # Whether a value is the member of `enum`, as an Enum column and an
    # action hold it, rather than the member's value, as a string column does.
    _holds_members: bool = field(init=False, default=True)

    widget = "select"
    python_type = str
    error_message = "Choose one of the listed options."
    unused_options = frozenset({"max_length"})

    def __post_init__(self) -> None:
        super().__post_init__()
        self.choices = tuple(self.choices) or self._choices_from_enum(self.enum)
        self._tones = self._read_tones(self.tones)

    def filled_from(self, schema: FieldSchema) -> Self:
        """A copy with what the column says, holding values as the column does."""
        filled = super().filled_from(schema)
        filled._holds_members = _is_enum(schema.python_type)
        return filled

    def column_options(self, schema: FieldSchema) -> dict[str, Any]:
        """The column's enum and set of values as well, for those left out.

        An Enum column keeps its enum where choices are given, so they only
        relabel or narrow its members. A string column stores a member's
        value, so the options an `enum` gives it are keyed by the value.
        """
        options = super().column_options(schema)
        stores_members = _is_enum(schema.python_type)
        if self.enum is None:
            if stores_members:
                options["enum"] = schema.python_type
            if not self.choices:
                options["choices"] = tuple(
                    (value, humanize(value)) for value in schema.enum_values or ()
                )
        # Only the options the enum gave are keyed again; written ones stay.
        elif not stores_members and self.choices == self._choices_from_enum(self.enum):
            options["choices"] = tuple(
                (str(member.value), humanize(member.name)) for member in self.enum
            )
        return options

    def display(self, value: Any) -> str:
        """Show the label of the chosen option, or of each of them."""
        if value is None:
            return ""
        if isinstance(value, list | tuple | set):
            return ", ".join(self.display(one) for one in value)
        chosen = self._choice_for(value)
        return chosen[1] if chosen else humanize(self._stored_value(value))

    def serialize(self, value: Any) -> str:
        """Show the option chosen, which is what the select submits."""
        if value is None:
            return ""
        if isinstance(value, list | tuple | set):
            return ", ".join(self._option_for(one) for one in value)
        return self._option_for(value)

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

    def check_options(self) -> None:
        """Refuse a field with nothing to choose from, or a tone for a value it lacks.

        Checked once the column has filled in its choices, since an Enum
        column brings its own. With an enum, each choice names a member.
        """
        super().check_options()
        if not self.choices:
            raise AdminSiteError(
                f"{self!r} has nothing to choose from. Give it "
                "choices=[(value, label), ...] or enum=, or use it on a column "
                "whose type is an Enum."
            )
        if self.enum is not None:
            for option, _label in self.choices:
                if self._member(option) is None:
                    members = ", ".join(member.name for member in self.enum)
                    raise AdminSiteError(
                        f"{self!r}: its choices name {_written(option)}, which is "
                        f"not a member of {self.enum.__name__}. Name a member by "
                        f"its name or its value: {members}."
                    )
        if self.tones is None or isinstance(self.tones, str):
            return
        known = {option.lower() for option, _label in self.choices}
        for value in self.tones:
            if not self._spellings(value) & known:
                listed = ", ".join(option for option, _label in self.choices)
                raise AdminSiteError(
                    f"{self!r}: its tones give a colour to {_written(value)}, "
                    f"which is not one of its choices: {listed}."
                )

    def _read_tones(self, tones: Tones | None) -> int | dict[str, int | None] | None:
        """The tones worked out once: one for every value, one each, or none."""
        if tones is None:
            return None
        if isinstance(tones, str):
            return tone_number(self.name, tones)
        read: dict[str, int | None] = {}
        for value, tone in tones.items():
            number = None if tone is None else tone_number(self.name, tone)
            for spelling in self._spellings(value):
                read[spelling] = number
        return read

    def _spellings(self, value: Any) -> set[str]:
        """The ways an option can be written for a value, in lower case.

        A member counts by its name or its value, whether the record holds
        the member, as an Enum column does, or its value, as a string column
        given `enum` does.
        """
        wanted = {self._stored_value(value).lower()}
        member = value if isinstance(value, Enum) else self._member(value)
        if member is not None:
            wanted |= {member.name.lower(), str(member.value).lower()}
        return wanted

    def values_of(self, value: Any) -> tuple[str, ...]:
        """The options chosen, as the select writes them."""
        if value is None or value == "":
            return ()
        if isinstance(value, list | tuple | set):
            return tuple(self._option_for(one) for one in value)
        return (self._option_for(value),)

    def to_python(self, text: str) -> Any:
        """Accept a listed option and return it as the model stores it.

        An option may be written in any case, and a member by its name or
        its value. With an enum, the field returns the member, or the
        member's value for a column that stores values, such as a string
        column.
        """
        if self.choices:
            chosen = self._choice_for(text)
            if chosen is None:
                raise FieldValidationError(self.name, _(self.error_message))
            text = chosen[0]
        member = self._member(text)
        if member is None:
            return text
        return member if self._holds_members else member.value

    def _choice_for(self, value: Any) -> tuple[str, str] | None:
        """The option and label chosen for one value, or None where none is.

        An option written exactly as the value wins over one that is only
        another spelling of it.
        """
        stored = self._stored_value(value)
        for choice in self.choices:
            if choice[0] == stored:
                return choice
        wanted = self._spellings(value)
        for choice in self.choices:
            if choice[0].lower() in wanted:
                return choice
        return None

    def _option_for(self, value: Any) -> str:
        """The option one value is chosen by, as the select writes it."""
        chosen = self._choice_for(value)
        return chosen[0] if chosen else self._stored_value(value)

    def _member(self, written: Any) -> Enum | None:
        """The member of `enum` an option or a value names, by name or by value."""
        if self.enum is None:
            return None
        text = str(written)
        for member in self.enum:
            if text in (member.name, str(member.value)):
                return member
        lowered = text.lower()
        for member in self.enum:
            if lowered in (member.name.lower(), str(member.value).lower()):
                return member
        return None

    def _choices_from_enum(
        self, enum: type[Enum] | None
    ) -> tuple[tuple[str, str], ...]:
        if enum is None:
            return ()
        return tuple((member.name, humanize(member.name)) for member in enum)

    def _stored_value(self, value: Any) -> str:
        return value.name if isinstance(value, Enum) else str(value)


def _is_enum(python_type: Any) -> bool:
    """Whether a column's type of value is an Enum, whose members it holds."""
    return isinstance(python_type, type) and issubclass(python_type, Enum)


renamed_keywords(EnumField, {"enum_class": "enum"})


def _written(value: Any) -> str:
    """A value as code writes it, for a message: "US" or Region.US."""
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)
