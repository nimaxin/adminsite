# Actions

An action is a button that runs over the rows the user ticked. Mark a method with `@action`:

```python
from adminsite.actions import Selection, action


class OrderView(ModelView[Order]):
    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def ship(self, selection: Selection[Order]) -> str:
        changed = await selection.update(status=OrderStatus.SHIPPED)
        return f"{changed} orders marked as shipped."
```

Ticking rows shows a bar with the view's actions. What the method returns is shown to the user
once it has run.

## Every row that matches

Ticking every row on the page offers "Select all 12,408 matching". The selection then covers every
row the current search and filters match, not only the page.

A `Selection` is a query, not a list of records, so an action over twelve thousand rows is still
one statement. It gives you:

| Method | What it does |
|---|---|
| `await selection.update(**values)` | Changes every covered row in one `UPDATE`, and returns how many. |
| `await selection.delete()` | Deletes every covered row in one `DELETE`, and returns how many. |
| `await selection.count()` | How many rows it covers. |
| `await selection.records(paths=[Order.customer])` | Loads the records, for work that needs each one, with the links `paths` names. A `Selection[Order]` loads orders. |
| `selection.statement()` | A `select()` of the covered primary keys, to use in your own queries. |

`update` and `delete` never load the records, so they skip the save hooks, and a delete relies on
the database for cascades. Give a child table's foreign key `ondelete="CASCADE"` where children
should go with their parent. Use `records()` when the hooks matter.

## Deleting the chosen rows

Every view that allows deleting offers Delete in the bar that rises when rows are ticked, after
asking to confirm. It deletes each record the way a single delete does: `allows(Permission.DELETE,
record=...)`, `before_delete` and `after_delete` run for every one, and the audit log gets a
delete entry for each.

It is all or none. When one record is refused, by a hook, by a permission, or because other records
still point at it, nothing is deleted and the message names that record. When the database refuses
something else, such as a row a hook wrote, nothing is deleted and the message says Delete was not
done, with the likely causes. One Delete removes at most 1,000 records; narrow the list first for
more.

Switch it off for a view with `can_delete_selected = False`, and add an action of your own where the
view needs a different one. `can_delete = False` removes it along with every other delete.

## Asking for values first

An action asks for values with typed parameters. They appear in a dialog before it runs, are checked
like form fields, and reach the method as the types say:

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

The type picks the input:

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

A value is required unless its type allows None or it has a default. A default fills the input when
the dialog opens, so a switch that is usually on opens on, and an input left empty takes it, unless
its type allows None: `copies: int | None = 2` opens on 2, and emptied it is None. A missing or
invalid value stops the action and tells the user which input and why, naming the group for a
field of one.

A `Literal`'s options read as they are written, `"DHL"` as DHL, and one in lower case as words:
`"next_day"` reads "Next day".

`Annotated[..., Input(...)]` words an input:

| Option | What it does |
|---|---|
| `label` | The text above the input. Defaults to the parameter's name, `tracking_number` reading "Tracking number". |
| `help_text` | A line under the input. |
| `multiline` | A box of several lines, for a `str`. |
| `accept`, `max_size` | For an `UploadFile`: the types offered, such as `".csv"`, and the largest file, in bytes. |
| `secret` | Keeps the value out of the [audit log](audit.md#actions), for one not named like a password. |

A type adminsite cannot ask for, such as a class of your own, stops the admin when it starts, with
the parameter's name and the types it can ask for. So does an option the type has no use for, such
as `multiline` on an `int`. The names `keys`, `everything` and `_csrf` are taken by the action form
itself.

With the [audit log](audit.md#actions) on, the values an action was run with are written down with
it. A secret is kept as `***`: an input named like `password` or `api_key`, or one given
`secret=True`.

### What the method is handed

A parameter of one of these types is handed over instead of asked for:

| Type | What it gets |
|---|---|
| `Selection[Order]` | the rows the user ticked, for an action on the selection |
| the view's model, such as `Order` | the record, for an [action on one record](#on-one-record) |
| `Request` | the request, to read who is signed in or the session |
| `SessionAdapter` | adminsite's session, with a sync or an async database alike |
| `AsyncSession` | SQLAlchemy's own session, where the database is async |

A `| None` changes nothing here: `request: Request | None` is handed the request just the same.

The session is the one the action runs in, so what the method writes is committed with it, or rolled
back when it fails. Asking for an `AsyncSession` when the database is not async stops the admin when
it starts.

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

The heading is the parameter's name, "Change", or the `label` of an `Input` on it. Each field is
sent as `change.percent`, the name the [JSON API](api.md#actions) takes too. An `InitVar` is asked
for like a field and handed to `__post_init__`. A dataclass inside a dataclass is asked for with a
parameter of its own.

### A file

A parameter typed `UploadFile` asks for a file, and the method receives it as it was sent. Nothing
stores it:

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

A file over `max_size`, 10 MB unless you say, or of a type `accept` does not name, is refused
before the method runs. The audit log keeps the file's name, type and size, never what is in it.

### Another record

A parameter typed with a model asks for one of its records. The dialog offers the records that
model's view lets this user see: a list while there are a hundred or fewer, and a search box above
that. The list is read when the page is drawn, so a product added a minute ago is on offer at once.
The method receives the record itself:

```python
class RunView(ModelView[Run]):
    @action("Assign to product")
    async def assign(self, selection: Selection[Run], *, product: Product) -> str:
        changed = await selection.update(product_id=product.id)
        return f"{changed} runs now sell as {product.name}."
```

The key that comes back is read through the product view, with its `scope_query` and its
permissions, so a record this user could not have picked is refused. `list[Product]` asks for
several, and the method receives a list. The audit log names the chosen record, and the
[JSON API](api.md#actions) takes its key: `{"inputs": {"product": "12"}}`.

### Choices worked out per request

Choices that are records are asked for with the model's type, above. For any other kind, the inputs
are read when the page is drawn, so `get_actions` can hand back an action carrying whatever this
user may pick:

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

The field takes the place of the input of its name, and its value still reaches `warehouse`. The
method's other parameters are still asked for as their types say, and so are those of an `Action`
that `get_actions` builds by hand. A copy under another name, `dataclasses.replace(item,
name="move_now", label="Move now")`, asks for what the action asks for, groups and defaults
included. The run goes through `get_actions` as well, so a value that was never on offer is refused
rather than accepted because the class said so.

## Options

| Option | What it does |
|---|---|
| `label` | The button text. Defaults to the method name, `mark_paid` reading "Mark paid". |
| `confirm` | A question asked in a dialog before it runs. |
| `inputs` | Fields to ask for as they are, each passed to the parameter of its name. The parameters' types usually say enough. |
| `dangerous` | Draws the button in red. |
| `permission` | What the user needs to run it. `Permission.EDIT` unless you say otherwise. |
| `name` | The name in the URL, if the method name will not do. |
| `audit_answer` | `False` keeps the answer out of the [audit log](audit.md#actions), for one that holds a secret shown once. |

## Refusing

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

An action that breaks a database constraint, for example deleting customers that orders still
point at, is rolled back and reported the same way.

## Exporting

Every list has an "Export CSV" button. It exports every row the search and filters match, not just
the page, using the values the list shows, and streams them in batches, so a large export never has
to fit in memory.

## What an action acts on

`on` says what an action is about, and what its method is handed:

| `on` | Where it appears | The method is handed |
|---|---|---|
| `"selection"`, the default | above the list, once rows are ticked | a `Selection[Order]` |
| `"record"` | in each row's menu, and on the record's page | the record, to a parameter typed `Order` |
| `"view"` | above the list, with nothing ticked | nothing in particular |

Each can also ask for the request or a session, as [above](#what-the-method-is-handed).

### On one record

```python
class InvoiceView(ModelView[Invoice]):
    @action("Confirm", on="record", confirm="Confirm this invoice?")
    async def confirm(self, invoice: Invoice) -> str:
        invoice.status = "confirmed"
        return f"Invoice {invoice.number} confirmed."
```

The record goes to the first parameter typed `Invoice`, written before the `*` or after it.

The button sits on the row and on the record's page, and the record's own permission decides
whether it is offered: `allows(action, record=record)` refusing a paid invoice hides Confirm for
that row and refuses the request if someone posts it anyway. A view with several record actions
collects them into a menu on the row. Afterwards the person lands back on the record.

Everything a selection action has is here too: a confirmation, inputs asked in a dialog, a
`dangerous` style, and an entry in the record's [history](audit.md).

### On the whole view

Some work is about the table, not about any row: fetching from another system, importing from an
API, sending a summary. Those need nothing ticked:

```python
class OrderView(ModelView[Order]):
    @action("Sync from the provider", on="view", permission=Permission.VIEW)
    async def sync(self, session: SessionAdapter) -> str:
        return f"{await fetch_new_orders(session)} orders fetched."
```

## Answering with more than a line

A message that all went well fades after a few seconds. When the person has to read, follow or copy
something, return a `Message` instead of a string:

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

| Option | What it does |
|---|---|
| `sticky` | Keeps the message on screen until it is closed. |
| `link`, `link_text` | An address to follow, written after the text. |
| `copy` | A value shown with a button that copies it. The message stays, and the audit log never keeps the value. |

The text is escaped. Return `Html(...)`, or give it as the message's text, for markup of your own.
The JSON API answers with the message, the link and the value to copy as fields of its reply.

## Answering with a file

An action can return a response instead of a message, which is how a download works:

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

Anything Starlette can answer with works: a file, JSON, or a redirect to somewhere the result
waits. The transaction is committed first, so the answer describes work that really happened. The
[JSON API](api.md#actions) sends the response as it is too.

