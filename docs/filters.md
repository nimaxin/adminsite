# Filters

`list_filters` names what people can filter a list by. adminsite picks the filter that fits each
column, and the filters open in a drawer above the list. A filtered list keeps its filters in the
URL, so it can be bookmarked or shared, and the CSV export and bulk actions cover the same rows.

## Built-in filters

```python
from adminsite import Link


class OrderView(ModelView[Order]):
    list_filters = [
        Order.status,
        Order.total,
        Order.created_at,
        Order.customer,
        Link(Order.customer, Customer.region),
    ]
```

| Column | Filter | In the URL |
|---|---|---|
| enum | `ChoiceFilter`, several values, with counts | `?status=PAID&status=SHIPPED` |
| `bool` | `BooleanFilter` | `?is_active=true` |
| number | `NumberRangeFilter`, either end optional | `?total=10,50` or `?total=100,` |
| date or datetime | `DateRangeFilter`, a shortcut or two dates | `?created_at=week` or `?created_at=2026-09-01,2026-09-30` |
| relationship | `RelationFilter`, picking linked records by name | `?customer=3&customer=5` |
| text | `TextFilter`, matching part of the value | `?email=fischer` |

A relation filter picks records through the linked model's view, so its scope and its permissions
apply. It matches the primary key, unless it is given another column, as in
`RelationFilter("customer", key="code")`. A related model's column, such as
`Link(Order.customer, Customer.region)`, filters without a join, so rows never repeat.

## Counts

Choice and yes or no filters show how many records each option matches. A view with a `count_mode`
of `CountMode.ESTIMATED` or `CountMode.NONE` shows no counts, since counting reads every record.
`show_counts` decides for one filter: `True` counts it whatever the view says, and `False` never
does.

```python
from adminsite import CountMode
from adminsite.filters import BooleanFilter, ChoiceFilter


class OrderView(ModelView[Order]):
    list_filters = [Order.status, BooleanFilter("high_risk", show_counts=False)]


class EventView(ModelView[Event]):
    count_mode = CountMode.ESTIMATED
    list_filters = [
        ChoiceFilter(
            "kind",
            choices=[("signup", "Sign up"), ("order", "Order")],
            show_counts=True,
        ),
        Event.created_at,
    ]
```

## Write your own

A filter is a class that returns a condition. Here orders are grouped by whether they are late:

```python
from datetime import timedelta

from sqlalchemy import ColumnElement, func

from adminsite.filters import SQLAlchemyRepository, SQLFilter
from adminsite.filters import FilterContext, FilterOption, FilterValue


class DeliveryFilter(SQLFilter[Order]):
    """Orders past their delivery date, or due soon."""

    async def options(self, context: FilterContext) -> list[FilterOption]:
        return [FilterOption("late", "Overdue"), FilterOption("soon", "Due in 2 days")]

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Order]
    ) -> ColumnElement[bool]:
        if value.first == "late":
            return Order.due_at < func.now()
        return Order.due_at.between(func.now(), func.now() + timedelta(days=2))


class OrderView(ModelView[Order]):
    list_filters = [Order.status, DeliveryFilter("delivery", label="Delivery")]
```

`SQLFilter[Order]` filters orders. `value.first` is the option picked, and `value.values` holds
every one picked when the class sets `multiple = True`. `options` can count too:
`await context.count_by(self.path)` counts the records by the value at that path, following the
rule for [counts](#counts).

To change the statement itself, for example to add a join, override `apply` instead of
`condition`:

```python
from typing import Any

from sqlalchemy import Select, func, select


class BigSpenders(SQLFilter[Customer]):
    def apply(
        self,
        statement: Select[Any],
        value: FilterValue,
        repository: SQLAlchemyRepository[Customer],
    ) -> Select[Any]:
        spent = (
            select(Order.customer_id)
            .group_by(Order.customer_id)
            .having(func.sum(Order.total) > int(value.first))
        )
        return statement.where(Customer.id.in_(spent))
```

A filter that depends on who is asking comes from `get_list_filters(request)` instead of
`list_filters`. A filter on a field `can_access_field` keeps from the user is not offered to them,
as [Permissions](permissions.md#fields) explains.
