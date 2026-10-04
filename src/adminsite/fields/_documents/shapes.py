"""The shapes a JSON Schema describes, and how their inputs are named."""

import string
from dataclasses import dataclass
from typing import Any

from adminsite.fields.base import Field

__all__ = [
    "DRAWN",
    "MISSING",
    "SET",
    "Fixed",
    "Group",
    "Pairs",
    "Property",
    "Rows",
    "Shape",
    "Value",
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
