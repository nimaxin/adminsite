import re
from collections.abc import Mapping
from typing import Any

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
