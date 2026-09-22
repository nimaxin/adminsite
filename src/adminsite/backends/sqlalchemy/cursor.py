import base64
import binascii
import datetime
import enum
import json
from collections.abc import Sequence
from typing import Any

from adminsite.backends.sqlalchemy.values import to_column_type


def encode_cursor(values: Sequence[Any]) -> str:
    """Write the sort values of one row as a short token for a URL."""
    data = json.dumps([_plain(value) for value in values], separators=(",", ":"))
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def decode_cursor(token: str, types: Sequence[type[Any]]) -> list[Any] | None:
    """Read a token back into values of the given types.

    Anything that does not fit, such as a token edited by hand or one left
    over from a different sort, reads as no cursor at all.
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(raw, list) or len(raw) != len(types):
        return None
    try:
        return [_typed(kind, value) for kind, value in zip(types, raw, strict=True)]
    except ValueError:
        return None


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    return str(value)


def _typed(kind: type[Any], value: Any) -> Any:
    if value is None:
        return None
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{value!r} is not a bool.")
        return value
    if issubclass(kind, datetime.date | datetime.time):
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not a {kind.__name__}.")
        return kind.fromisoformat(value)
    return to_column_type(kind, value)
