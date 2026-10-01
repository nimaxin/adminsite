from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from adminsite.fields.base import Field

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
    """A date together with a time."""

    widget = "datetime"
    python_type = datetime
    error_message = "Enter a date and time, for example 2026-09-18 14:30."
    column_types = (datetime,)
    unused_options = frozenset({"max_length"})

    def display(self, value: Any) -> str:
        """Show the date in words, followed by the time."""
        if value is None:
            return ""
        return f"{format_date(value)} {format_time(value.time())}"

    def serialize(self, value: Any) -> str:
        """Show the value the way a datetime input expects it."""
        if value is None:
            return ""
        moment: datetime = value
        return moment.strftime("%Y-%m-%dT%H:%M")


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
