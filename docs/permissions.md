# Permissions

Permissions work at four levels: the view, the action, the field and the row. Each is a method on
the view, so the answer can depend on who is asking and on the record. The user is on
`request.state.user`, as your [auth provider](auth.md) loaded it.

The examples on this page use the [example models](models.md).

## Views and actions

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
question is about, or `None` for the view as a whole. The check runs before a page is shown and
again before anything is written, and buttons for what the user may not do are left out.

## Roles

With [`AdminUsers`](auth.md#users-and-roles-in-the-admin), a superuser ticks on the Roles page what
each role may do: View, Add, Edit, Delete, Export, Import and History on each view, View on each
page of your own, and each permission an action asks for of its own, such as
`@action("Mark as shipped", permission="ship")`. Each person may do whatever one of their roles
allows, and a superuser everything.

The roles come after the view's own settings, so a view with `can_delete = False` never deletes,
and an `allows` that ends with `super().allows(...)`, as above, keeps both. Rules about rows and
fields stay in code, where they can ask about a role:

```python
from starlette.requests import Request

from adminsite import Statement


class OrderView(ModelView[Order]):
    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        if request.state.user.has_role("Support"):
            return statement.where(Order.status == OrderStatus.PAID)
        return statement
```

### Roles from your own user table

An [auth provider](auth.md#your-own-user-table) of your own can answer the same way: its
[`grants_for`][adminsite.auth.AuthProvider.grants_for] returns the [`Grants`][adminsite.Grants] a
user's roles add up to, each view named as in its URL, and every view and page follows them.

```python
from adminsite import Grants
from adminsite.auth import AuthProvider


class StaffAuth(AuthProvider):
    async def grants_for(self, user: User) -> Grants | None:
        if user.is_staff:
            return Grants(everything=True)
        return Grants({"orders": {"view", "edit"}, "customers": {"view"}})
```

## History

`Permission.HISTORY` decides who reads a view's [audit log](audit.md): the History tab on its
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

The Activity page shows entries only from views where the user has both `VIEW` and `HISTORY`.

## Rows

`scope_query` narrows every read the view makes:

```python
from adminsite import Statement


class OrderView(ModelView[Order]):
    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.region == request.state.user.region)
```

`Statement` is the select the view hands over, so return it narrowed. The scope covers the list,
its counts, a record's page, the export, bulk actions and the history. A row outside it cannot be
seen, changed or deleted, and its URL answers "not found".

Other views follow it too. A picker for a customer reads through `CustomerView`, so it offers only
the customers this user may see, and an order whose customer the scope hides shows that customer
as "Hidden".

## Fields

`can_access_field` decides who sees which field, page by page, and `get_readonly_fields` who may
change it:

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
the path the field shows, such as `credit_limit` or `customer.email`.

A field refused on a page is left off it. A field refused on the list is also left out of the search
and the filters, so nobody finds records by a value kept from them. A filter of your own that
reads such a field is not covered, so leave it out in `get_list_filters`.

`get_readonly_fields` answers for one record, and `record` is `None` on the form for a new one. A
locked or refused field is ignored when the form comes back, even if someone adds the input back.

### Inline fields

The fields of an [inline](views.md#edit-related-records-in-the-same-form) are decided by the view
it belongs to. `can_access_field` gets each one by its path from that view, such as
`items.unit_price`, and `get_readonly_fields` locks one with a `Link`:

```python
class OrderView(ModelView[Order]):
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
        )
    ]

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        if field.name == "items.unit_price":
            return request.state.user.can_see_money
        return True

    def get_readonly_fields(
        self, request: Request, record: Order | None
    ) -> list[ColumnReference]:
        if record is not None and record.status == "shipped":
            return [Link(Order.items, OrderItem.quantity)]
        return []
```

`action` is `RequestAction.CREATE` for a new row, `EDIT` for an existing one and `DETAIL` on the
record page. `record` is the parent, so the lines of a shipped order are locked together.
