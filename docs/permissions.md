# Permissions

Permissions work at four levels: the whole view, one action, one field, and one row. Each is a
method on the view, so the answer can depend on the request and on the record.

## The view and its actions

The simplest switches are attributes:

```python
class AuditedPayments(ModelView[Payment]):
    can_create = False
    can_delete = False
```

For anything that depends on the user, override `allows`:

```python
from starlette.requests import Request

from adminsite import Permission


class OrderView(ModelView[Order]):
    async def allows(
        self, action: Permission | str, *, request: Request, record: Order | None
    ) -> bool:
        user = request.state.user
        if action == Permission.DELETE:
            return user.is_manager
        if action == Permission.EDIT and record is not None:
            return record.status != "shipped"
        return await super().allows(action, request=request, record=record)
```

`action` is one of `Permission.VIEW`, `CREATE`, `EDIT`, `DELETE`, `VIEW_DETAIL`, `EXPORT`, `IMPORT`
and `HISTORY`, or the permission an [action](actions.md) asks for. `record` is the record the
question is about, or None when it is about the view as a whole, such as whether its list may be
exported. The check runs before a page is shown and again before anything is
written, so a refused user gets an error even if they post the form by hand. Buttons for things the
user may not do are left out.

A refused page shows "Not allowed" inside the admin with a 403, and views a user may not open are
left out of the sidebar.

## History

`Permission.HISTORY` decides who reads the [audit log](audit.md) of a view: the History tab on its
records, and its entries on the Activity page. The log keeps every field's old and new value, so
refuse it where the values are sensitive:

```python
class PayrollView(ModelView[Salary]):
    async def allows(
        self, action: Permission | str, *, request: Request, record: Salary | None
    ) -> bool:
        if action == Permission.HISTORY:
            return request.state.user.is_hr
        return await super().allows(action, request=request, record=record)
```

The Activity page shows only entries from views where the user has both `VIEW` and `HISTORY`, and
only from views registered on this admin, so two admins sharing one log never show each other's
entries. A user with no such view does not see the Activity page at all.

## Rows

`scope_query` narrows every read the view makes:

```python
from adminsite import Statement


class OrderView(ModelView[Order]):
    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.region == request.state.user.region)
```

`Statement` stands for the select the view hands over, so the hook returns that select narrowed.
Returning a new `select(Order)` in its place is a type error: it would drop what the view had
already asked for, such as the page's filters.

It is applied to the list, its count, opening one record, the CSV export and bulk actions. A row
outside the scope cannot be seen, opened, changed or deleted, and guessing its key in the URL gives
a "not found". There is no path through the admin that forgets to check.

That includes a picker on someone else's form. When an order links to a customer, the picker reads
through `CustomerView`, so it offers only the customers this user may see, and offers none at all
where they may not open the view. See [Fields](fields.md#links-to-many-records).

The same holds when the form comes back. A key sent for a link is resolved through the target's
own view, so a customer outside the user's scope cannot be attached by editing the form, and the
answer to a key the view will not give up is the same as to a key that does not exist: "Choose a
record." A model with no view of its own is loaded by key, since there is no view to ask.

Sorting follows it too. `?sort=` in the URL is honoured only for a column the user can read
somewhere on the view: one on offer in the list, on the record page or in the form. Sorting by
anything else, such as a column no page shows them, is ignored rather than putting the rows in the
order of a value the user cannot see.

## Fields

`can_access_field` decides who sees which field, page by page, and `get_readonly_fields` who
may change it:

```python
from starlette.requests import Request

from adminsite import BaseField, ColumnReference, RequestAction


class CustomerView(ModelView[Customer]):
    fields = [Customer.name, Customer.email, Customer.credit_limit]

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        if field.name == "credit_limit":
            return request.state.user.can_see_money
        return True

    def get_readonly_fields(
        self, request: Request, record: Customer | None
    ) -> list[ColumnReference]:
        if not request.state.user.is_manager:
            return [Customer.credit_limit]
        return []
```

`action` is the page the field is asked for: `RequestAction.LIST`, `DETAIL`, `CREATE`, `EDIT` or
`EXPORT`, so `action == RequestAction.EXPORT` keeps a column out of the CSV alone. `field.name` is
the path the field shows, such as `credit_limit` or `customer.email`. A field refused on a page is
left off it: the list and its Columns menu, the record page, the form and the export. The JSON API
carries what the list, the record page and the form show this user, and nobody can sort by a field
they see nowhere.

`get_readonly_fields` answers for one record too, such as the customer of a shipped order, and
`record` is None on the form for a new one. A field with `read_only=True` is locked for everyone.
A locked or refused field is ignored when the form comes back, so adding the input back with the
browser's developer tools changes nothing.

## Who is asking

When signing in is set up, the user is on `request.scope["user_record"]`, as your
[auth provider](auth.md) loaded it. If your application already puts the user on `request.state`
with its own middleware, use that instead; adminsite passes the same request through.
