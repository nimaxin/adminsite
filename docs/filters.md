# Filters

Filters sit in a panel above the list. The ones in use show as chips, and the whole state lives in
the URL, so a filtered list can be bookmarked or sent to someone.

## Built in

Name a path in `list_filter` and adminsite picks the filter that fits the column:

```python
class OrderView(ModelView, model=Order):
    list_filter = ("status", "total", "created_at", "customer", "customer.region")
```

| Column | Filter | In the URL |
|---|---|---|
| enum | `ChoiceFilter`, several values, with counts | `?status=PAID&status=SHIPPED` |
| `bool` | `BooleanFilter` | `?is_active=true` |
| number | `NumberRangeFilter`, either end optional | `?total=10,50` or `?total=100,` |
| date or datetime | `DateRangeFilter`, a shortcut or two dates | `?created_at=week` or `?created_at=2026-09-01,2026-09-30` |
| relationship | `RelationFilter`, by the linked record's key | `?customer=3` |
| text | `TextFilter`, matching part of the value | `?email=fischer` |

A dotted path such as `customer.region` filters through the link without adding a join, so rows
are never duplicated. Its name in the URL is `customer__region`.

## Counts

Choice and boolean filters show how many records each option matches. The counts follow the search
but not the other filters, so they stay steady while you pick. On a big table, turn them off:

```python
from adminsite.backends.sqlalchemy import ChoiceFilter


class OrderView(ModelView, model=Order):
    list_filter = (
        ChoiceFilter(
            "status",
            choices=(("PAID", "Paid"), ("SHIPPED", "Shipped")),
            show_counts=False,
        ),
    )
```

## Writing your own

A filter is a class that returns a condition. Here orders are grouped by whether they are late:

```python
from datetime import timedelta

from sqlalchemy import func

from adminsite.backends.sqlalchemy import SQLFilter
from adminsite.filters import FilterOption


class DeliveryFilter(SQLFilter):
    """Orders past their delivery date, or due soon."""

    async def options(self, context):
        return [FilterOption("late", "Overdue"), FilterOption("soon", "Due in 2 days")]

    def condition(self, value, repository):
        if value.first == "late":
            return Order.due_at < func.now()
        return Order.due_at.between(func.now(), func.now() + timedelta(days=2))


class OrderView(ModelView, model=Order):
    list_filter = ("status", DeliveryFilter("delivery", label="Delivery"))
```

`value.first` is the chosen option, and `value.values` holds all of them when the filter allows
several (set `multiple = True` on the class).

When the filter needs to change the statement itself, for example to add a join, override `apply`
instead of `condition`:

```python
from sqlalchemy import func, select


class BigSpenders(SQLFilter):
    def apply(self, statement, value, repository):
        spent = (
            select(Order.customer_id)
            .group_by(Order.customer_id)
            .having(func.sum(Order.total) > int(value.first))
        )
        return statement.where(Customer.id.in_(spent))
```

A filter can also depend on who is asking: return it from `get_filters(request)` instead of naming
it in `list_filter`, and it is offered and applied for that request alone.

## One place for every read

The list, its count, a CSV export and a bulk action all go through the same filters. "Select all
12,408 matching" always means exactly the rows the list is showing.
