# Filters

The Filters button above the list opens every filter at once, in a drawer over the list. Each
applies as it is picked: ticking a box or choosing a range reloads the list behind the drawer,
which stays open. The ones in use show as chips under the toolbar, and the button counts them. A
chip opens the drawer at its filter, and its cross takes it off. On a phone the drawer covers the
screen, and the button at its foot says how many records match. The whole state lives in the URL,
so a filtered list can be bookmarked or sent to someone.

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
| relationship | `RelationFilter`, picking linked records by name | `?customer=3&customer=5` |
| text | `TextFilter`, matching part of the value | `?email=fischer` |

A relation filter is a picker, as a link on a form is: a box that searches the linked records as
you type, named as their own view names them, with the ones picked as chips in it. Picking one, or
letting one go, applies the filter, and its chip above the list names the records. They come
through the linked model's own view, so its scope and its permissions apply, and seeing the list is
enough to use it. The URL carries each record's key; the filter matches the linked model's primary
key unless it is given another column, as `RelationFilter("customer", key="code")`.

A column of a related model, such as `Link(Order.customer, Customer.region)`, filters through the
relation without adding a join, so rows are never duplicated. Its name in the URL is
`customer__region`.

Anyone can edit the URL, so a value a filter does not offer is left out, as if it was never
picked: a status that is not among the choices, or a key past what the column holds, such as
`?customer=99999999999` for an `Integer` key. With nothing left, the filter matches no records.
The list never fails on such a value, and nor does a record's page or the search.

A choice filter with more than 10 options, such as a list of countries, has a search box above
them. Typing narrows the options to those whose label holds what was typed, and the ones already
ticked stay in view, so they can be unticked.

## Counts

Choice and boolean filters show how many records each option matches. The counts follow the search
but not the other filters, so they stay steady while you pick. They are counted in a request of
their own as the drawer opens, so the list never waits for them, and a list nobody filters never
counts them. Opened again, the drawer keeps them until the search changes.

Counting reads every record the list may show, so a view that says its table is too big to count,
with a `count_mode` of `CountMode.ESTIMATED` or `CountMode.NONE`, shows no counts beside its filters
either. `show_counts` decides for one filter: `True` counts it whatever the view says, and `False`
never does.

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

`options` can count as well: `await context.count_by(self.path)` counts the records grouped by the
value at that path. It follows the rule for [counts](#counts), and gives none, without asking the
database, where the list shows none for the filter. Set `show_counts = True` on the class to count
it whatever the view says.

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
