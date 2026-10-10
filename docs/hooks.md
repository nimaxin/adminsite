# Hooks

Hooks run your code around a save or a delete. Most run inside the same transaction, so a business
rule can read and write other tables, and refuse the change. Two run once the change is committed,
for work such as an email that must only go out for a change that was kept.

```python
from adminsite import DeleteContext, ModelView, RefusedError, SaveContext


class OrderView(ModelView[Order]):
    async def before_save(self, context: SaveContext[Order]) -> None:
        if context.created:
            customer = context.values[Order.customer].get()
            if not customer.is_active:
                raise RefusedError(
                    "This customer's account is closed.", field=Order.customer
                )

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
| `after_save` | After the flush, so the record has its key and the values the database set, and before the commit. |
| `after_save_committed` | Once the save has committed. |
| `before_delete` | Before the record is deleted. |
| `after_delete` | After the delete is flushed, before the commit. |
| `after_delete_committed` | Once the delete has committed. |

## What a hook gets

`SaveContext[Order]` holds the `session`, the `record`, the `values` the save stores, whether the
save `created` a new record, and the `request`, as [`SaveContext`][adminsite.SaveContext] says.
The record is an `Order`, and each value has its column's type. `DeleteContext[Order]` holds the
`session`, the `record` and the `request`.

## Read and change a value

`context.values[Order.status]` is one column's value in the save. `get()` reads it, typed as the
column is, and `set()` stores another value in its place:

```python
class ProductView(ModelView[Product]):
    async def before_save(self, context: SaveContext[Product]) -> None:
        name = context.values[Product.name].get()
        context.values[Product.slug].set(slugify(name))
```

`get()` gives the value the save stores, or the record's own when the save leaves the column as it
is. On a new record, a column the database fills in is `None` until the insert, so read it in
`after_save`. `Product.name in context.values` tells whether the save stores that column at all.

Change values in `before_save`, before they are written onto the record. Setting an attribute on
`context.record` there would be overwritten by the form's value a moment later.

A string names a column too, `context.values["slug"]`, and so does the name of a
[form-only input](fields.md#form-only-inputs), such as `context.values["password"]`. A name that is
neither is refused, so a typo never reads as an empty value.

## Refuse a change

Raise `RefusedError` with a message, and the whole change is rolled back, including anything the
hook wrote. The message shows on the form, or above the list for a delete. Name a field, and the
message shows next to its input instead, with everything else that was typed kept:

```python
class CheckView(ModelView[Check]):
    async def before_save(self, context: SaveContext[Check]) -> None:
        delay = context.values[Check.validation_delay].get()
        if delay is not None and delay < context.values[Check.check_delay].get():
            raise RefusedError(
                "Keep this above the check delay.", field=Check.validation_delay
            )
```

Any other exception is a fault: the change is rolled back and reported as an error, so a bug in a
hook is never mistaken for a business rule.

## After the commit

`after_save_committed` and `after_delete_committed` run once the change is committed, so they only
see a change that was kept. They are the place for anything outside the database, such as an email,
a webhook or a cache to clear.

They cannot undo the change. An error in one is written to the server's log, under `adminsite`,
and the save still succeeds. The transaction is over too, so write through a session of your own.

## The session in a hook

The session is adminsite's wrapper, the same for async and sync engines, so a hook is written once:

```python
class OrderView(ModelView[Order]):
    async def after_save(self, context: SaveContext[Order]) -> None:
        order = context.record
        customer = await context.session.get(Customer, order.customer_id)
        orders = await context.session.scalar(
            select(func.count()).where(Order.customer_id == order.customer_id)
        )
        if customer is not None and orders == 1:
            await context.session.add(Notification(order_id=order.id))
```

For work that needs a plain SQLAlchemy `Session`, such as lazy loading, use `run`:

```python
from sqlalchemy.orm import Session


class OrderView(ModelView[Order]):
    async def before_delete(self, context: DeleteContext[Order]) -> None:
        def count_lines(session: Session) -> int:
            order = session.get(Order, context.record.id)
            return 0 if order is None else len(order.items)

        if await context.session.run(count_lines) > 0:
            raise RefusedError("Remove the order's lines first.")
```
