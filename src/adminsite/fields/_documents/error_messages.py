"""What Pydantic finds wrong with a document, worded for people."""

from decimal import Decimal
from typing import Any

from pydantic_core import ErrorDetails

from adminsite.i18n import gettext as _

__all__ = [
    "message_for",
]


def _number_text(number: Any) -> str:
    """A number as a person writes it: 5, not 5.0."""
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


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
