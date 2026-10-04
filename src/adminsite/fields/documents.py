"""JSON documents edited as forms, drawn from a schema.

A schema is a Pydantic type, such as a model or a TypedDict, or a JSON Schema
written as a dict. `Document` reads it into shapes:

- a single value, drawn and read by one of the ordinary fields;
- a group of properties, each under its title;
- rows of objects, added and removed like the rows of a table;
- pairs, a map from keys to values, its keys chosen from a fixed set where
  the schema has one;
- a fixed value, which the form never asks for.

Whatever no shape covers, such as a choice between objects of different
shapes, is edited as JSON in a code box.

Every part of a document has an input name of its own under the field's:
`settings.free_shipping_over`, `settings.channels.0.id`.
"""

import copy
import dataclasses
import json
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, TypeAdapter, create_model
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import ErrorDetails, to_jsonable_python

from adminsite._text import choice_label, humanize_class
from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields.base import Field
from adminsite.fields.choice import EnumField
from adminsite.fields.list_field import ListField
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

__all__ = [
    "DRAWN",
    "MISSING",
    "SET",
    "Document",
    "DocumentAddress",
    "DocumentChoice",
    "DocumentCode",
    "DocumentDate",
    "DocumentDateTime",
    "DocumentDecimal",
    "DocumentEmail",
    "DocumentError",
    "DocumentInteger",
    "DocumentList",
    "DocumentLongText",
    "DocumentNumber",
    "DocumentSwitch",
    "DocumentText",
    "DocumentTime",
    "Fixed",
    "Group",
    "Pairs",
    "Property",
    "Rows",
    "Shape",
    "Shown",
    "Value",
    "is_schema_function",
    "message_for",
    "row_numbers",
    "segment",
]

# Added to a property's input name to mark it set, where each property of a
# document may be left unset.
SET = "~set"

# Added to the field's name by a document form, so the submitted form is read
# input by input rather than as the text of a code box.
DRAWN = "~form"

# The characters a key keeps in an input name; any other is written as %XX,
# so a key holding a dot never reads as two.
_PLAIN = frozenset(string.ascii_letters + string.digits + "_-")


class _Missing:
    """No value at all, as distinct from null."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


def segment(key: str) -> str:
    """A key as it is written in an input name."""
    return "".join(
        letter
        if letter in _PLAIN
        else "".join(f"%{byte:02X}" for byte in letter.encode())
        for letter in key
    )


# The shapes a schema is read into.


class Shape:
    """One part of a document, as a schema describes it."""


@dataclass
class Value(Shape):
    """A single value, drawn and read by an ordinary field."""

    field: Field[Any]


@dataclass
class Fixed(Shape):
    """A value the schema allows only one of, so the form never asks."""

    value: Any


@dataclass
class Property:
    """One named part of an object."""

    key: str
    shape: Shape
    label: str
    description: str = ""
    required: bool = False
    nullable: bool = False
    default: Any = MISSING


@dataclass
class Group(Shape):
    """An object with named properties, each drawn under its title."""

    properties: list[Property]
    # Whether the schema refuses keys it does not name.
    closed: bool = False

    def find(self, key: str) -> Property | None:
        """The property with this key, if the object has one."""
        for found in self.properties:
            if found.key == key:
                return found
        return None


@dataclass
class Rows(Shape):
    """A list of objects, edited as rows that can be added and removed."""

    item: Group


@dataclass
class Pairs(Shape):
    """An object used as a map, edited as rows of a key and a value."""

    key: Field[Any]
    value: Field[Any]
    # The keys the map may hold, where the schema fixes them.
    keys: tuple[Any, ...] = ()


# The fields that draw single values inside a document. Each is one of the
# ordinary fields, with the checks a schema can ask for, and the property's
# description as the note under it.


def _number_text(number: Any) -> str:
    """A number as a person writes it: 5, not 5.0."""
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


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


def _outside(
    number: Any,
    *,
    minimum: Any = None,
    maximum: Any = None,
    exclusive_minimum: Any = None,
    exclusive_maximum: Any = None,
    multiple_of: Any = None,
) -> str:
    """What is wrong with a number, or nothing when it falls in the range."""
    if minimum is not None and number < minimum:
        return _above_or_at(minimum)
    if maximum is not None and number > maximum:
        return _below_or_at(maximum)
    if exclusive_minimum is not None and number <= exclusive_minimum:
        return _above(exclusive_minimum)
    if exclusive_maximum is not None and number >= exclusive_maximum:
        return _below(exclusive_maximum)
    if multiple_of and Decimal(str(number)) % Decimal(str(multiple_of)):
        return _("Enter a multiple of {step}.", step=_number_text(multiple_of))
    return ""


def _above_or_at(limit: Any) -> str:
    return _("Enter {limit} or more.", limit=_number_text(limit))


def _below_or_at(limit: Any) -> str:
    return _("Enter {limit} or less.", limit=_number_text(limit))


def _above(limit: Any) -> str:
    return _("Enter a number above {limit}.", limit=_number_text(limit))


def _below(limit: Any) -> str:
    return _("Enter a number below {limit}.", limit=_number_text(limit))


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
                _("Enter at least {count} characters.", count=self.min_length),
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


# Reading a JSON Schema into shapes.


def _label_for(key: str, node: Mapping[str, Any]) -> str:
    """A property's label: its own title, or its name as adminsite writes it.

    Pydantic titles every property, Free Shipping Over, while adminsite
    writes labels as sentences, Free shipping over; a title that only
    repeats the name is written that way instead.
    """
    title = node.get("title")
    if not isinstance(title, str) or not title:
        return humanize_class(key)
    if title.lower() == key.replace("_", " ").lower():
        return humanize_class(key)
    return title


def _is_plain(shape: Shape) -> bool:
    """Whether a shape fits in one cell of a table."""
    if isinstance(shape, Fixed):
        return True
    return isinstance(shape, Value) and not isinstance(shape.field, DocumentCode)


class _SchemaReader:
    """Reads one JSON Schema into shapes, following its references."""

    def __init__(self, root: Mapping[str, Any]) -> None:
        self.root = root

    def pointed(self, reference: str) -> Mapping[str, Any] | None:
        """The part of the schema a reference such as #/$defs/Channel points at."""
        if not reference.startswith("#"):
            return None
        found: Any = self.root
        for part in reference[1:].split("/"):
            if not part:
                continue
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(found, Mapping) or part not in found:
                return None
            found = found[part]
        return found if isinstance(found, Mapping) else None

    def resolve(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> tuple[Mapping[str, Any] | None, bool, frozenset[str]]:
        """Follow references, and take null out of a choice.

        Returns the node, whether it may be null, and the references walked
        through, so a model that holds itself is not read for ever. A choice
        between shapes comes back marked, to be written as JSON.
        """
        nullable = False
        while True:
            rest = dict(node)
            if "$ref" in rest:
                reference = str(rest.pop("$ref"))
                target = self.pointed(reference)
                if reference in seen or target is None:
                    return None, nullable, seen
                seen = seen | {reference}
                node = {**target, **rest}
                continue
            if isinstance(rest.get("allOf"), list) and len(rest["allOf"]) == 1:
                only = rest.pop("allOf")[0]
                node = {**only, **rest}
                continue
            word = next(
                (one for one in ("anyOf", "oneOf") if isinstance(rest.get(one), list)),
                None,
            )
            if word is not None:
                options = rest.pop(word)
                real = [one for one in options if one.get("type") != "null"]
                nullable = nullable or len(real) < len(options)
                if len(real) == 1:
                    node = {**real[0], **rest}
                    continue
                if {one.get("type") for one in real} == {"number", "string"}:
                    # Pydantic's Decimal: a number, or text holding one.
                    return {**rest, "type": "number", "decimal": True}, nullable, seen
                return {**rest, "choice": real}, nullable, seen
            kind = rest.get("type")
            if isinstance(kind, list):
                kinds = [one for one in kind if one != "null"]
                nullable = nullable or len(kinds) < len(kind)
                rest["type"] = kinds[0] if len(kinds) == 1 else None
            return rest, nullable, seen

    def shape(
        self,
        node: Mapping[str, Any],
        *,
        key: str,
        label: str,
        description: str,
        required: bool,
        seen: frozenset[str] = frozenset(),
    ) -> tuple[Shape, bool]:
        """The shape of one part of a document, and whether it may be null."""
        resolved, nullable, seen = self.resolve(node, seen)
        settings: dict[str, Any] = {
            "label": label,
            "required": required and not nullable,
        }
        code = Value(_describe(DocumentCode(key, **settings), description))
        if resolved is None or "choice" in resolved:
            return code, nullable
        if "const" in resolved:
            return Fixed(resolved["const"]), nullable
        kind = resolved.get("type")
        found: Shape | None
        if kind == "object" or "properties" in resolved:
            found = self.object_shape(resolved, seen)
        elif kind == "array":
            found = self.array_shape(resolved, key, label, description, seen)
        else:
            single = self.value_field(resolved, key, settings)
            found = None if single is None else Value(_describe(single, description))
        return found or code, nullable

    def object_shape(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> Shape | None:
        """A group of named properties, or pairs of keys and values."""
        properties = node.get("properties")
        extra = node.get("additionalProperties")
        if isinstance(properties, Mapping) and properties:
            return Group(self.properties(node, seen), closed=extra is False)
        if not isinstance(extra, Mapping):
            return None
        resolved, _nullable, _walked = self.resolve(extra, seen)
        if resolved is None or "choice" in resolved:
            return None
        value = self.value_field(
            resolved, "value", {"label": "Value", "required": True}
        )
        if value is None:
            return None
        keys: tuple[Any, ...] = ()
        names = node.get("propertyNames")
        if isinstance(names, Mapping):
            named, _nullable, _walked = self.resolve(names, seen)
            if named is not None and isinstance(named.get("enum"), list):
                keys = tuple(named["enum"])
        key_field: Field[Any] = (
            DocumentChoice("key", options=keys, label="Key", required=True)
            if keys
            else DocumentText("key", label="Key", required=True)
        )
        return Pairs(key_field, value, keys)

    def properties(
        self, node: Mapping[str, Any], seen: frozenset[str]
    ) -> list[Property]:
        """Each property of an object, in the order the schema lists them."""
        required = set(node.get("required") or ())
        found = []
        for key, child in node["properties"].items():
            if not isinstance(child, Mapping):
                continue
            label = _label_for(key, child)
            description = str(child.get("description") or "")
            shape, nullable = self.shape(
                child,
                key=key,
                label=label,
                description=description,
                required=key in required,
                seen=seen,
            )
            found.append(
                Property(
                    key=key,
                    shape=shape,
                    label=label,
                    description=description,
                    required=key in required,
                    nullable=nullable,
                    default=child.get("default", MISSING),
                )
            )
        return found

    def array_shape(
        self,
        node: Mapping[str, Any],
        key: str,
        label: str,
        description: str,
        seen: frozenset[str],
    ) -> Shape | None:
        """Options to pick several of, rows of objects, or lines of values."""
        items = node.get("items")
        if "prefixItems" in node or not isinstance(items, Mapping):
            return None
        item, _nullable, walked = self.resolve(items, seen)
        if item is None or "choice" in item:
            return None
        if isinstance(item.get("enum"), list):
            choice = DocumentChoice(
                key, options=item["enum"], multiple=True, label=label
            )
            return Value(_describe(choice, description))
        if item.get("type") == "object" or "properties" in item:
            group = self.object_shape(item, walked)
            if not isinstance(group, Group):
                return None
            # A row is one line of a table, so each of its parts is one value.
            if not all(_is_plain(one.shape) for one in group.properties):
                return None
            return Rows(group)
        one = self.value_field(item, key, {"label": label})
        if one is None:
            return None
        return Value(_describe(DocumentList(key, item=one, label=label), description))

    def value_field(
        self, node: Mapping[str, Any], key: str, settings: dict[str, Any]
    ) -> Field[Any] | None:
        """The ordinary field that draws one plain value, if one fits."""
        if isinstance(node.get("enum"), list):
            return DocumentChoice(key, options=node["enum"], **settings)
        kind = node.get("type")
        if kind == "boolean":
            return DocumentSwitch(key, **{**settings, "required": False})
        if kind in ("integer", "number"):
            number: DocumentInteger | DocumentNumber | DocumentDecimal
            if kind == "integer":
                number = DocumentInteger(key, **settings)
            elif node.get("decimal"):
                number = DocumentDecimal(key, **settings)
            else:
                number = DocumentNumber(key, **settings)
            number.limit(node)
            return number
        if kind != "string":
            return None
        form = node.get("format")
        if form == "email":
            return DocumentEmail(key, **settings)
        if form == "date":
            return DocumentDate(key, **settings)
        if form == "date-time":
            return DocumentDateTime(key, **settings)
        if form == "time":
            return DocumentTime(key, **settings)
        text: DocumentText
        if form in ("uri", "url"):
            text = DocumentAddress(key, **settings)
        elif form in ("textarea", "multiline"):
            text = DocumentLongText(key, **settings)
        else:
            text = DocumentText(key, **settings)
        text.max_length = node.get("maxLength")
        text.min_length = node.get("minLength")
        text.pattern = node.get("pattern")
        return text


def _describe(item: Field[Any], description: str) -> Field[Any]:
    """Give a field its property's description, as the note under it."""
    if isinstance(item, _Described):
        item.description = description
    return item


# Reading a submitted form back into a document.


def _as_text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list | tuple):
        return str(raw[-1]) if raw else None
    return str(raw)


def _as_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list | tuple):
        return [str(one) for one in raw]
    return [str(raw)]


def _blank(raw: Any) -> bool:
    return all(not one.strip() for one in _as_list(raw))


class DocumentError(FieldValidationError):
    """A document that cannot be saved, with a message for each part.

    `errors` holds the messages by input name, `settings.free_shipping_over`,
    so each is shown beside its own input; `places` says where each input
    sits, Free shipping over, for a message about the whole document.
    """

    def __init__(
        self,
        field_name: str,
        errors: Mapping[str, str],
        places: Mapping[str, str] | None = None,
    ) -> None:
        self.errors = dict(errors)
        self.places = dict(places or {})
        first = next(iter(self.errors.values()), "")
        super().__init__(field_name, first)

    def summary(self) -> str:
        """Every problem in one message, each after the place it is in."""
        return " ".join(
            _("{place}: {problem}", place=self.places[name], problem=message)
            if self.places.get(name)
            else message
            for name, message in self.errors.items()
        )


@dataclass
class _Reading:
    """What reading a form turned up, besides the document itself."""

    errors: dict[str, str] = dataclasses.field(default_factory=dict)
    # For each list of rows, the input name of each item, in order, so a
    # problem Pydantic finds at a position lands on the right row.
    rows: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    # For each map, the input name of the row each key came from.
    keys: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)


def _read_value(item: Field[Any], raw: Any, name: str, reading: _Reading) -> Any:
    """Read one input: its value, None when empty, or MISSING when wrong."""
    try:
        if isinstance(item, EnumField) and item.multiple:
            return item.parse_many(_as_list(raw))
        return item.parse(_as_text(raw))
    except FieldValidationError as error:
        reading.errors[name] = error.message
        return MISSING


def row_numbers(data: Mapping[str, Any], name: str) -> list[int]:
    """The rows sent for a list or a map, by the marker each row carries."""
    pattern = re.compile(re.escape(name) + r"\.(\d+)")
    found = set()
    for key in data:
        matched = pattern.fullmatch(key)
        if matched:
            found.add(int(matched.group(1)))
    return sorted(found)


class Document:
    """A schema, read once, that fills, reads and checks documents."""

    def __init__(self, schema: Any, *, name: str = "", partial: bool = False) -> None:
        self.schema = schema
        self.partial = partial
        self.adapter: TypeAdapter[Any] | None = None
        if isinstance(schema, Mapping):
            self.json_schema: Mapping[str, Any] = schema
        else:
            try:
                self.adapter = TypeAdapter(schema)
                self.json_schema = self.adapter.json_schema()
            except Exception as error:
                raise AdminSiteError(
                    f"The field {name!r} has a schema adminsite cannot read, "
                    f"{schema!r}: {error}"
                ) from error
        self.shape, _nullable = _SchemaReader(self.json_schema).shape(
            self.json_schema, key=name, label="", description="", required=True
        )
        if partial and not isinstance(self.shape, Group):
            raise AdminSiteError(
                f"The field {name!r} is partial, which needs a schema for an "
                "object with named properties."
            )
        self._partial_adapter: TypeAdapter[Any] | None = None

    @property
    def drawn(self) -> bool:
        """Whether the form is more than a code box."""
        shape = self.shape
        return not (isinstance(shape, Value) and isinstance(shape.field, DocumentCode))

    def fits(self, value: Any) -> bool:
        """Whether a stored value can be put in the form without losing any of it."""
        if value is None:
            return True
        if isinstance(self.shape, Group | Pairs):
            return isinstance(value, dict)
        if isinstance(self.shape, Rows):
            return isinstance(value, list) and all(
                isinstance(one, dict) for one in value
            )
        return True

    # From a document to the form's inputs.

    def form_values(self, value: Any, name: str) -> dict[str, Any]:
        """The document as the form sends it, input name by input name.

        Filling the form from these, rather than from the document, means a
        form shown again after a failed save is drawn by the same code as one
        opened from the record. A property the document lacks starts at its
        default.
        """
        values: dict[str, Any] = {}
        self._flatten(
            self.shape, MISSING if value is None else value, name, values, True
        )
        return values

    def _flatten(
        self,
        shape: Shape,
        value: Any,
        name: str,
        values: dict[str, Any],
        top: bool = False,
    ) -> None:
        if isinstance(shape, Value):
            if value is MISSING or value is None:
                return
            item = shape.field
            if isinstance(item, EnumField) and item.multiple:
                values[name] = list(item.values_of(value))
            else:
                values[name] = item.serialize(_python_value(item, value))
            return
        if isinstance(shape, Group):
            given = value if isinstance(value, Mapping) else {}
            for found in shape.properties:
                child = f"{name}.{segment(found.key)}"
                if found.key in given:
                    one = given[found.key]
                    if top and self.partial:
                        values[child + SET] = ""
                else:
                    # A property left unset still starts at its default, for
                    # when it is set.
                    one = found.default
                self._flatten(found.shape, one, child, values)
            return
        if isinstance(shape, Rows):
            for index, item in enumerate(value if isinstance(value, list) else []):
                row = f"{name}.{index}"
                values[row] = ""
                given = item if isinstance(item, Mapping) else {}
                for column in shape.item.properties:
                    self._flatten(
                        column.shape,
                        given.get(column.key, column.default),
                        f"{row}.{segment(column.key)}",
                        values,
                    )
            return
        if isinstance(shape, Pairs):
            given = value if isinstance(value, Mapping) else {}
            for index, (key, one) in enumerate(given.items()):
                row = f"{name}.{index}"
                values[row] = ""
                values[f"{row}.key"] = shape.key.serialize(key)
                values[f"{row}.value"] = shape.value.serialize(
                    _python_value(shape.value, one)
                )

    # From the form's inputs to a document.

    def read(self, data: Mapping[str, Any], name: str, *, stored: Any = None) -> Any:
        """Read the document the form sent, and check it against the schema.

        Keys the stored document holds that the schema does not name are
        kept, unless the schema refuses them. Raises DocumentError, holding
        a message for each input that needs another look.
        """
        reading = _Reading()
        document = self._read(self.shape, data, name, reading, top=True)
        if document is MISSING:
            document = None
        document = _keep_unknown(self.shape, document, stored)
        try:
            checked = self._validate(document, name, reading)
        except DocumentError as error:
            # Checked even when a part could not be read, so every problem
            # shows at once; the part's own message comes first.
            for where, message in error.errors.items():
                reading.errors.setdefault(where, message)
        if reading.errors:
            raise DocumentError(name, reading.errors)
        return checked

    def _read(
        self,
        shape: Shape,
        data: Mapping[str, Any],
        name: str,
        reading: _Reading,
        *,
        top: bool = False,
    ) -> Any:
        if isinstance(shape, Fixed):
            return shape.value
        if isinstance(shape, Value):
            return _read_value(shape.field, data.get(name), name, reading)
        if isinstance(shape, Group):
            return self._read_group(shape, data, name, reading, top=top)
        if isinstance(shape, Rows):
            items, names = [], []
            for number in row_numbers(data, name):
                row = f"{name}.{number}"
                cells = [f"{row}.{segment(one.key)}" for one in shape.item.properties]
                if all(_blank(data.get(cell)) for cell in cells):
                    # A row added and left empty.
                    continue
                items.append(self._read_group(shape.item, data, row, reading))
                names.append(row)
            reading.rows[name] = names
            return items
        if isinstance(shape, Pairs):
            return self._read_pairs(shape, data, name, reading)
        return MISSING

    def _read_group(
        self,
        shape: Group,
        data: Mapping[str, Any],
        name: str,
        reading: _Reading,
        *,
        top: bool = False,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for found in shape.properties:
            child = f"{name}.{segment(found.key)}"
            if top and self.partial and child + SET not in data:
                continue
            one = self._read(found.shape, data, child, reading)
            if one is MISSING:
                continue
            if one is None:
                # Left empty: null where the schema asks for the key and
                # allows null, and otherwise left out, so a default applies.
                if found.required and found.nullable:
                    document[found.key] = None
                continue
            document[found.key] = one
        return document

    def _read_pairs(
        self, shape: Pairs, data: Mapping[str, Any], name: str, reading: _Reading
    ) -> dict[str, Any]:
        pairs: dict[str, Any] = {}
        rows: dict[str, str] = {}
        for number in row_numbers(data, name):
            row = f"{name}.{number}"
            key_raw, value_raw = data.get(f"{row}.key"), data.get(f"{row}.value")
            if _blank(key_raw) and _blank(value_raw):
                continue
            key = _read_value(shape.key, key_raw, f"{row}.key", reading)
            value = _read_value(shape.value, value_raw, f"{row}.value", reading)
            if key is MISSING:
                continue
            text = str(key)
            if text in rows:
                reading.errors[f"{row}.key"] = _("This key is given twice.")
                continue
            rows[text] = row
            if value is not MISSING:
                pairs[text] = value
        reading.keys[name] = rows
        return pairs

    # Checking a document against the schema.

    def check(self, document: Any, name: str) -> Any:
        """Check a whole document, as the API or a code box sends it."""
        return self._validate(document, name, _Reading())

    def _validate(self, document: Any, name: str, reading: _Reading) -> Any:
        adapter = self._checking_adapter()
        if adapter is None:
            # A JSON Schema written as a dict: each part's field has checked
            # what the form draws.
            return to_jsonable_python(document)
        try:
            checked = adapter.validate_python(document)
        except PydanticValidationError as error:
            errors: dict[str, str] = {}
            places: dict[str, str] = {}
            for found in error.errors():
                where, place, inside = self.where(found["loc"], name, reading)
                message = message_for(found)
                if inside:
                    # Inside a part written as JSON: say where in it.
                    message = _(
                        "{place}: {problem}",
                        place=".".join(str(one) for one in inside),
                        problem=message,
                    )
                errors.setdefault(where, message)
                places.setdefault(where, ", ".join(place))
            raise DocumentError(name, errors, places) from None
        return adapter.dump_python(
            checked, mode="json", by_alias=True, exclude_unset=self.partial
        )

    def _checking_adapter(self) -> TypeAdapter[Any] | None:
        if self.adapter is None or not self.partial:
            return self.adapter
        if self._partial_adapter is None:
            model = self.schema
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                # Only a model can be checked a few properties at a time; any
                # other type is checked by each part's own field.
                return None
            self._partial_adapter = TypeAdapter(_partial_model(model))
        return self._partial_adapter

    def where(
        self, location: Sequence[Any], name: str, reading: _Reading
    ) -> tuple[str, list[str], list[Any]]:
        """The input a problem at a place in the document belongs to.

        Returns its name, the labels of the parts it sits in, such as
        Channels, row 2, Id, and what is left of the place inside that input,
        as for a problem inside a part written as JSON.
        """
        shape = self.shape
        place: list[str] = []
        parts = list(location)
        while parts:
            part = parts[0]
            if isinstance(shape, Group):
                found = shape.find(str(part))
                if found is None:
                    break
                name = f"{name}.{segment(found.key)}"
                place.append(found.label)
                shape = found.shape
            elif isinstance(shape, Rows) and isinstance(part, int):
                rows = reading.rows.get(name)
                if rows is not None and part >= len(rows):
                    break
                name = rows[part] if rows is not None else f"{name}.{part}"
                place.append(_("row {number}", number=part + 1))
                shape = shape.item
            elif isinstance(shape, Pairs):
                place.append(str(part))
                parts.pop(0)
                row = reading.keys.get(name, {}).get(str(part))
                if row is not None:
                    name = f"{row}.key" if parts[:1] == ["[key]"] else f"{row}.value"
                return name, place, []
            else:
                break
            parts.pop(0)
        return name, place, parts

    # The record page.

    def shown(self, value: Any) -> "Shown":
        """The document as labelled values, for the record page.

        In a partial document, each field the document leaves out is marked
        unset, so the page can list it apart from what is set.
        """
        shown = _shown(self.shape, value, "")
        if self.partial and isinstance(self.shape, Group):
            given = value if isinstance(value, Mapping) else {}
            drawn = [
                found.key
                for found in self.shape.properties
                if not isinstance(found.shape, Fixed)
            ]
            for entry, key in zip(shown.entries, drawn, strict=True):
                entry.unset = key not in given
        return shown


def _partial_model(model: type[BaseModel]) -> type[BaseModel]:
    """The model with every property optional, its checks kept.

    A property left out is not checked, and not written back.
    """
    fields: dict[str, Any] = {}
    for key, info in model.model_fields.items():
        loose = copy.copy(info)
        loose.default = None
        loose.default_factory = None
        fields[key] = (info.annotation, loose)
    built: type[BaseModel] = create_model(
        f"Partial{model.__name__}", __base__=model, **fields
    )
    return built


def _keep_unknown(shape: Shape, document: Any, stored: Any) -> Any:
    """Put back the keys a stored document holds that the schema does not name."""
    if not isinstance(shape, Group) or not isinstance(document, dict):
        return document
    if not isinstance(stored, Mapping):
        return document
    for key, value in stored.items():
        found = shape.find(key)
        if found is None:
            if not shape.closed:
                document.setdefault(key, value)
        elif isinstance(found.shape, Group) and key in document:
            document[key] = _keep_unknown(found.shape, document[key], value)
    return document


def message_for(error: ErrorDetails) -> str:
    """What a problem Pydantic found says, in the page's language."""
    kind = error["type"]
    context = error.get("ctx") or {}
    if kind == "missing":
        return _("This field is required.")
    if kind == "greater_than_equal":
        return _above_or_at(context["ge"])
    if kind == "less_than_equal":
        return _below_or_at(context["le"])
    if kind == "greater_than":
        return _above(context["gt"])
    if kind == "less_than":
        return _below(context["lt"])
    if kind == "multiple_of":
        return _(
            "Enter a multiple of {step}.", step=_number_text(context["multiple_of"])
        )
    if kind == "string_too_short":
        return _("Enter at least {count} characters.", count=context["min_length"])
    if kind == "string_too_long":
        return _(
            "Keep this to {count} characters or fewer.", count=context["max_length"]
        )
    if kind == "too_short":
        return _("Add at least {count}.", count=context["min_length"])
    if kind == "too_long":
        return _("Keep this to {count} or fewer.", count=context["max_length"])
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return _("Enter a whole number.")
    if kind in ("float_parsing", "float_type", "decimal_parsing", "decimal_type"):
        return _("Enter a number.")
    if kind in ("string_type", "string_unicode"):
        return _("Enter some text.")
    if kind in ("bool_parsing", "bool_type"):
        return _("Choose yes or no.")
    if kind in ("literal_error", "enum"):
        return _("Choose one of the listed options.")
    if kind.startswith("url_"):
        return _("Enter a full address, such as https://example.com.")
    if kind == "string_pattern_mismatch":
        return _("Enter it in the form this field expects.")
    if kind == "extra_forbidden":
        return _("This is not part of the document.")
    if kind in ("value_error", "assertion_error") and "error" in context:
        # The application's own words, from its own validator.
        return str(context["error"])
    return _("Enter a valid value.")


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


# The record page.


@dataclass
class Shown:
    """One part of a document as the record page shows it.

    A value has its `text`; a group its `entries`; rows and pairs their
    `columns` and `rows`; and a part the form writes as JSON its `code`.
    """

    kind: str
    label: str = ""
    text: str = ""
    field: Field[Any] | None = None
    value: Any = None
    entries: list["Shown"] = dataclasses.field(default_factory=list)
    columns: list[str] = dataclasses.field(default_factory=list)
    rows: list[list["Shown"]] = dataclasses.field(default_factory=list)
    code: str = ""
    # A field a partial document leaves out.
    unset: bool = False

    @property
    def unset_entries(self) -> list["Shown"]:
        """The fields a partial document leaves out, in the schema's order."""
        return [entry for entry in self.entries if entry.unset]

    @property
    def missing(self) -> bool:
        """Whether the document has no value here at all, not even an empty one."""
        return self.value is MISSING

    @property
    def empty(self) -> bool:
        """Whether the document holds nothing here."""
        if self.kind == "value":
            return self.value is MISSING or self.value is None or self.text == ""
        if self.kind == "code":
            return self.value is MISSING
        if self.kind in ("rows", "pairs"):
            return not self.rows
        return False


def _shown(shape: Shape, value: Any, label: str) -> Shown:
    if isinstance(shape, Group):
        given = value if isinstance(value, Mapping) else {}
        return Shown(
            "group",
            label,
            entries=[
                _shown(found.shape, given.get(found.key, MISSING), found.label)
                for found in shape.properties
                if not isinstance(found.shape, Fixed)
            ],
        )
    if isinstance(shape, Rows):
        columns = [
            one for one in shape.item.properties if not isinstance(one.shape, Fixed)
        ]
        items = value if isinstance(value, list) else []
        return Shown(
            "rows",
            label,
            value=value,
            columns=[one.label for one in columns],
            rows=[
                [
                    _shown(one.shape, item.get(one.key, MISSING), one.label)
                    for one in columns
                ]
                for item in items
                if isinstance(item, Mapping)
            ],
        )
    if isinstance(shape, Pairs):
        given = value if isinstance(value, Mapping) else {}
        return Shown(
            "pairs",
            label,
            value=value,
            rows=[
                [
                    Shown("key", text=shape.key.display(key), value=key),
                    _shown(Value(shape.value), one, ""),
                ]
                for key, one in given.items()
            ],
        )
    if isinstance(shape, Value):
        item = shape.field
        if isinstance(item, DocumentCode):
            code = "" if value is MISSING else item.display(value)
            return Shown("code", label, value=value, code=code)
        if value is MISSING or value is None:
            return Shown("value", label, field=item, value=value)
        python = _python_value(item, value)
        return Shown(
            "value", label, text=item.display(python), field=item, value=python
        )
    return Shown("value", label, value=MISSING)


def is_schema_function(source: Any) -> bool:
    """Whether a schema is a function that gives one for each record.

    A class is a schema itself, and so is a type such as list[int].
    """
    return (
        callable(source)
        and not isinstance(source, type | Mapping)
        and not hasattr(source, "__origin__")
    )
