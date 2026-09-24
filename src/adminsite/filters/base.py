from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from adminsite.text import humanize


@dataclass(frozen=True, slots=True)
class FilterOption:
    """One choice a filter offers, with how many records it would match."""

    value: str
    label: str
    count: int | None = None


@dataclass(frozen=True, slots=True)
class FilterValue:
    """What the user picked for one filter."""

    name: str
    values: tuple[str, ...]
    # The filter these were read for, so one that get_filters adds for a
    # single request is applied by the query that request makes.
    source: Any = field(default=None, compare=False, repr=False)

    @property
    def first(self) -> str:
        """The first value, which is all a single choice filter uses."""
        return self.values[0] if self.values else ""

    def __bool__(self) -> bool:
        return bool(self.values)


class FilterContext(Protocol):
    """What a filter can ask the database while building its options."""

    async def count_by(self, path: str) -> Mapping[str, int]:
        """Count the matching records grouped by the value at this path."""
        ...

    async def distinct(self, path: str, limit: int) -> Sequence[object]:
        """List the values that appear at this path."""
        ...


class Filter:
    """A named control that narrows a list.

    Subclasses decide what the options are and how the records are
    narrowed. The name is what appears in the URL.
    """

    multiple = False
    template = "choice"

    def __init__(
        self,
        name: str,
        *,
        path: str | None = None,
        label: str | None = None,
    ) -> None:
        self.name = name
        self.path = path or name
        self.label = label if label is not None else humanize(name)

    def parse(self, raw: Sequence[str]) -> FilterValue | None:
        """Read the values submitted for this filter, or nothing."""
        values = tuple(value.strip() for value in raw if value.strip())
        if not values:
            return None
        if not self.multiple:
            values = values[:1]
        return FilterValue(self.name, values)

    async def options(self, context: FilterContext) -> Sequence[FilterOption]:
        """List the choices to offer. Empty when the filter is free text."""
        return ()

    def describe(self, value: FilterValue, options: Sequence[FilterOption] = ()) -> str:
        """Write the chip shown while the filter is on."""
        labels = {option.value: option.label for option in options}
        shown = ", ".join(labels.get(item, item) for item in value.values)
        return f"{self.label}: {shown}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


def parse_filters(
    filters: Sequence[Filter], params: Mapping[str, Sequence[str]]
) -> tuple[FilterValue, ...]:
    """Read every filter the request carries, ignoring anything unknown."""
    values = []
    for item in filters:
        raw = params.get(item.name)
        if raw is None:
            continue
        parsed = item.parse(raw)
        if parsed is not None:
            values.append(replace(parsed, source=item))
    return tuple(values)
