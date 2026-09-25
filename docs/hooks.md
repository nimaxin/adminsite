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

## Changing a value before it is stored

`before_save` runs before the submitted values are written onto the record, so that is where to
change them. Change `context.values`, or use `context.set`:

```python
class ProductView(ModelView, model=Product):
    async def before_save(self, context: SaveContext) -> None:
        name = context.values.get("name")
        if name:
            context.set("slug", slugify(name))
```

Whatever the hook leaves in `context.values` is what is stored, so the database never sees the
untransformed value and a unique check fires against the value you meant. Setting an attribute on
`context.record` here would be overwritten a moment later by the value from the form; set the
attribute in `after_save` only for things the form does not send.

The values of [inputs that are not columns](fields.md#inputs-that-are-not-columns), such as a
password to hash, are in `context.values` too. They are never stored on the record: the hooks store
them where they belong.

## Refusing one field

A refusal with a field name appears next to that input, like any other problem with what was
typed, and everything else the person wrote stays in place:

```python
async def before_save(self, context: SaveContext) -> None:
    delay = context.values.get("validation_delay")
    if delay is not None and delay < context.record.check_delay:
        raise RefusedError("Keep this above the check delay.", field="validation_delay")
```

Without a field the message sits above the form, as before. The [JSON API](api.md) answers 422
with `{"errors": {"validation_delay": "..."}}` for a refusal about a field, and 409 for the rest.

