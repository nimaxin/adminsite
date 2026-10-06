from dataclasses import KW_ONLY, dataclass
from datetime import date, datetime, time
from typing import Any

from adminsite.exceptions import FieldValidationError
from adminsite.fields.base import Field
from adminsite.i18n import gettext as _
from adminsite.schema import FieldSchema
from adminsite.timezones import (
    current_timezone,
    happens_once,
    to_column_time,
    to_local_time,
)

__all__ = [
    "MONTHS",
    "DateField",
    "DateTimeField",
    "TimeField",
    "format_date",
    "format_time",
]

MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_date(value: date) -> str:
    """Write a date the way a person reads it, such as Sep 8, 2026."""
    return f"{MONTHS[value.month - 1]} {value.day}, {value.year}"


def format_time(value: time) -> str:
    """Write a time as hours and minutes on the 24 hour clock."""
    return f"{value.hour:02d}:{value.minute:02d}"


@dataclass(eq=False, repr=False)
class DateField(Field[date | None]):
    """A calendar date."""

    widget = "date"
    python_type = date
    error_message = "Enter a date, for example 2026-09-18."
    column_types = (date,)
    unused_options = frozenset({"max_length"})

    def display(self, value: Any) -> str:
        """Show the date in words."""
        if value is None:
            return ""
        return format_date(value)

    def serialize(self, value: Any) -> str:
        """Show the date the way a date input expects it."""
        if value is None:
            return ""
        day: date = value
        return day.isoformat()


@dataclass(eq=False, repr=False)
class DateTimeField(Field[datetime | None]):
    """A date together with a time, on the clock of the person reading it.

    A time the column keeps without a zone is taken to be in the admin's
    `database_timezone`. What a person types is read on their own clock,
    and saved the way the column keeps it.

    Args:
        with_timezone: Whether the column keeps the time zone, as one
            declared `DateTime(timezone=True)` does. It is then given a time
            with its zone, and otherwise a plain one. Left as None, the
            column decides.
    """

    _: KW_ONLY
    with_timezone: bool | None = None

    widget = "datetime"
    python_type = datetime
    error_message = "Enter a date and time, for example 2026-09-18 14:30."
    column_types = (datetime,)
    unused_options = frozenset({"max_length"})

    def display(self, value: Any) -> str:
        """Show the date in words, followed by the time, on the reader's clock."""
        if value is None:
            return ""
        moment = to_local_time(value)
        return f"{format_date(moment)} {format_time(moment.time())}"

    def text_for(self, record: Any, value: Any) -> str:
        """The text shown, in the field's format, on the reader's clock."""
        local = None if value is None else to_local_time(value)
        return super().text_for(record, local)

    def serialize(self, value: Any) -> str:
        """Show the value the way a datetime input expects it, on the reader's clock."""
        if value is None:
            return ""
        return to_local_time(value).strftime("%Y-%m-%dT%H:%M")

    def to_python(self, text: str) -> Any:
        """Read the time on the person's clock, as the column keeps it.

        A time written with an offset, such as 2026-09-18T14:30+03:30, keeps
        it. A time the clocks skip or repeat when they change is refused,
        since it names no one moment.
        """
        moment: datetime = super().to_python(text)
        if moment.tzinfo is None and not happens_once(moment):
            raise FieldValidationError(
                self.name,
                _(
                    "{zone} skips or repeats this time when its clocks change. "
                    "Enter another time.",
                    zone=str(current_timezone.get()),
                ),
            )
        return to_column_time(moment, with_timezone=bool(self.with_timezone))

    def column_options(self, schema: FieldSchema) -> dict[str, Any]:
        """The column's options, and whether it keeps the time zone."""
        options = super().column_options(schema)
        if self.with_timezone is None:
            options["with_timezone"] = schema.with_timezone
        return options


@dataclass(eq=False, repr=False)
class TimeField(Field[time | None]):
    """A time of day."""

    widget = "time"
    python_type = time
    error_message = "Enter a time, for example 14:30."
    column_types = (time,)
    unused_options = frozenset({"max_length"})

    def display(self, value: Any) -> str:
        """Show hours and minutes."""
        if value is None:
            return ""
        return format_time(value)

    def serialize(self, value: Any) -> str:
        """Show the value the way a time input expects it."""
        if value is None:
            return ""
        return format_time(value)
