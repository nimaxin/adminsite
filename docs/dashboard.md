# Dashboard

The overview page is a grid of cards. Without any setup it shows how many records each view holds.
Pass `dashboard` to choose the cards:

```python
from sqlalchemy import func, select

from adminsite import Admin, Chart, RecentRecords, Stat

order_day = func.date(Order.created_at)

admin = Admin(
    engine,
    views=[OrderView, CustomerView],
    dashboard=[
        Stat("Revenue", select(func.sum(Order.total)), format="€{:,.2f}"),
        Stat("Waiting to ship", select(func.count()).where(Order.status == "paid"),
             link="orders?status=PAID"),
        Chart("Revenue per day",
              select(order_day, func.sum(Order.total)).group_by(order_day).order_by(order_day),
              format="€{:,.2f}"),
        RecentRecords("Latest orders", "orders", sort="-created_at", detail="total"),
    ],
)
```

Cards sit four to a row on a wide screen and one to a row on a phone. Each has a `width` from 1 to
4; stats take one column, charts and lists two.

## Where the numbers come from

A card reads either a SQLAlchemy `select`, or an async function that receives the session and
returns the value:

```python
async def active_customers(session):
    return await session.scalar(select(func.count()).where(Customer.is_active))


Stat("Active customers", active_customers)
```

The function is the way to reach anything else: another database, an API, a cache.

## The cards

### Stat

A single number.

| Option | What it does |
|---|---|
| `format` | How the value is written, such as `"{:,}"` or `"€{:,.2f}"`. |
| `previous` | A second source for the period before. The card then shows the change, such as "+12% on the period before", in green or red. |
| `hint` | A line of text under the number, when there is no change to show. |
| `link` | Where clicking the card goes. A relative link starts at the admin, so `"orders?status=PAID"` opens the filtered list. |

### Chart

Bars or a line over rows of a label and a value. `kind` is `"bar"` or `"line"`. The chart is drawn
as plain SVG on the server, so no chart library is loaded. Hovering a bar shows its value, and
screen readers get the same numbers as a table.

Dates as labels are written short, such as "Sep 14".

### RecentRecords

The latest records of a view, each linking to its page. It reads through the view, so the view's
`scope_query` applies and the card is hidden from users who may not open the view.

| Option | What it does |
|---|---|
| `sort` | The order, such as `"-created_at"`. The view's own ordering if left out. |
| `detail` | A path shown at the right of each record, such as `"total"`. |
| `limit` | How many records. Five unless you say. |

### ModelCounts

The record counts of every view the user may open, within its scope. It is the whole overview when
you set no dashboard, and you can put it among your own cards.

## Cards of your own

Subclass `Widget`, give it a template from your `template_dirs`, and return what it needs from
`load`. The template gets `widget` and `data`:

```python
from adminsite import Widget


class Weather(Widget):
    title = "Weather at the warehouse"
    template = "cards/weather.html"
    width = 2

    async def load(self, admin, request):
        return await fetch_weather("Rotterdam")
```

```jinja
<div class="card h-full border border-base-300">
  <div class="card-body p-4">
    <h2 class="text-sm font-medium text-base-content/70">{{ widget.title }}</h2>
    <p class="text-2xl">{{ data.temperature }}°</p>
  </div>
</div>
```

## Who sees what

Every card has `allows(admin, request)`. Override it to show a card only to some users:

```python
class Margin(Stat):
    async def allows(self, admin, request):
        return request.state.user.is_manager
```

A card whose query fails shows "This card could not be loaded" and the error goes to the
`adminsite` logger. The rest of the overview still loads.
