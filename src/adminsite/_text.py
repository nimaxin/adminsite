import re
from collections.abc import Mapping
from string import Formatter
from typing import Any

__all__ = [
    "RecordValues",
    "choice_label",
    "humanize",
    "humanize_class",
    "names_itself",
    "pluralize",
    "snake_case",
    "template_names",
]

_SEPARATORS = re.compile(r"[_\s]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_VOWEL_Y = ("ay", "ey", "iy", "oy", "uy")
_SIBILANTS = ("s", "x", "z", "ch", "sh")


def humanize(name: str) -> str:
    """Turn an attribute name into a label, so `created_at` reads Created at."""
    words = [word for word in _SEPARATORS.split(name.strip("_")) if word]
    if not words:
        return name
    first, *rest = words
    return " ".join([first.capitalize(), *[word.lower() for word in rest]])


def choice_label(value: str) -> str:
    """An option as a person reads it: card reads Card, and USD stays USD."""
    return humanize(value) if value == value.lower() else value


def humanize_class(name: str) -> str:
    """Turn a class name into a label, so `OrderItem` reads Order item."""
    return humanize(_CAMEL_BOUNDARY.sub(" ", name))


def pluralize(word: str) -> str:
    """Make an English plural, good enough for a label the user can override."""
    if not word:
        return word
    lowered = word.lower()
    if lowered.endswith(_SIBILANTS):
        return f"{word}es"
    if lowered.endswith("y") and not lowered.endswith(_VOWEL_Y):
        return f"{word[:-1]}ies"
    return f"{word}s"


def snake_case(name: str) -> str:
    """Turn a name into snake case, so `OrderItem` becomes order_item."""
    spaced = _CAMEL_BOUNDARY.sub("_", name.strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", spaced).strip("_").lower()


def names_itself(record: Any) -> bool:
    """Whether a record's class gives it a name, with a `__str__` of its own.

    Without one, `str` falls back to `__repr__`, which says where the record
    sits in memory or, written for a dataclass, prints every column.
    """
    found: Any = type(record).__str__
    return found is not object.__str__


def template_names(template: str) -> list[str]:
    """The attributes a template such as `{name} ({email})` reads, in order.

    `{created_at.year}` reads `created_at`. Raises ValueError for a template
    `str.format` cannot read, and for braces that name no attribute.
    """
    names = []
    for _text, field, _spec, _conversion in Formatter().parse(template):
        if field is None:
            continue
        attribute = re.split(r"[.\[]", field, maxsplit=1)[0]
        if not attribute.isidentifier():
            raise ValueError(f"{{{field}}} names no column")
        names.append(attribute)
    return names


class RecordValues(Mapping[str, Any]):
    """Reads attributes of a record the way `str.format` reads a mapping.

    A missing attribute reads as empty, so a template such as
    `{name} ({email})` never raises while rendering a page.
    """

    def __init__(self, record: Any) -> None:
        self._record = record

    def __getitem__(self, key: str) -> Any:
        return getattr(self._record, key, "")

    def __iter__(self) -> Any:
        return iter(())

    def __len__(self) -> int:
        return 0
