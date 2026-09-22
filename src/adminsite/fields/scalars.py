from decimal import Decimal
from typing import Any
from uuid import UUID

from adminsite.fields.base import Field
from adminsite.i18n import gettext as _

TRUE_VALUES = frozenset({"1", "true", "on", "yes"})


class StringField(Field):
    """A short piece of text, edited on one line."""

    widget = "text"
    python_type = str
    error_message = "Enter some text."


class TextField(StringField):
    """A longer piece of text, edited in a box."""

    widget = "textarea"


class EmailField(StringField):
    """Text holding an email address."""

    widget = "email"


class IntegerField(Field):
    """A whole number."""

    widget = "number"
    python_type = int
    error_message = "Enter a whole number."


class FloatField(Field):
    """A number that can have a fractional part."""

    widget = "number"
    python_type = float
    error_message = "Enter a number."


class DecimalField(Field):
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


class BooleanField(Field):
    """A yes or no value."""

    widget = "checkbox"
    python_type = bool
    error_message = "Choose yes or no."

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


class UUIDField(Field):
    """An identifier in UUID form."""

    widget = "text"
    python_type = UUID
    error_message = "Enter a valid UUID."
