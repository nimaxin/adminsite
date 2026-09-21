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

## Options

| Option | What it does |
|---|---|
| `label` | The button text. Defaults to the method name, `mark_paid` reading "Mark paid". |
| `confirm` | A question asked in a dialog before it runs. |
| `inputs` | Fields to ask for. See above. |
| `dangerous` | Draws the button in red. |
| `permission` | What the user needs to run it. `Permission.EDIT` unless you say otherwise. |
| `name` | The name in the URL, if the method name will not do. |

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
