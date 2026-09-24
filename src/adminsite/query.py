from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from adminsite.filters.base import FilterValue

DEFAULT_PAGE_SIZE = 25


class CountMode(StrEnum):
    """How hard to work to report how many records match."""

    EXACT = "exact"
    # The database's own row estimate when nothing narrows the list, and a
    # count that stops at a cap when something does.
    ESTIMATED = "estimated"
    NONE = "none"


class Pagination(StrEnum):
    """How the list moves from one page to the next."""

    # Page numbers. Simple, but the database still walks every row before
    # the page, so deep pages on a big table get slow.
    OFFSET = "offset"
    # Continue after the last row seen. Every page costs the same, but there
    # is no jumping to page 40, only previous and next.
    KEYSET = "keyset"


@dataclass(frozen=True, slots=True)
class Sort:
    """One ordering step, such as newest first."""

    path: str
    descending: bool = False

    @classmethod
    def parse(cls, value: str) -> "Sort":
        """Read a sort written as `total` or `-total`."""
        if value.startswith("-"):
            return cls(value[1:], descending=True)
        return cls(value)

    def __str__(self) -> str:
        return f"-{self.path}" if self.descending else self.path


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """What to read: which paths, which order, which page."""

    paths: tuple[str, ...] = ()
    # Columns to leave out of the select, however wide the table is.
    defer: tuple[str, ...] = ()
    search: str = ""
    search_paths: tuple[str, ...] = ()
    # The condition the view works out for the search, used instead of
    # matching the search paths. Left out of comparisons: a SQL condition
    # answers == with another condition, not with True or False.
    search_condition: Any = field(default=None, compare=False)
    filters: tuple[FilterValue, ...] = ()
    sort: tuple[Sort, ...] = ()
    offset: int = 0
    limit: int | None = DEFAULT_PAGE_SIZE
    count: CountMode = CountMode.EXACT
    keyset: bool = False
    after: str = ""
    before: str = ""

    def page(self, number: int) -> "QuerySpec":
        """Return the same query moved to a one based page number."""
        size = self.limit or DEFAULT_PAGE_SIZE
        return self.replace(offset=max(number - 1, 0) * size)

    def replace(self, **changes: Any) -> "QuerySpec":
        """Return a copy with some parts changed."""
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Page:
    """One page of records, with enough to draw the pager."""

    rows: Sequence[Any] = field(default_factory=tuple)
    offset: int = 0
    limit: int | None = None
    total: int | None = None
    has_next: bool = False
    # The total is the database's estimate, or a count that stopped early.
    estimated: bool = False
    at_least: bool = False
    # Set when the page was read by keyset: where the next and previous
    # pages start.
    keyset: bool = False
    next_cursor: str = ""
    previous_cursor: str = ""

    @property
    def has_previous(self) -> bool:
        """Whether there is a page before this one."""
        return self.offset > 0 or bool(self.previous_cursor)

    @property
    def first_position(self) -> int:
        """The one based position of the first row on this page."""
        return self.offset + 1 if self.rows else 0

    @property
    def last_position(self) -> int:
        """The one based position of the last row on this page."""
        return self.offset + len(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Any:
        return iter(self.rows)
