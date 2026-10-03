from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Select, false, func

from adminsite.backends.sqlalchemy.repository import Scope, SQLAlchemyRepository
from adminsite.backends.sqlalchemy.session import SessionAdapter
from adminsite.backends.sqlalchemy.values import to_column_type
from adminsite.exceptions import InvalidPathError
from adminsite.filters.base import (
    Filter,
    FilterContext,
    FilterOption,
    FilterValue,
)
from adminsite.i18n import gettext as _
from adminsite.query import QuerySpec
from adminsite.schema import FieldSchema
from adminsite.text import humanize

__all__ = [
    "DISTINCT_LIMIT",
    "RANGE_SEPARATOR",
    "BooleanFilter",
    "ChoiceFilter",
    "DateRangeFilter",
    "NumberRangeFilter",
    "RelationFilter",
    "SQLFilter",
    "SQLFilterContext",
    "TextFilter",
    "filter_for",
    "stored_value",
]

RANGE_SEPARATOR = ","
DISTINCT_LIMIT = 50

# The model a filter narrows the list of.
M = TypeVar("M")


class SQLFilter(Filter, Generic[M]):
    """A filter that narrows a SQLAlchemy statement: `SQLFilter[Product]`.

    Write a custom filter by subclassing this, offering its choices from
    `options` and returning a condition from `condition`. Override `apply`
    instead when the filter needs to change the statement itself, for
    example to add a join.

    Args:
        name: The filter's name in the URL, such as "delivery" in
            `?delivery=late`.
        path: The column it reads, such as "customer.region". Left out, the
            name.
        label: The filter's label in the list's toolbar. Left out, the name
            in words.
    """

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[M]
    ) -> ColumnElement[bool] | None:
        """The condition this filter adds, or nothing to leave the list be.

        Args:
            value: What the user picked: `value.first`, or every one in
                `value.values`.
            repository: The repository of the view's model.

        Returns:
            A condition on the view's model, or None to leave the list be.
        """
        raise NotImplementedError

    def apply(
        self,
        statement: Select[Any],
        value: FilterValue,
        repository: SQLAlchemyRepository[M],
    ) -> Select[Any]:
        """Narrow the statement with this filter's condition.

        Args:
            statement: The select the list is about to run.
            value: What the user picked.
            repository: The repository of the view's model.

        Returns:
            The statement, narrowed.
        """
        condition = self.condition(value, repository)
        if condition is None:
            return statement
        return statement.where(condition)


class ChoiceFilter(SQLFilter[Any]):
    """Pick one or more values from a fixed list, such as a status."""

    multiple = True
    template = "choice"

    def __init__(
        self,
        name: str,
        *,
        choices: Sequence[tuple[str, str]] = (),
        show_counts: bool = True,
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.choices = tuple(choices)
        self.show_counts = show_counts

    async def options(self, context: FilterContext) -> Sequence[FilterOption]:
        """List the choices, with how many records each one matches."""
        counts: Mapping[str, int] = {}
        if self.show_counts:
            counts = await context.count_by(self.path)
        return tuple(
            FilterOption(value, label, counts.get(value) if counts else None)
            for value, label in self.choices
        )

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match any of the chosen values."""
        return repository.condition_at(
            self.path, lambda column, field: column.in_(list(value.values))
        )


class BooleanFilter(SQLFilter[Any]):
    """A yes or no column."""

    template = "choice"

    def __init__(
        self,
        name: str,
        *,
        yes_label: str = "Yes",
        no_label: str = "No",
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.yes_label = yes_label
        self.no_label = no_label

    async def options(self, context: FilterContext) -> Sequence[FilterOption]:
        """Offer yes and no, with how many records each one matches."""
        counts = await context.count_by(self.path)
        return (
            FilterOption("true", _(self.yes_label), counts.get("true")),
            FilterOption("false", _(self.no_label), counts.get("false")),
        )

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match records where the column is set or not set."""
        wanted = value.first == "true"
        return repository.condition_at(
            self.path, lambda column, field: column.is_(wanted)
        )


class NumberRangeFilter(SQLFilter[Any]):
    """A range of numbers, written as `min,max` with either side empty."""

    template = "range"
    # What each end of the range is typed into.
    input_type = "number"

    def __init__(
        self,
        name: str,
        *,
        presets: Sequence[tuple[str, str]] = (),
        **options: Any,
    ) -> None:
        super().__init__(name, **options)
        self.presets = tuple(presets)

    async def options(self, context: FilterContext) -> Sequence[FilterOption]:
        """List the shortcuts this filter offers, if any."""
        return tuple(FilterOption(value, label) for value, label in self.presets)

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match records inside the range."""
        low, high = self._bounds(value.first)
        if low is None and high is None:
            return None

        def build(
            column: ColumnElement[Any], field: FieldSchema
        ) -> ColumnElement[bool] | None:
            clauses = []
            if low is not None:
                clauses.append(column >= low)
            if high is not None:
                clauses.append(column <= high)
            first, *rest = clauses
            for clause in rest:
                first = first & clause
            return first

        return repository.condition_at(self.path, build)

    def _bounds(self, raw: str) -> tuple[Decimal | None, Decimal | None]:
        low, _, high = raw.partition(RANGE_SEPARATOR)
        return self._number(low), self._number(high)

    def _number(self, raw: str) -> Decimal | None:
        text = raw.strip()
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None


class DateRangeFilter(SQLFilter[Any]):
    """A period, either one of the shortcuts or `from,to` as dates."""

    template = "range"
    input_type = "date"

    PRESETS = (
        ("today", "Today", 1),
        ("week", "Last 7 days", 7),
        ("month", "Last 30 days", 30),
        ("quarter", "Last 90 days", 90),
    )

    def __init__(self, name: str, *, now: datetime | None = None, **options: Any):
        super().__init__(name, **options)
        self._now = now

    async def options(self, context: FilterContext) -> Sequence[FilterOption]:
        """List the periods this filter offers."""
        return tuple(
            FilterOption(value, _(label)) for value, label, _days in self.PRESETS
        )

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match records whose date falls inside the period."""
        start, end = self._period(value.first)
        if start is None and end is None:
            return None

        def build(
            column: ColumnElement[Any], field: FieldSchema
        ) -> ColumnElement[bool] | None:
            wants_date = field.python_type is date
            clauses = []
            if start is not None:
                clauses.append(column >= (start.date() if wants_date else start))
            if end is not None:
                clauses.append(column <= (end.date() if wants_date else end))
            first, *rest = clauses
            for clause in rest:
                first = first & clause
            return first

        return repository.condition_at(self.path, build)

    def _period(self, raw: str) -> tuple[datetime | None, datetime | None]:
        for name, _label, days in self.PRESETS:
            if raw == name:
                return self._now_value() - timedelta(days=days), None
        start, _, end = raw.partition(RANGE_SEPARATOR)
        return self._day(start), self._day(end, end_of_day=True)

    def _now_value(self) -> datetime:
        return self._now or datetime.now(UTC).replace(tzinfo=None)

    def _day(self, raw: str, end_of_day: bool = False) -> datetime | None:
        text = raw.strip()
        if not text:
            return None
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None
        moment = datetime.combine(day, datetime.min.time())
        return moment + timedelta(days=1, microseconds=-1) if end_of_day else moment


class RelationFilter(SQLFilter[Any]):
    """Records linked to one of the chosen related records."""

    multiple = True
    template = "relation"

    def __init__(self, name: str, *, key: str = "id", **options: Any) -> None:
        super().__init__(name, **options)
        self.key = key

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match records linked to any of the chosen keys."""

        def build(
            column: ColumnElement[Any], field: FieldSchema
        ) -> ColumnElement[bool] | None:
            keys = []
            for raw in value.values:
                try:
                    keys.append(to_column_type(field.python_type, raw))
                except ValueError:
                    continue
            return column.in_(keys) if keys else false()

        return repository.condition_at(f"{self.path}.{self.key}", build)


class TextFilter(SQLFilter[Any]):
    """Records whose text contains what was typed."""

    template = "text"

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Any]
    ) -> ColumnElement[bool] | None:
        """Match the text anywhere in the column."""
        term = value.first
        return repository.condition_at(
            self.path, lambda column, field: column.ilike(f"%{term}%")
        )


@dataclass
class SQLFilterContext:
    """Lets a filter ask the database what to offer, and how often.

    Both stay inside the view's scope, so a filter never counts or offers a
    record the list itself would not show.
    """

    session: SessionAdapter
    repository: SQLAlchemyRepository[Any]
    spec: QuerySpec
    scope: Scope | None = None

    async def count_by(self, path: str) -> Mapping[str, int]:
        """Count matching records grouped by the value at this path.

        Counts reflect the search, not the other filters, so the numbers
        stay steady while the user changes their mind. A path this cannot
        count, such as one through a relationship, gives no counts rather
        than an error: the filter still works, it just shows no numbers.
        """
        try:
            column = self._own_column(path)
        except InvalidPathError:
            return {}
        statement = self._rows().with_only_columns(column, func.count())
        statement = statement.group_by(column)
        condition = self.repository.search_clause(self.spec)
        if condition is not None:
            statement = statement.where(condition)

        result = await self.session.execute(statement)
        return {stored_value(value): count for value, count in result.all()}

    async def distinct(self, path: str, limit: int = DISTINCT_LIMIT) -> Sequence[Any]:
        """List the values that appear at this path."""
        column = self._own_column(path)
        statement = self._rows().with_only_columns(column).distinct().limit(limit)
        return list((await self.session.scalars(statement)).all())

    def _rows(self) -> Select[Any]:
        """The records the list may show, in no order."""
        # A scope may sort, and a count grouped by value cannot be sorted by
        # a column it does not group by.
        return self.repository.base_statement(self.scope).order_by(None)

    def _own_column(self, path: str) -> Any:
        if "." in path:
            raise InvalidPathError(
                path, "options can only be counted on the model's own columns."
            )
        self.repository.schema.field_named(path)
        return getattr(self.repository.model, path)


def stored_value(value: Any) -> str:
    """Write a value the way it appears in a URL."""
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def filter_for(
    repository: SQLAlchemyRepository[Any], path: str, **options: Any
) -> SQLFilter[Any]:
    """Build the filter that fits what the path points at."""
    resolved = repository.inspector.resolve(repository.model, path)
    name = options.pop("name", path.replace(".", "__"))
    options.setdefault("path", path)
    options.setdefault("label", resolved.label)

    if resolved.field is None:
        return RelationFilter(name, **options)

    field = resolved.field
    if field.enum_values:
        options.setdefault(
            "choices",
            tuple((value, humanize(value)) for value in field.enum_values),
        )
        return ChoiceFilter(name, **options)
    if field.python_type is bool:
        return BooleanFilter(name, **options)
    if field.python_type in (datetime, date):
        return DateRangeFilter(name, **options)
    if field.python_type in (int, float, Decimal):
        return NumberRangeFilter(name, **options)
    return TextFilter(name, **options)
