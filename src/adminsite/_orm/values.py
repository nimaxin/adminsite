from decimal import InvalidOperation
from typing import Any

from adminsite.schema import FieldSchema

__all__ = [
    "to_column_type",
    "to_column_value",
]


def to_column_type(python_type: type[Any], value: Any) -> Any:
    """Convert text from a URL or a form to the type a column holds.

    SQLite compares `1 = '1'` without complaint, but Postgres refuses to
    compare an integer column with text, so every key read from a request
    goes through here first. Raises ValueError when the text cannot be the
    column's type, such as "abc" for an integer key.
    """
    if value is None or isinstance(value, python_type):
        return value
    try:
        return python_type(value)
    except (TypeError, ValueError, ArithmeticError, InvalidOperation) as error:
        raise ValueError(f"{value!r} is not a valid {python_type.__name__}.") from error


def to_column_value(field: FieldSchema, value: Any) -> Any:
    """Convert text from a URL or a form to a value the column can hold.

    Raises ValueError as `to_column_type` does, and for a number past what
    an integer column holds, such as 99999999999 for an Integer, which no
    record has and asyncpg refuses to send.
    """
    converted = to_column_type(field.python_type, value)
    whole = field.integer_range
    if whole is not None and isinstance(converted, int) and converted not in whole:
        raise ValueError(f"{value!r} is past what {field.name} holds.")
    return converted
