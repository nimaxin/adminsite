# Filters

Filters sit in a panel above the list. The ones in use show as chips, and the whole state lives in
the URL, so a filtered list can be bookmarked or sent to someone.

## Built in

Name a column in `list_filters` and adminsite picks the filter that fits it:

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
| relationship | `RelationFilter`, by the linked record's key | `?customer=3` |
| text | `TextFilter`, matching part of the value | `?email=fischer` |

A column of a related model, such as `Link(Order.customer, Customer.region)`, filters through the
relation without adding a join, so rows are never duplicated. Its name in the URL is
`customer__region`.

A choice filter with more than 10 options, such as a list of countries, has a search box above
them. Typing narrows the options to those whose label holds what was typed, and the ones already
ticked stay in view, so they can be unticked.

## Counts

Choice and boolean filters show how many records each option matches. The counts follow the search
but not the other filters, so they stay steady while you pick. On a big table, turn them off:

```python
from adminsite.filters import ChoiceFilter


class OrderView(ModelView[Order]):
    list_filters = [
        ChoiceFilter(
            "status",
            choices=[("PAID", "Paid"), ("SHIPPED", "Shipped")],
            show_counts=False,
        ),
    ]
```

## Writing your own

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

`SQLFilter[Order]` is a filter of orders, so `repository` reads orders. `value.first` is the
chosen option, and `value.values` holds all of them when the filter allows
several (set `multiple = True` on the class).

When the filter needs to change the statement itself, for example to add a join, override `apply`
instead of `condition`:

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

A filter can also depend on who is asking: return it from `get_list_filters(request)` instead of
naming it in `list_filters`, and it is offered and applied for that request alone.

A filter on a field that `can_access_field` keeps from the user on the list is neither offered to
them nor applied when the URL names it, since its choices and their counts would give the field's
values away. See [Permissions](permissions.md#fields).

## One place for every read

The list, its count, a CSV export and a bulk action all go through the same filters. "Select all
12,408 matching" always means exactly the rows the list is showing.
