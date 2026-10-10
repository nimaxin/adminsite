# Views

A `ModelView` says how one model appears in the admin. The model is its type argument, class
attributes describe its pages, and methods whose names start with `get_` answer per request.

The examples on this page use the [example models](models.md).

```python
from adminsite import Descending, Field, Link, ModelView
from adminsite.fields import TextAreaField


class OrderView(ModelView[Order]):
    group = "Sales"
    record_title = "Order #{id}"

    fields = [
        Order.id,
        Order.customer,
        Link(Order.customer, Customer.email),
        Order.status,
        Field(Order.total, read_only=True),
        TextAreaField(Order.note, exclude_from_list=True),
        Field(
            Order.created_at,
            hidden_in_list=True,
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
    ]
    searchable_fields = [Order.id, Link(Order.customer, Customer.email)]
    sortable_fields = [Order.created_at, Order.total]
    fields_default_sort = [Descending(Order.created_at)]
    list_filters = [Order.status, Order.total, Order.created_at]
    page_size = 50
```

A view with no settings needs no class of its own: `Admin(engine, views=[OrderView, ModelView[Tag]])`.

!!! tip "ruff's RUF012"

    adminsite reads the settings once and never changes them, so RUF012, which asks for
    `ClassVar` on a list in a class body, can be switched off for the module that holds your views:

    ```toml
    [tool.ruff.lint.per-file-ignores]
    "app/admin.py" = ["RUF012"]
    ```

## Name a column

A setting names a column by its attribute, `Order.total`, or by its name as a string, `"total"`.
`Link(Order.customer, Customer.email)` names a column of a related model, which a string writes as
`"customer.email"`. Links nest to go further: `Link(OrderItem.order, Link(Order.customer,
Customer.name))`. Through a link to many records, such as `Link(Customer.orders, Order.total)`, the
column shows the value of every record it reaches.

A sort takes the same names, and `Descending(Order.created_at)`, or the string `"-created_at"`,
sorts newest first.

A type checker catches a misspelt attribute. A misspelt string stops the admin when it starts, with
a message that lists what the model has:

```text
OrderView.fields: Order has no column or relationship "totl". Its columns: id, customer_id,
status, total, note, created_at. Its relationships: customer, items.
```

## Choose the fields

`fields` lists the view's fields once, in order, and every page shows them in that order. An entry
is a column, or a field with options: `Field(Order.created_at, label="Placed")` keeps the field
adminsite picks for the column, and `TextAreaField(Order.note)` picks another kind. What the
options leave out comes from the column, such as its label and its length. See [Fields](fields.md).

With `fields` empty, the view shows every column, and a foreign key such as `customer_id` shows as
its relationship, `customer`.

A relationship such as `Order.customer` shows the linked record's name in the list and a picker in
the form. Forms leave out what nobody types: a related model's column, a computed field, and a key
the database fills in. A key people choose, such as a product code, is on the form for a new
record and read only after that.

### Leave a field off one page

A flag on the field or a list on the view leaves a field off one page. Both mean the same:

| Flag on a field | List on the view | Leaves the field off |
|---|---|---|
| `exclude_from_list` | `exclude_fields_from_list` | the list |
| `exclude_from_detail` | `exclude_fields_from_detail` | the record page |
| `exclude_from_create` | `exclude_fields_from_create` | the form for a new record |
| `exclude_from_edit` | `exclude_fields_from_edit` | the form for an existing record |
| `exclude_from_export` | `exclude_fields_from_export` | the CSV export |

```python
class ProductView(ModelView[Product]):
    fields = [Product.name, Product.price, Product.cost, Product.description]
    exclude_fields_from_list = [Product.description]
    exclude_fields_from_export = [Product.cost]
```

## Name the view and its records

`name` is the view's part of the URL. `label` and `label_plural` are what its records are called
in headings, buttons and the sidebar, and `group` is the sidebar section it sits under. Each one
left out comes from the model, as [`ModelView`][adminsite.ModelView] says.

`record_title` names a record wherever it appears, other models' pages included, such as
`"{name} ({email})"`. The names in braces are the model's columns. Without a `record_title`, a
record is named by its model's `__str__`, or else by the view's label and its key, as in
`Order #12`.

For a name no template can build, write `get_record_title`. It gets the record with its columns
loaded, but not its links. The record comes by position, so the parameter can be named after the
model:

```python
class SupplierView(ModelView[Supplier]):
    def get_record_title(self, supplier: Supplier, /) -> str:
        if supplier.closed_at is not None:
            return f"{supplier.name} (closed)"
        return supplier.name
```

## The list

[`ModelView`][adminsite.ModelView] lists every setting of the list, and [Filters](filters.md)
covers `list_filters`. The sections below show the rest at work.

### Search

`searchable_fields` lists the columns the search box looks in, and it finds the term anywhere
inside them. On a table of millions that reads every row, so `search_condition` can return a
condition of your own:

```python
import re

from sqlalchemy import ColumnElement
from starlette.requests import Request


class ContactView(ModelView[Contact]):
    searchable_fields = [Contact.phone, Contact.name]

    def search_condition(
        self, term: str, *, request: Request
    ) -> ColumnElement[bool] | None:
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 6:
            # "+1 (555) 0100" finds 15550100, through the index on phone.
            return Contact.phone == digits
        return None
```

Everything that searches the view uses the condition. Return `None` for the usual search, as above
for a name.

### Choose the columns

The Columns menu above the list hides and shows columns. A field with `hidden_in_list=True` starts
hidden:

```python
class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        Order.customer,
        Order.status,
        Order.total,
        Field(Order.note, hidden_in_list=True),
        Field(Order.created_at, hidden_in_list=True),
    ]
```

The choice is kept in the URL and in the session, and the CSV export follows it.

### Edit values in the list

A value that changes often, such as an order's status, can be changed straight from the list. Name
its field in `inline_editable_fields`:

```python
class OrderView(ModelView[Order]):
    fields = [Order.id, Order.customer, Order.status, Order.total, Order.note]
    inline_editable_fields = [Order.status, Order.note]
```

The cell opens the field's input from the edit form, and the save runs the same hooks and audit
log. A cell can be changed only where the edit form would let this user change it. The save holds
that one value, so `Order.note in context.values` tells a hook whether the note changed.

### Rows per page

`page_size` sets how many rows a page holds, and `page_size_options` adds a menu to change it:

```python
class OrderView(ModelView[Order]):
    page_size = 25
    page_size_options = [25, 100, 500]
```

### Refresh the list on its own

A view whose records come in while people watch, such as new orders, can read its rows again every
so often:

```python
class OrderView(ModelView[Order]):
    list_refresh_seconds = 30
```

New and changed records show up without a reload. The list waits while someone is working in it,
and a list in a tab nobody is looking at asks the database nothing.

### Saved views

A saved view keeps a search, filters, a sort and columns under a name, such as "Unpaid this month".
Switch them on for the admin:

```python
admin = Admin(engine, views=[OrderView], saved_views=True)
```

The list gets a Saved views menu. A saved view belongs to the person who saved it, who can share
it with everyone.

The views go to a SQLite file of their own, `adminsite_views.db`, as the [audit log](audit.md)
does. To keep them in your database instead:

```python
from adminsite import SavedViews
from adminsite.saved_views import saved_view_metadata

admin = Admin(engine, saved_views=SavedViews(engine))

# In your Alembic env.py, so the table is part of your migrations:
target_metadata = [Base.metadata, saved_view_metadata]
```

### The command palette

<kbd>Ctrl</kbd>+<kbd>K</kbd>, or <kbd>Cmd</kbd>+<kbd>K</kbd> on a Mac, jumps to any page or record.
It searches the `searchable_fields` of each view the user may open. Leave a view out, such as a
very large table, with `global_search = False`.

### Large tables

Counting every match and reaching deep pages both get slow on a table of millions. Each has a
setting:

```python
from adminsite import CountMode, Descending, ModelView, Pagination


class EventView(ModelView[Event]):
    fields_default_sort = [Descending(Event.created_at)]
    count_mode = CountMode.ESTIMATED
    pagination = Pagination.KEYSET
```

`CountMode.ESTIMATED` shows the row count that Postgres and MySQL keep in their statistics, such as
"about 2,500,000", and counts a narrowed list only up to 10,000. SQLite counts exactly.
`CountMode.NONE` shows no total. Either one leaves the counts off the filters' options, as
[Filters](filters.md#counts) says.

`Pagination.KEYSET` goes on from the last row seen instead of skipping rows, so page 400 costs the
same as page 1. The pager then shows Previous and Next instead of page numbers. A sort by a column
that can be empty, or by a related model's column, falls back to page numbers. Put an index on the
columns you sort by, primary key last, such as `(created_at, id)`.

A large column the list does not show is still loaded on every row, unless `deferred_fields` names
it:

```python
class EventView(ModelView[Event]):
    fields = [Event.id, Event.kind, Event.created_at, Event.payload]
    exclude_fields_from_list = [Event.payload]
    deferred_fields = [Event.payload]
```

The record page, the form and the API still load it.

## Forms

The forms show the view's fields in order. `read_only=True` shows a value nobody can change, and
`get_readonly_fields(request, record)` locks more for one user or one record. A read-only value is
never taken from the form, even if someone adds the input back. `can_create`, `can_edit` and
`can_delete` switch pages off, as [Permissions](permissions.md) shows.

### The record page

The record page shows every field with its label. Leave a field off the forms when nobody should
send it back:

```python
class UserView(ModelView[User]):
    fields = [User.name, User.email, User.is_active, User.signed_up_at, User.invoices]
    exclude_fields_from_create = [User.signed_up_at, User.invoices]
    exclude_fields_from_edit = [User.signed_up_at, User.invoices]
```

A link to many records, such as `invoices`, names the first 20 and says how many more there are.

### Arrange the fields

`form_layout` puts the fields of a long form into groups, and the record page follows the same
layout:

```python
from adminsite import PanelWidget, TabsWidget


class ProductView(ModelView[Product]):
    fields = [
        Product.name,
        Product.slug,
        Product.description,
        Product.price,
        Product.cost,
        Product.photo,
        Product.datasheet,
    ]
    form_layout = [
        PanelWidget(
            "Product",
            [(Product.name, Product.slug), Product.description],
            description="What customers see in the shop.",
        ),
        PanelWidget("Price", [(Product.price, Product.cost)]),
        TabsWidget([("Photo", [Product.photo]), ("Datasheet", [Product.datasheet])]),
    ]
```

| Entry | What it draws |
|---|---|
| A field | The field, on a line of its own. |
| [`PanelWidget`][adminsite.PanelWidget] | A card with a title. |
| [`FieldsetWidget`][adminsite.FieldsetWidget] | A group with a caption, inside a card. |
| [`RowWidget`][adminsite.RowWidget], or a tuple | Fields side by side, and one under the other on a phone. |
| A list | Fields one under the other, such as a column inside a row. |
| [`TabsWidget`][adminsite.TabsWidget] | One tab at a time. |

A field the layout leaves out comes last.

### Edit related records in the same form

An order and its lines belong together, so edit them on one page. Name the relationship in
`inlines`:

```python
from adminsite import Inline


class OrderView(ModelView[Order]):
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
        )
    ]
```

The lines show as a table under the order's fields, where rows can be added and removed. Removing
a row deletes that line. The order and its lines are saved in one transaction, so a line that fails
keeps the whole order unsaved.

[`Inline`][adminsite.Inline] lists the other options. `get_inlines(request, record)` picks a
different set per request, and [Permissions](permissions.md#inline-fields) covers the fields of
the rows.

### Turn off the record page or the export

```python
class ApiKeyView(ModelView[ApiKey]):
    can_view_detail = False
    can_export = False
```

Without a record page, a row opens the form instead. Both go through `allows`, so they can be
decided per user too.

### The sidebar

`icon` sets the view's icon in the sidebar, as inline SVG markup or the address of a picture. The
markup goes into the page as it is, so use only icons you trust. `in_sidebar = False` leaves a view
out of the sidebar, for records people only open from others, such as an invoice's payments:

```python
class OrderView(ModelView[Order]):
    icon = '<svg viewBox="0 0 16 16"><path d="M2 4h12v9H2z" fill="currentColor"/></svg>'


class PaymentView(ModelView[Payment]):
    in_sidebar = False
```

The pages of a view left out of the sidebar still open, and links from other records still lead
to them.

## Answer per request

Every `get_` method receives the request, so the answer can depend on the user, and
`can_access_field` decides who sees which field on which page:

```python
from starlette.requests import Request

from adminsite import BaseField, ColumnReference, ModelView, RequestAction


class OrderView(ModelView[Order]):
    fields = [Order.id, Order.customer, Order.status, Order.total]

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        return field.name != "total" or request.state.user.is_manager

    def get_readonly_fields(
        self, request: Request, record: Order | None
    ) -> list[ColumnReference]:
        if record is not None and record.status == "shipped":
            return [Order.customer, Order.status]
        return []
```

The methods are `get_searchable_fields`, `get_list_filters`, `get_fields_default_sort`,
`get_deferred_fields`, `get_readonly_fields`, `get_inlines` and `get_actions`. Each answers with
what its setting takes. [Permissions](permissions.md#fields) says where a refused field is left
out.

## Several views of one model

A model can have as many views as you like, each with its own `name`:

```python
class ShippedOrders(ModelView[Order]):
    name = "shipped_orders"
    label_plural = "Shipped orders"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status == OrderStatus.SHIPPED)
```

`scope_query` narrows every read the view makes, as [Permissions](permissions.md#rows) shows.

### Choose which view a link opens

A link to a customer opens the first view registered for `Customer`. Name another with `view`:

```python
class WalletView(ModelView[Wallet]):
    fields = [Wallet.id, RelationField(Wallet.owner, view=BuyerView), Wallet.balance]
```

The owner then opens in `BuyerView`, and the picker lists buyers. `view` also takes the name in a
view's URL, such as `"buyers"`.
