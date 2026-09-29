# Hooks

Hooks run around a save or a delete. Most run inside the same transaction: they receive the
session, so a business rule can read or write other tables, and they can refuse the change. Two
run once the change has committed, for work such as an email that must only go out for a change
that was kept.

```python
from adminsite import DeleteContext, ModelView, RefusedError, SaveContext


class OrderView(ModelView[Order]):
    async def before_save(self, context: SaveContext[Order]) -> None:
        if context.created:
            product = context.values[Order.product].get()
            stock = await context.session.get(Stock, product.id)
            if stock is None or stock.quantity < 1:
                raise RefusedError("That product is out of stock.", field=Order.product)

    async def after_save(self, context: SaveContext[Order]) -> None:
        await context.session.add(Notification(order_id=context.record.id))

    async def after_save_committed(self, context: SaveContext[Order]) -> None:
        if context.created:
            await send_confirmation(context.record)

    async def before_delete(self, context: DeleteContext[Order]) -> None:
        if context.record.status == "shipped":
            raise RefusedError("A shipped order cannot be deleted.")
```

| Hook | When it runs |
|---|---|
| `before_save` | Before the submitted values are written onto the record. |
| `after_save` | After the flush, so the record has its primary key, and before the commit. |
| `after_save_committed` | Once the save has committed. |
| `before_delete` | Before the record is deleted. |
| `after_delete` | After the delete is flushed, before the commit. |
| `after_delete_committed` | Once the delete has committed. |

## What a hook receives

`SaveContext[Order]` is the context of an order's save, so `context.record` is an `Order` and each
value has its column's type. It has:

| Attribute | What it holds |
|---|---|
| `session` | The session, for reading and writing other tables. |
| `record` | The record being saved. On create, a new instance. |
| `values` | The values the save stores, each read and changed by its column. |
| `created` | Whether this is a new record. |
| `request` | The request, to see who is asking. None when the save was not made from one. |

`DeleteContext[Order]` has `session`, `record` and `request`.

## Reading and changing a value

`context.values[Order.status]` is one column's value in the save. `get()` reads it, typed as the
column is, and `set()` stores another value in its place:

```python
class ProductView(ModelView[Product]):
    async def before_save(self, context: SaveContext[Product]) -> None:
        name = context.values[Product.name].get()
        context.values[Product.slug].set(slugify(name))
```

`get()` gives the value the column has once saved: the one the save stores, or the record's own
when the save leaves the column as it is, such as a read-only field. `Product.name in
context.values` tells whether the save stores a value for that column at all. A value the record
does not hold yet, such as a relation it was loaded without, is refused by name rather than read
with a query behind your back: put it in the view's fields, or read it with `context.session`.

`before_save` runs before the values are written onto the record, so that is where to change them.
Whatever the hook leaves in `context.values` is what is stored, so the database never sees the
untransformed value and a unique check fires against the value you meant. Setting an attribute on
`context.record` here would be overwritten a moment later by the value from the form; set the
attribute in `after_save` only for things the form does not send.

A string names a column too, `context.values["slug"]`, and so do the values of
[inputs that are not columns](fields.md#inputs-that-are-not-columns), such as a password to hash:
`context.values["password"].get()`. They are never stored on the record: the hooks store them where
they belong. A name that is neither a column nor such an input is refused, so a typo does not read
as an empty value.

## Refusing

Raise `RefusedError` with a message and the whole change is rolled back, including anything the
hook already wrote. The message is shown on the form, or above the list for a delete.

Any other exception is treated as a fault: the change is rolled back, and the error is reported as
one, so a bug in a hook is never mistaken for a business rule.

## Refusing one field

A refusal naming a field appears next to that input, like any other problem with what was typed,
and everything else the person wrote stays in place:

```python
async def before_save(self, context: SaveContext[Check]) -> None:
    delay = context.values[Check.validation_delay].get()
    if delay is not None and delay < context.record.check_delay:
        raise RefusedError(
            "Keep this above the check delay.", field=Check.validation_delay
        )
```

The field is named by its attribute, by `Link(...)` for a column of a related model, or by its name
as a string. Without a field the message sits above the form, as before. The [JSON API](api.md)
answers 422 with `{"errors": {"validation_delay": "..."}}` for a refusal about a field, and 409 for
the rest.

## Once the change is committed

`after_save_committed` and `after_delete_committed` run after the commit, so they only ever see a
change that was kept. That is the place for anything outside the database: an email, a webhook, a
cache to clear. A save that is refused or fails never reaches them.

The change is stored by then, so these hooks cannot undo it. An error in one is written to the
server's log, under `adminsite`, and the save or delete still succeeds. The transaction is over
too: read from `context.record`, and write through a session of your own.

When a bulk action deletes the chosen rows, each record's `after_delete_committed` runs once all of
them are gone, and none runs if one of them was refused.

## The session in a hook

The session is adminsite's wrapper, the same for async and sync engines, so a hook is written once:

```python
await context.session.add(record)
await context.session.get(Model, key)
await context.session.scalar(select(func.count()).select_from(Model))
```

For work that needs a plain SQLAlchemy `Session`, such as lazy loading, use `run`:

```python
def count_lines(session):
    order = session.get(Order, key)
    return len(order.items)


total = await context.session.run(count_lines)
```
