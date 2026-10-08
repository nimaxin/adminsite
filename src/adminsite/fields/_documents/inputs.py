"""The inputs inside a document: numbers, text, dates, choices and the rest."""

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from adminsite._text import choice_label
from adminsite.exceptions import FieldValidationError
from adminsite.fields._documents.error_messages import _number_text, _outside
from adminsite.fields.base import Field
from adminsite.fields.choices import EnumField
from adminsite.fields.lists import ListField
from adminsite.fields.scalars import (
    BooleanField,
    DecimalField,
    EmailField,
    FloatField,
    IntegerField,
    StringField,
)
from adminsite.fields.temporal import DateField, DateTimeField, TimeField
from adminsite.i18n import gettext as _
from adminsite.i18n import ngettext

__all__ = [
    "DocumentAddress",
    "DocumentChoice",
    "DocumentCode",
    "DocumentDate",
    "DocumentDateTime",
    "DocumentDecimal",
    "DocumentEmail",
    "DocumentInteger",
    "DocumentList",
    "DocumentLongText",
    "DocumentNumber",
    "DocumentSwitch",
    "DocumentText",
    "DocumentTime",
]


class _Described(Field[Any]):
    """A field whose note starts with its property's description."""

    description = ""

    def hint(self) -> str:
        """The description, followed by how to fill the input in."""
        return " ".join(part for part in (self.description, super().hint()) if part)


class _Limited(_Described):
    """A number that has to fall in a range."""

    minimum: Any = None
    maximum: Any = None
    exclusive_minimum: Any = None
    exclusive_maximum: Any = None
    multiple_of: Any = None

    def limit(self, node: Mapping[str, Any]) -> None:
        """Take the range from a schema."""
        self.minimum = node.get("minimum")
        self.maximum = node.get("maximum")
        self.exclusive_minimum = node.get("exclusiveMinimum")
        self.exclusive_maximum = node.get("exclusiveMaximum")
        self.multiple_of = node.get("multipleOf")

    def hint(self) -> str:
        """The description, and the range the number has to fall in."""
        lower, upper = self.minimum, self.maximum
        parts = [self.description]
        if lower is not None and upper is not None:
            parts.append(
                _(
                    "{minimum} to {maximum}.",
                    minimum=_number_text(lower),
                    maximum=_number_text(upper),
                )
            )
        else:
            if lower is not None:
                parts.append(_("{limit} or more.", limit=_number_text(lower)))
            if self.exclusive_minimum is not None:
                parts.append(
                    _("Above {limit}.", limit=_number_text(self.exclusive_minimum))
                )
            if upper is not None:
                parts.append(_("{limit} or less.", limit=_number_text(upper)))
            if self.exclusive_maximum is not None:
                parts.append(
                    _("Below {limit}.", limit=_number_text(self.exclusive_maximum))
                )
        return " ".join(part for part in parts if part)

    def to_python(self, text: str) -> Any:
        """Read the number and check it falls in the range."""
        number = super().to_python(text)
        wrong = _outside(
            number,
            minimum=self.minimum,
            maximum=self.maximum,
            exclusive_minimum=self.exclusive_minimum,
            exclusive_maximum=self.exclusive_maximum,
            multiple_of=self.multiple_of,
        )
        if wrong:
            raise FieldValidationError(self.name, wrong)
        return number


class DocumentInteger(_Limited, IntegerField):
    """A whole number inside a document."""


class DocumentNumber(_Limited, FloatField):
    """A number inside a document."""

    def display(self, value: Any) -> str:
        """The number as a person writes it: 60, not 60.0."""
        return "" if value is None else _number_text(value)

    def serialize(self, value: Any) -> str:
        """The number for the input, 60 rather than 60.0."""
        return "" if value is None else _number_text(value)


class DocumentDecimal(_Limited, DecimalField):
    """An exact number inside a document, as a Pydantic Decimal is."""


class DocumentText(_Described, StringField):
    """Text inside a document, with the length and pattern its schema asks."""

    min_length: int | None = None
    pattern: str | None = None

    def to_python(self, text: str) -> Any:
        """Read the text and check its length and pattern."""
        value = super().to_python(text)
        if self.min_length is not None and len(value) < self.min_length:
            raise FieldValidationError(
                self.name,
                ngettext(
                    "Enter at least {count} character.",
                    "Enter at least {count} characters.",
                    self.min_length,
                ),
            )
        if self.pattern is not None and not re.search(self.pattern, value):
            raise FieldValidationError(
                self.name, _("Enter it in the form this field expects.")
            )
        return value


class DocumentLongText(DocumentText):
    """Longer text inside a document, edited in a box."""

    widget = "textarea"


class DocumentAddress(DocumentText):
    """A web address inside a document."""

    widget = "url"

    def to_python(self, text: str) -> Any:
        """Read the address and check it is a whole one."""
        value = super().to_python(text)
        parts = urlsplit(value)
        if not parts.scheme or not (parts.netloc or parts.path):
            raise FieldValidationError(
                self.name, _("Enter a full address, such as https://example.com.")
            )
        return value


class DocumentEmail(_Described, EmailField):
    """An email address inside a document."""


class DocumentDate(_Described, DateField):
    """A date inside a document, stored as 2026-09-18."""


class DocumentDateTime(_Described, DateTimeField):
    """A date and time inside a document."""


class DocumentTime(_Described, TimeField):
    """A time of day inside a document."""


class DocumentSwitch(_Described, BooleanField):
    """A yes or no inside a document."""


class DocumentList(_Described, ListField):
    """A list of plain values inside a document, one per line."""


class DocumentCode(_Described, Field[Any]):
    """A part of a document the form cannot draw, written as JSON."""

    widget = "json"
    python_type = dict

    def serialize(self, value: Any) -> str:
        """The value laid out as JSON."""
        return json.dumps(value, ensure_ascii=False, indent=2)

    def display(self, value: Any) -> str:
        """The value as JSON on one line."""
        return json.dumps(value, ensure_ascii=False)

    def to_python(self, text: str) -> Any:
        """Read the text as JSON, or say where it stops being JSON."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise FieldValidationError(
                self.name,
                _(
                    "Line {line}, column {column}: this is not valid JSON.",
                    line=error.lineno,
                    column=error.colno,
                ),
            ) from None


def _choice_text(value: Any) -> str:
    """An option as a select sends it."""
    return value if isinstance(value, str) else json.dumps(value)


def _choice_label(value: Any) -> str:
    """An option as a person reads it: card reads Card, and USD stays USD."""
    if isinstance(value, str):
        return choice_label(value)
    return _choice_text(value)


class DocumentChoice(_Described, EnumField):
    """One of a fixed set of values inside a document, or several of them.

    The values may be numbers as well as text, and each is stored as the
    schema lists it.
    """

    def __init__(self, name: str, *, options: Sequence[Any], **settings: Any) -> None:
        self.options = {_choice_text(value): value for value in options}
        super().__init__(
            name,
            choices=[
                (text, _choice_label(value)) for text, value in self.options.items()
            ],
            **settings,
        )

    def to_python(self, text: str) -> Any:
        """Accept a listed option and return it as the schema lists it."""
        return self.options[super().to_python(text)]

    def _stored_value(self, value: Any) -> str:
        return _choice_text(value)


def _python_value(item: Field[Any], value: Any) -> Any:
    """A stored JSON value as the field that shows it expects it.

    A date is stored as text, 2026-09-18, and a date field formats a date.
    """
    wanted: dict[type[Field[Any]], type] = {
        DocumentDate: date,
        DocumentDateTime: datetime,
        DocumentTime: time,
    }
    for kind, python_type in wanted.items():
        if isinstance(item, kind) and isinstance(value, str):
            try:
                return TypeAdapter(python_type).validate_python(value)
            except PydanticValidationError:
                return value
    if isinstance(item, DocumentList) and isinstance(value, list):
        return [_python_value(item.reader, one) for one in value]
    return value
