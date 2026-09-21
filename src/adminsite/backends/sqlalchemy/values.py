from decimal import InvalidOperation
from typing import Any


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
