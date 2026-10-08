from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from adminsite._text import humanize
from adminsite.i18n import gettext as _
from adminsite.i18n import listed

__all__ = [
    "Filter",
    "FilterContext",
    "FilterOption",
    "FilterValue",
    "parse_filters",
]


@dataclass(frozen=True, slots=True)
class FilterOption:
    """One choice a filter offers, with how many records it would match.

    Args:
        value: What the URL carries when it is picked, such as "late".
        label: What the list shows for it, such as "Overdue".
        count: How many records it would match, shown beside it. None shows
            no count.
    """

    value: str
    label: str
    count: int | None = None


@dataclass(frozen=True, slots=True)
class FilterValue:
    """What the user picked for one filter."""

    name: str
    """The filter's name, as the URL carries it."""
    values: tuple[str, ...]
    """Every value picked, as text, in the order the URL carries them."""
    source: Any = field(default=None, compare=False, repr=False)
    """The filter these were read for.

    So a filter that `get_list_filters` adds for a single request is applied
    by the query that request makes.
    """

    @property
    def first(self) -> str:
        """The first value, which is all a single choice filter uses."""
        return self.values[0] if self.values else ""

    def __bool__(self) -> bool:
        return bool(self.values)


class FilterContext(Protocol):
    """What a filter can ask the database while building its options."""

    async def count_by(self, path: str) -> Mapping[str, int]:
        """Count the matching records grouped by the value at this path.

        Empty, without asking the database, where the list shows no counts
        for the filter asking: see `Filter.show_counts`.
        """
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
    """Whether several options can be picked at once, all in `value.values`."""
    template = "choice"
    """The control the list draws: "choice", "range", "relation" or "text"."""
    show_counts: bool | None = None
    """Whether each option says how many records it matches.

    Counting reads every record the list may show, so None, the default,
    leaves it to the view: the options are counted where its `count_mode` is
    `CountMode.EXACT`, and not on a view too big to count.
    """

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
        """List the choices to offer. Empty when the filter is free text.

        Args:
            context: Asks the database for counts and values, within the
                view's scope, such as `await context.count_by(self.path)`.

        Returns:
            The choices, in the order they are offered.
        """
        return ()

    def describe(self, value: FilterValue, options: Sequence[FilterOption] = ()) -> str:
        """Write the chip shown while the filter is on."""
        labels = {option.value: option.label for option in options}
        shown = listed(labels.get(item, item) for item in value.values)
        return _("{filter}: {chosen}", filter=self.label, chosen=shown)

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
