# Actions

An action is a button that runs your code on the rows people tick. Mark a method with `@action`:

```python
from adminsite.actions import Selection, action


class OrderView(ModelView[Order]):
    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def ship(self, selection: Selection[Order]) -> str:
        changed = await selection.update(status=OrderStatus.SHIPPED)
        return f"{changed} orders marked as shipped."
```

Ticking rows turns on the Actions menu above the list. What the method returns is shown once it
has run.

The examples on this page use the [example models](models.md).

## Every row that matches

Ticking every row on the page offers to select every row the search and filters match, on all
pages. A `Selection` is a query, not a list of records, so an action over thousands of rows is
still one statement:

- `await selection.update(**values)` and `await selection.delete()` change or delete every row and
  say how many.
- `await selection.count()` counts them.
- `await selection.records(paths=[Order.customer])` loads them, for work that needs each one.
- `selection.statement()` is a `select()` of their keys, for queries of your own.

`update` and `delete` never load the records, so they skip the save hooks. Use `records()` when the
hooks matter. A delete leaves cascades to the database, so give a child table's foreign key
`ondelete="CASCADE"` where children should go with their parent.

## One record, or the whole view

`on` says what an action is about, and what its method gets:

| `on` | Where it appears | The method gets |
|---|---|---|
| `"selection"`, the default | in the Actions menu, once rows are ticked | a `Selection[Order]` |
| `"record"` | in each row's menu, and on the record's page | the record, in a parameter typed `Order` |
| `"view"` | above the list, with nothing ticked | nothing in particular |

### On one record

```python
class InvoiceView(ModelView[Invoice]):
    @action("Confirm", on="record", confirm="Confirm this invoice?")
    async def confirm(self, invoice: Invoice) -> str:
        invoice.status = "confirmed"
        return f"Invoice {invoice.number} confirmed."
```

`allows(action, record=record)` decides for each record whether the action is offered, so refusing
it for a paid invoice hides Confirm on that row.

### On the whole view

Some work is about the table, not any row, such as fetching new orders from another system:

```python
class OrderView(ModelView[Order]):
    @action("Sync from the provider", on="view", permission=Permission.VIEW)
    async def sync(self, session: SessionAdapter) -> str:
        return f"{await fetch_new_orders(session)} orders fetched."
```

## Ask for values first

Typed parameters are asked for in a dialog before the action runs. They are checked like form
fields and reach the method as their types say:

```python
from typing import Annotated, Literal

from adminsite.actions import Input, Selection, action


class OrderView(ModelView[Order]):
    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def ship(
        self,
        selection: Selection[Order],
        *,
        carrier: Literal["DHL", "UPS", "PostNL"],
        tracking: Annotated[str | None, Input(label="Tracking number")] = None,
    ) -> str:
        changed = await selection.update(
            status=OrderStatus.SHIPPED, carrier=carrier, tracking=tracking
        )
        return f"{changed} orders sent with {carrier}."
```

| Type | Asked for with |
|---|---|
| `str` | a line of text, or a box with `Input(multiline=True)` |
| `int`, `float`, `Decimal` | a number |
| `bool` | a switch |
| `date`, `datetime`, `time` | a date or time picker |
| `UUID` | a line of text |
| an `Enum`, or `Literal["DHL", "UPS"]` | a select |
| a `list` of an `Enum` or a `Literal` | a select holding several |
| a model, such as `Product` | [a record to pick](#another-record) |
| `list[Product]` | several records |
| `UploadFile` | [a file](#a-file) |
| a dataclass | [its fields, under one heading](#a-group-of-values) |

A value is required unless its type allows `None` or it has a default, which fills the input when
the dialog opens. `Annotated[..., Input(...)]` sets an input's `label`, `help_text` and more, as
[`Input`][adminsite.actions.Input] says. With the [audit log](audit.md#actions) on, the values are
written down with the action, and a secret, such as an input named `password` or given
`secret=True`, is kept as `***`.

### What the method gets

A parameter of one of these types is handed over instead of asked for:

| Type | What it gets |
|---|---|
| `Selection[Order]` | the rows ticked, for an action on the selection |
| the view's model, such as `Order` | the record, for an [action on one record](#on-one-record) |
| `Request` | the request, to read who is signed in |
| `SessionAdapter` | adminsite's session, the same for a sync or an async database |
| `AsyncSession` | SQLAlchemy's own session, when the database is async |

The session is the one the action runs in, so what the method writes is committed with it, or
rolled back when it fails.

### A group of values

A dataclass is asked for field by field, under one heading, and reaches the method as the
dataclass:

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PriceChange:
    percent: Annotated[Decimal, Input(help_text="Negative to lower prices.")]
    never_below_cost: bool = True


class ProductView(ModelView[Product]):
    @action("Change prices")
    async def change_prices(
        self, selection: Selection[Product], *, change: PriceChange
    ) -> str:
        products = await selection.records()
        for product in products:
            price = product.price * (100 + change.percent) / 100
            if change.never_below_cost:
                price = max(price, product.cost)
            product.price = round(price, 2)
        return f"{len(products)} prices changed."
```

### A file

A parameter typed `UploadFile` asks for a file, and the method gets it as it was sent:

```python
from starlette.datastructures import UploadFile


class ProductView(ModelView[Product]):
    @action("Import a price list", on="view")
    async def import_prices(
        self,
        session: SessionAdapter,
        *,
        prices: Annotated[UploadFile, Input(accept=".csv", max_size=1024 * 1024)],
    ) -> str:
        rows = (await prices.read()).decode().splitlines()
        ...
        return f"{len(rows)} prices read from {prices.filename}."
```

A file larger than `max_size`, 10 MB unless you say, or of a type `accept` does not name, is
refused before the method runs. Nothing stores the file, and the audit log keeps only its name,
type and size.

### Another record

A parameter typed with a model asks for one of its records, and the method gets the record:

```python
class RunView(ModelView[Run]):
    @action("Assign to product")
    async def assign(self, selection: Selection[Run], *, product: Product) -> str:
        changed = await selection.update(product_id=product.id)
        return f"{changed} runs now sell as {product.name}."
```

The dialog offers the records the model's view lets this user see, and a record they could not
have picked is refused. `list[Product]` asks for several.

### Choices that depend on the user

The inputs are read when the page is drawn, so `get_actions` can hand back an action with the
choices this user may pick:

```python
import dataclasses
from collections.abc import Sequence

from starlette.requests import Request

from adminsite.actions import Action
from adminsite.fields import EnumField


class OrderView(ModelView[Order]):
    @action("Move")
    async def move(self, selection: Selection[Order], *, warehouse: str) -> str:
        changed = await selection.update(warehouse=warehouse)
        return f"{changed} orders moved to {warehouse}."

    def get_actions(self, request: Request) -> Sequence[Action]:
        choices = warehouses_for(request.state.user)
        return [
            dataclasses.replace(item, inputs=(EnumField("warehouse", choices=choices),))
            if item.name == "move"
            else item
            for item in super().get_actions(request)
        ]
```

The field takes the place of the input with its name. The run goes through `get_actions` too, so a
value that was never on offer is refused.

## Refuse an action

Raise `RefusedError` to stop an action with a message. Everything it did is rolled back:

```python
from adminsite import RefusedError


class OrderView(ModelView[Order]):
    @action("Refund")
    async def refund(self, selection: Selection[Order]) -> str:
        if await selection.count() > 50:
            raise RefusedError("Refund at most 50 orders at a time.")
        changed = await selection.update(status=OrderStatus.REFUNDED)
        return f"{changed} orders refunded."
```

An action the database refuses, such as deleting customers that orders still point at, is rolled
back and reported the same way.

## Answer with more than a line

An action's message shows under the page's heading until it is closed. When the person has to
follow a link or copy something, return a `Message`:

```python
from adminsite import Message


class AccountView(ModelView[Account]):
    @action("Rotate the key", on="record", audit_answer=False)
    async def rotate(self, account: Account, session: SessionAdapter) -> Message:
        key = await issue_key(session, account)
        return Message(
            "The new key is ready. Copy it now: it is not shown again.", copy=key
        )

    @action("Export all", on="view")
    async def export_all(self, session: SessionAdapter) -> Message:
        await queue_export(session)
        return Message(
            "The export is on its way.",
            link="/admin/exports",
            link_text="See the exports",
        )
```

`link` and `link_text` add an address to follow, and `copy` adds a value to copy, which the audit
log never keeps. The text is escaped, so return `Html(...)` for markup of your own.

## Answer with a file

An action can return a response, which is how a download works:

```python
from starlette.responses import Response


class OrderView(ModelView[Order]):
    @action("Download as CSV", on="record", permission=Permission.EXPORT)
    async def download(self, order: Order) -> Response:
        filename = f"order-{order.id}.csv"
        return Response(
            render_csv(order),
            media_type="text/csv",
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )
```

Anything Starlette can answer with works, such as a file, JSON or a redirect. The transaction is
committed first.

## Options

`@action` takes the button's `label`, an `icon`, a `confirm` question, `inputs` to ask for as they
are, `dangerous` for a red button listed last, the `permission` it needs, its `name` in the URL,
and `audit_answer` to keep a secret answer out of the [audit log](audit.md#actions). A `permission`
of its own, such as `"ship"`, gets a box of its own on each [role](permissions.md#roles).
[`@action`][adminsite.actions.action] says what each one does. An `icon` takes what a view's
[`icon`](views.md#the-sidebar) takes:

```python
class OrderView(ModelView[Order]):
    @action("Send the invoice", on="record", icon="icons/invoice.svg")
    async def send_invoice(self, record: Order) -> str:
        return f"Invoice for order {record.id} sent."
```

## Delete the chosen rows

Every view that allows deleting offers Delete in the Actions menu. It deletes each record as a
single delete does, so `allows`, `before_delete` and `after_delete` run for every one, and the audit
log gets an entry for each. It is all or none: when one record is refused, nothing is deleted. One
Delete removes at most 1,000 records.

`can_delete_selected = False` switches it off, and `can_delete = False` switches off every delete.

## Export

Every list has an Export CSV button. It exports every row the search and filters match, with the
values the list shows, and streams them so a large export never has to fit in memory.
