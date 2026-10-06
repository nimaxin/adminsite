"""Time zones: every time is shown on the clock of the person reading it.

```python
from datetime import datetime

from adminsite.timezones import current_timezone

today = datetime.now(current_timezone.get()).date()
```

A database keeps most times without a zone, and those are taken to be in
the admin's `database_timezone`, UTC unless it says otherwise. The zone of
the person reading is set once per request, like the language, so a field
deep inside a view can convert a time without being handed the request.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adminsite.exceptions import AdminSiteError

__all__ = [
    "BROWSER_TIMEZONE_COOKIE",
    "TIMEZONE_COOKIE",
    "activate_timezone",
    "current_timezone",
    "database_timezone",
    "find_timezone",
    "happens_once",
    "known_timezone",
    "offset_text",
    "showing",
    "to_column_time",
    "to_local_time",
    "to_utc_time",
]

# The time zone picked from the menu, and the one the browser is set to,
# which a script on every page keeps up to date.
TIMEZONE_COOKIE = "adminsite_timezone"
BROWSER_TIMEZONE_COOKIE = "adminsite_browser_timezone"

current_timezone: ContextVar[tzinfo] = ContextVar("adminsite_timezone", default=UTC)
database_timezone: ContextVar[tzinfo] = ContextVar(
    "adminsite_database_timezone", default=UTC
)

# A name from the time zone database, such as Asia/Tehran or Etc/GMT+3. A
# cookie can hold anything, and only such a name is looked up.
_NAME = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+){0,2}")


def find_timezone(name: str) -> tzinfo | None:
    """The time zone with this name, such as "Asia/Tehran", or None for no such zone."""
    # UTC needs no time zone database, so it works where none is installed.
    if name == "UTC":
        return UTC
    if len(name) > 64 or not _NAME.fullmatch(name):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def known_timezone(name: str) -> tzinfo:
    """The time zone with this name, as a setting gives it, or refuse to start."""
    found = find_timezone(name)
    if found is None:
        raise AdminSiteError(
            f"No time zone is called {name!r}. Use a name from the time zone "
            'database, such as "Asia/Tehran" or "UTC".'
        )
    return found


def activate_timezone(timezone: tzinfo, database: tzinfo = UTC) -> None:
    """Show this request's times in a time zone, the database's read in another."""
    current_timezone.set(timezone)
    database_timezone.set(database)


@contextmanager
def showing(timezone: tzinfo) -> Iterator[None]:
    """Show times in another time zone for a while, such as UTC for the audit log."""
    token = current_timezone.set(timezone)
    try:
        yield
    finally:
        current_timezone.reset(token)


def to_local_time(value: datetime) -> datetime:
    """A time from the database, on the clock of the person reading it.

    One kept without a zone is taken to be in the database's time zone.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=database_timezone.get())
    return value.astimezone(current_timezone.get())


def to_utc_time(value: datetime) -> datetime:
    """A time from the database in UTC, with its offset, as the API sends it."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=database_timezone.get())
    return value.astimezone(UTC)


def to_column_time(
    value: datetime, *, with_timezone: bool = False, stored_in: tzinfo | None = None
) -> datetime:
    """A time a person gave, as a column keeps it.

    One without a zone is read on the person's clock. A column declared
    `DateTime(timezone=True)` gets the time with its zone. Any other column
    gets a plain time in the database's time zone, or in `stored_in`.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=current_timezone.get())
    stored = value.astimezone(stored_in or database_timezone.get())
    return stored if with_timezone else stored.replace(tzinfo=None)


def happens_once(value: datetime) -> bool:
    """Whether a time without a zone names one moment on the person's clock.

    When the clocks change, the hour they skip never happens and the hour
    they repeat happens twice, so a time inside either names no one moment.
    """
    timezone = current_timezone.get()
    early = value.replace(tzinfo=timezone, fold=0)
    late = value.replace(tzinfo=timezone, fold=1)
    return early.utcoffset() == late.utcoffset()


def offset_text(timezone: tzinfo, moment: datetime | None = None) -> str:
    """How far a time zone is from UTC, such as UTC+03:30, now or at a moment."""
    shown = (moment or datetime.now(UTC)).astimezone(timezone).strftime("%z")
    if shown in ("", "+0000"):
        return "UTC"
    return f"UTC{shown[:3]}:{shown[3:5]}"
