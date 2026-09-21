# Hooks

Hooks run around a save or a delete, inside the same transaction. They receive the session, so a
business rule can read or write other tables, and they can refuse the change.

```python
from adminsite import RefusedError
from adminsite.views.writing import DeleteContext, SaveContext


class OrderView(ModelView, model=Order):
    async def before_save(self, context: SaveContext) -> None:
        if context.created:
            stock = await context.session.get(Stock, context.record.product_id)
            if stock is None or stock.quantity < 1:
                raise RefusedError("That product is out of stock.")

    async def after_save(self, context: SaveContext) -> None:
        await context.session.add(Notification(order_id=context.record.id))

    async def before_delete(self, context: DeleteContext) -> None:
        if context.record.status == "shipped":
            raise RefusedError("A shipped order cannot be deleted.")
```

| Hook | When it runs |
|---|---|
| `before_save` | Before the submitted values are written onto the record. |
| `after_save` | After the flush, so the record has its primary key, and before the commit. |
| `before_delete` | Before the record is deleted. |
| `after_delete` | After the delete is flushed, before the commit. |

## What a hook receives

`SaveContext` has:

| Attribute | What it holds |
|---|---|
| `session` | The session, for reading and writing other tables. |
| `record` | The record being saved. On create, a new instance. |
| `values` | The values that were submitted, already converted. |
| `created` | Whether this is a new record. |
| `request` | The request, to see who is asking. |

`DeleteContext` has `session`, `record` and `request`.

## Refusing

Raise `RefusedError` with a message and the whole change is rolled back, including anything the
hook already wrote. The message is shown on the form, or above the list for a delete.

Any other exception is treated as a fault: the change is rolled back, and the error is reported as
one, so a bug in a hook is never mistaken for a business rule.

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
