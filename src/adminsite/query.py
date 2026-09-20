from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

DEFAULT_PAGE_SIZE = 25


class CountMode(StrEnum):
    """How hard to work to report how many records match."""

    EXACT = "exact"
    NONE = "none"


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
    search: str = ""
    search_paths: tuple[str, ...] = ()
    sort: tuple[Sort, ...] = ()
    offset: int = 0
    limit: int | None = DEFAULT_PAGE_SIZE
    count: CountMode = CountMode.EXACT

    def page(self, number: int) -> "QuerySpec":
        """Return the same query moved to a one based page number."""
        size = self.limit or DEFAULT_PAGE_SIZE
        return self.replace(offset=max(number - 1, 0) * size)

    def replace(self, **changes: Any) -> "QuerySpec":
        """Return a copy with some parts changed."""
        current = {
            "paths": self.paths,
            "search": self.search,
            "search_paths": self.search_paths,
            "sort": self.sort,
            "offset": self.offset,
            "limit": self.limit,
            "count": self.count,
        }
        current.update(changes)
        return QuerySpec(**current)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Page:
    """One page of records, with enough to draw the pager."""

    rows: Sequence[Any] = field(default_factory=tuple)
    offset: int = 0
    limit: int | None = None
    total: int | None = None
    has_next: bool = False

    @property
    def has_previous(self) -> bool:
        """Whether there is a page before this one."""
        return self.offset > 0

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
