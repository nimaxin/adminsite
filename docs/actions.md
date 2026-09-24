# Actions

An action is a button that runs over the rows the user ticked. Mark a method with `@action`:

```python
from adminsite.actions import Selection, action


class OrderView(ModelView, model=Order):
    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def ship(self, selection: Selection) -> str:
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
| `await selection.records()` | Loads the records, for work that needs each one. |
| `selection.statement()` | A `select()` of the covered primary keys, to use in your own queries. |

`update` and `delete` never load the records, so they skip the save hooks, and a delete relies on
the database for cascades. Give a child table's foreign key `ondelete="CASCADE"` where children
should go with their parent. Use `records()` when the hooks matter.

## Asking for values first

An action can ask for values before it runs. They appear in a dialog, are checked like form fields,
and reach the method by name:

```python
from adminsite.fields import ChoiceField, StringField

CARRIERS = (("dhl", "DHL Express"), ("ups", "UPS"), ("postnl", "PostNL"))


class OrderView(ModelView, model=Order):
    @action(
        "Mark as shipped",
        confirm="Mark the chosen orders as shipped?",
        inputs=[
            ChoiceField("carrier", choices=CARRIERS, required=True),
            StringField("tracking", label="Tracking number", max_length=40),
        ],
    )
    async def ship(
        self, selection: Selection, carrier: str, tracking: str | None
    ) -> str:
        changed = await selection.update(
            status=OrderStatus.SHIPPED, carrier=carrier, tracking=tracking
        )
        return f"{changed} orders sent with {carrier}."
```

A missing or invalid value stops the action and tells the user which field and why. The names
`keys`, `everything` and `_csrf` are taken by the action form itself and cannot be used.

### Where the dialog starts

`default` says what an input holds when the dialog opens, so switches that are usually on open on
and nobody has to set them every time. `multiple=True` on a `ChoiceField` lets one input hold
several options, and the method receives a list:

```python
class OrderView(ModelView, model=Order):
    @action(
        "Download",
        inputs=[
            ChoiceField("kinds", choices=KINDS, multiple=True, default=("paper",)),
            BooleanField("with_totals", label="With totals", default=True),
        ],
    )
    async def download(
        self, selection: Selection, kinds: list[str], with_totals: bool
    ) -> Response: ...
```

`default` works on any field, and a form for a new record starts from it too. A stored value
always wins over it, so it never overwrites anything.

With the [audit log](audit.md#actions) on, the values an action was run with are written down with
it. A secret is kept as `***`: an input named like `password` or `api_key`, or one given
`secret=True`.

### Choices worked out per request

The inputs are read when the page is drawn, so `get_actions` can hand back an action carrying
whatever this user may pick:

```python
import dataclasses


class OrderView(ModelView, model=Order):
    @action("Move", inputs=[ChoiceField("warehouse", choices=())])
    async def move(self, selection: Selection, warehouse: str) -> str: ...

    def get_actions(self, request=None):
        choices = warehouses_for(request.user)
        return tuple(
            dataclasses.replace(
                item, inputs=(ChoiceField("warehouse", choices=choices),)
            )
            if item.name == "move"
            else item
            for item in super().get_actions(request)
        )
```

The run goes through `get_actions` as well, so a value that was never on offer is refused rather
than accepted because the class said so.

## Options

| Option | What it does |
|---|---|
| `label` | The button text. Defaults to the method name, `mark_paid` reading "Mark paid". |
| `confirm` | A question asked in a dialog before it runs. |
| `inputs` | Fields to ask for, each with an optional `default`. See above. |
| `dangerous` | Draws the button in red. |
| `permission` | What the user needs to run it. `Permission.EDIT` unless you say otherwise. |
| `name` | The name in the URL, if the method name will not do. |
| `audit_answer` | `False` keeps the answer out of the [audit log](audit.md#actions), for one that holds a secret shown once. |

## Refusing

Raise `RefusedError` to stop an action with a message. Everything it did is rolled back:

```python
from adminsite import RefusedError


@action("Refund")
async def refund(self, selection: Selection) -> str:
    if await selection.count() > 50:
        raise RefusedError("Refund at most 50 orders at a time.")
    ...
```

An action that breaks a database constraint, for example deleting customers that orders still
point at, is rolled back and reported the same way.

## Exporting

Every list has an "Export CSV" button. It exports every row the search and filters match, not just
the page, using the values the list shows, and streams them in batches, so a large export never has
to fit in memory.

## What an action acts on

`on` says what an action is about, and what its method is given:

| `on` | Where it appears | The method gets |
|---|---|---|
| `"selection"`, the default | above the list, once rows are ticked | a `Selection` |
| `"record"` | on each row, and on the record's page | `(record, session)` |
| `"view"` | above the list, with nothing ticked | `(session)` |

### On one record

```python
class InvoiceView(ModelView, model=Invoice):
    @action("Confirm", on="record", confirm="Confirm this invoice?")
    async def confirm(self, record: Invoice, session: SessionAdapter) -> str:
        record.status = "confirmed"
        return f"Invoice {record.number} confirmed."
```

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
class OrderView(ModelView, model=Order):
    @action("Sync from the provider", on="view", permission=Permission.VIEW)
    async def sync(self, session: SessionAdapter) -> str:
        return f"{await fetch_new_orders(session)} orders fetched."
```

## Answering with a file

An action can return a response instead of a message, which is how a download works:

```python
@action("Download as CSV", on="record", permission=Permission.EXPORT)
async def download(self, record: Order, session: SessionAdapter) -> Response:
    return Response(
        render_csv(record),
        media_type="text/csv",
        headers={
            "content-disposition": f'attachment; filename="order-{record.id}.csv"'
        },
    )
```

Anything Starlette can answer with works: a file, JSON, or a redirect to somewhere the result
waits. The transaction is committed first, so the answer describes work that really happened.

