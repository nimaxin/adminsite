from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from adminsite.exceptions import AdminSiteError
from adminsite.fields.base import Field
from adminsite.fields.tones import NEUTRAL, TONE_NAMES, Tone, tone_number
from adminsite.i18n import gettext as _

__all__ = [
    "TRUE_VALUES",
    "BooleanField",
    "DecimalField",
    "EmailField",
    "FloatField",
    "IntegerField",
    "StringField",
    "TextAreaField",
    "UUIDField",
]

TRUE_VALUES = frozenset({"1", "true", "on", "yes"})


@dataclass(eq=False, repr=False)
class StringField(Field[str | None]):
    """A short piece of text, edited on one line."""

    widget = "text"
    python_type = str
    error_message = "Enter some text."


@dataclass(eq=False, repr=False)
class TextAreaField(StringField):
    """A longer piece of text, edited in a box."""

    widget = "textarea"


@dataclass(eq=False, repr=False)
class EmailField(StringField):
    """Text holding an email address."""

    widget = "email"


@dataclass(eq=False, repr=False)
class IntegerField(Field[int | None]):
    """A whole number."""

    widget = "number"
    python_type = int
    error_message = "Enter a whole number."


@dataclass(eq=False, repr=False)
class FloatField(Field[float | None]):
    """A number that can have a fractional part."""

    widget = "number"
    python_type = float
    error_message = "Enter a number."


@dataclass(eq=False, repr=False)
class DecimalField(Field[Decimal | None]):
    """A number kept exact, such as a price."""

    widget = "number"
    python_type = Decimal
    error_message = "Enter an amount, for example 12.50."

    def display(self, value: Any) -> str:
        """Show two decimal places and group the thousands."""
        if value is None:
            return ""
        return f"{Decimal(value):,.2f}"

    def serialize(self, value: Any) -> str:
        """Show the plain number, so the input can be edited."""
        if value is None:
            return ""
        return f"{Decimal(value):f}"


@dataclass(eq=False, repr=False)
class BooleanField(Field[bool | None]):
    """A yes or no value.

    Its badge is green for yes and grey for no, unless `tones` says: for a
    flag whose yes is the bad case, `{True: "rose", False: None}` draws a
    rose badge when it is set and nothing when it is not.
    """

    _: KW_ONLY
    tones: Tone | Mapping[bool, Tone | None] | None = None

    widget = "checkbox"
    python_type = bool
    error_message = "Choose yes or no."

    def __post_init__(self) -> None:
        super().__post_init__()
        self._tones = self._read_tones(self.tones)

    def tone_of(self, value: Any) -> int | None:
        """Which of the six badge tones a value is drawn in, or None for no badge."""
        return self._tones.get(bool(value), NEUTRAL)

    def _read_tones(
        self, tones: Tone | Mapping[bool, Tone | None] | None
    ) -> dict[bool, int | None]:
        if tones is None:
            return {True: TONE_NAMES.index("green"), False: NEUTRAL}
        if isinstance(tones, str):
            number = tone_number(self.name, tones)
            return {True: number, False: number}
        read: dict[bool, int | None] = {}
        for value, tone in tones.items():
            if not isinstance(value, bool):
                raise AdminSiteError(
                    f"The field {self.name!r} gives a tone to {value!r}. A yes or "
                    "no field takes True and False."
                )
            read[value] = None if tone is None else tone_number(self.name, tone)
        return read

    def display(self, value: Any) -> str:
        """Show Yes or No rather than True or False."""
        if value is None:
            return ""
        return _("Yes") if value else _("No")

    def parse(self, raw: str | None) -> bool:
        """Read a checkbox, where nothing submitted means no."""
        if raw is None:
            return False
        return raw.strip().lower() in TRUE_VALUES

    def serialize(self, value: Any) -> str:
        """Give the input a value it can round trip."""
        return "true" if value else ""


@dataclass(eq=False, repr=False)
class UUIDField(Field[UUID | None]):
    """An identifier in UUID form."""

    widget = "text"
    python_type = UUID
    error_message = "Enter a valid UUID."
