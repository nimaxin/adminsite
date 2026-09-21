# Permissions

Permissions work at four levels: the whole view, one action, one field, and one row. Each is a
method on the view, so the answer can depend on the request and on the record.

## The view and its actions

The simplest switches are attributes:

```python
class AuditedPayments(ModelView, model=Payment):
    can_create = False
    can_delete = False
```

For anything that depends on the user, override `allows`:

```python
from adminsite import Permission


class OrderView(ModelView, model=Order):
    async def allows(self, action, *, request=None, record=None):
        user = request.state.user
        if action == Permission.DELETE:
            return user.is_manager
        if action == Permission.EDIT and record is not None:
            return record.status != "shipped"
        return await super().allows(action, request=request, record=record)
```

`action` is one of `Permission.VIEW`, `CREATE`, `EDIT`, `DELETE` and `EXPORT`, or the permission an
[action](actions.md) asks for. The check runs before a page is shown and again before anything is
written, so a refused user gets an error even if they post the form by hand. Buttons for things the
user may not do are left out.

## Rows

`scope_query` narrows every read the view makes:

```python
class OrderView(ModelView, model=Order):
    def scope_query(self, statement, *, request=None):
        return statement.where(Order.region == request.state.user.region)
```

It is applied to the list, its count, opening one record, the CSV export and bulk actions. A row
outside the scope cannot be seen, opened, changed or deleted, and guessing its key in the URL gives
a "not found". There is no path through the admin that forgets to check.

## Fields

Hide a column or lock a field for some users with the `get_` methods:

```python
class CustomerView(ModelView, model=Customer):
    list_display = ("name", "email", "credit_limit")

    def get_list_display(self, request=None):
        if not request.state.user.can_see_money:
            return ("name", "email")
        return super().get_list_display(request)

    def get_readonly_fields(self, request=None, record=None):
        if not request.state.user.is_manager:
            return ("credit_limit",)
        return ()
```

A locked field is ignored when the form comes back, so adding the input back with the browser's
developer tools changes nothing.

## Who is asking

When signing in is set up, the user is on `request.scope["user_record"]`, as your
[auth provider](auth.md) loaded it. If your application already puts the user on `request.state`
with its own middleware, use that instead; adminsite passes the same request through.
