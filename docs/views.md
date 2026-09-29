# Views

A `ModelView` says how one model appears in the admin, and names the model as its type argument:
`class OrderView(ModelView[Order])`. Class attributes describe the list and the form. Methods whose names start with `get_` answer the same questions per request, for when the
answer depends on who is asking.

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

## Naming columns

A setting names a column by its attribute, `Order.total`, or by its name as a string, `"total"`.
`Link(Order.customer, Customer.email)` is a column of a related model, which a string writes as
`"customer.email"`. Links nest to go further: `Link(OrderItem.order, Link(Order.customer,
Customer.name))`.

A sort takes the same, and `Descending(Order.created_at)`, or the string `"-created_at"`, sorts from
the highest value down, so newest first.

A type checker checks an attribute; the admin checks a string when it starts. A name the model
does not have stops it with a message that names the view and the setting, and lists what the
model has:

```text
OrderView.fields: Order has no column or relationship "totl". Its columns: id, customer_id,
status, total, note, created_at. Its relationships: customer, items.
```

So does an attribute of another model, a link whose relation does not lead to its column, one
string where a list belongs, such as `searchable_fields = "note"`, and a name a setting cannot use:
a relationship or a computed field in `searchable_fields`, `sortable_fields` or
`fields_default_sort`, a sort through a relationship holding many records, or a related model's
column in `deferred_fields`.

## Fields

`fields` lists every field of the view once, in order, and each page shows them in that order. An
entry is a column, named as above, or a field. `Field(Order.created_at, label="Placed")` is the
field adminsite picks for the column, with your options, and a kind such as
`TextAreaField(Order.note)` chooses the field itself. Either way, whatever the options leave out
comes from the column: its label, its length, whether it may be empty. See [Fields](fields.md).
With `fields` empty, the view shows every column the model has, and a foreign key such as
`customer_id` appears as its relationship, `customer`.

A relationship does two jobs: `Order.customer` shows the customer's name in the list and a picker
in the form. A column of a related model, such as `Link(Order.customer, Customer.email)`, is shown
and never edited, as is a computed field, so forms leave both out. They leave out a key the
database numbers too; a key people choose, such as a product code, stays in the form.

A page leaves a field out by a flag on the field or a list on the view, which mean the same:

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

`hidden_in_list=True` keeps a column off the list until someone turns it on in the
[Columns menu](#choosing-columns). A field hidden in the list and excluded from it at once asks for
two different things, and stops the admin when it starts.

`list_display`, `list_columns`, `form_fields`, `detail_fields`, `exclude` and `FieldOptions` still
work, and 0.1.0a10 will refuse them, naming what replaces each. A view that still sets
`list_display`, `form_fields` or `detail_fields` reads `fields` as before: it changes the fields
those settings name, and places none. The other names 0.1.0a9 used, such as `search_fields`,
`ordering` and `list_filter`, already stop the admin with the name each has now.

## Naming

| Setting | Default | Used for |
|---|---|---|
| `name` | the model name, plural and snake case: `orders`, `order_items` | the URL |
| `label` | `Order`, `Order item` | headings and buttons |
| `label_plural` | `Orders`, `Order items` | the sidebar and the list heading |
| `group` | none | the sidebar section the view sits under |
| `record_title` | `str(record)`, or `Order #12` | how a record is named elsewhere, for example `"{name} ({email})"` |

Without a `record_title`, a record is named by its model's `__str__`. A model without one, as
SQLAlchemy models start out, is named by the view's label and the record's key, `Order #12`,
rather than `<Order object at 0x...>`. A `__repr__` is not used as a name, since one written for a
dataclass prints every column.

`record_title` is also used wherever another model links to this one: in its list cells, on
its record pages, in its export, in its audit log and in its pickers. A customer then reads
`Lena Fischer (lena@fischer.de)` on every order, not just the name. A link given its own
`display_template` keeps it.

Each name in braces is checked against the model when the admin starts, so
`record_title = "Order #{nmae}"` stops it with a message listing the columns the model has.
`{customer.name}` reads through a link. A name no template can build comes from
`get_record_title`:

```python
class SupplierView(ModelView[Supplier]):
    def get_record_title(self, supplier: Supplier, /) -> str:
        if supplier.closed_at is not None:
            return f"{supplier.name} (closed)"
        return supplier.name
```

The record is passed by position, so the parameter can be named after the model.

## The list

| Setting | What it does |
|---|---|
| `searchable_fields` | The columns the search box looks in. Text matches anywhere in the value, numbers match exactly. |
| `sortable_fields` | The columns people can sort the list by. Every stored column when left empty. |
| `fields_default_sort` | The starting order. `Descending(Order.created_at)` or `"-created_at"` means newest first. |
| `list_filters` | Columns, or filters you built yourself. See [Filters](filters.md). |
| `page_size` | Rows per page. 25 unless you say otherwise. |
| `page_size_options` | The sizes people may switch between. Empty leaves the size fixed. |
| `count_mode` | `EXACT` counts every match, `ESTIMATED` guesses on big tables, `NONE` skips the count. |
| `deferred_fields` | Columns the list never shows, left out of its query. |
| `global_search` | Whether the command palette searches this view. On by default. |
| `icon` | The sidebar icon: inline SVG markup, or the address of a picture. |
| `pagination` | `Pagination.OFFSET` for page numbers, `Pagination.KEYSET` for big tables. |

Anything the list shows is loaded with the page. `customer.name` joins the customer into the same
query; a path through a collection such as `items.quantity` costs one more query for the whole
page, not one per row.

### How the search matches

The search box looks for the term inside every search field, which is what people expect of a name
or a note. On a table of millions of rows it cannot use an index, so every search reads the whole
table. `search_condition` lets the view decide the condition instead:

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

Return a condition, and the list, its count, the export, "select all matching", the command
palette and pickers of this view all use it. Return `None` to fall back to the usual search, as
above for a name.

### Choosing columns

The **Columns** menu above the list hides and shows columns. It offers the list's columns, and the
fields with `hidden_in_list=True`, which start off the list:

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

The choice goes in the URL as `?cols=id&cols=total`, so it can be bookmarked and shared, and it is
remembered in the session, so the list keeps those columns next time. The CSV export follows it
too. Only columns on offer can be picked: a column `can_access_field` keeps from someone stays
hidden from them, whatever the URL says.

### Rows per page

`page_size` sets how many rows a page holds. Offer a few sizes and a menu appears above the list:

```python
class OrderView(ModelView[Order]):
    page_size = 25
    page_size_options = (25, 100, 500)
```

The choice goes in the URL as `?size=100`, stays while paging, sorting, searching and filtering,
and is remembered in the session. Only a size on offer counts, so nobody can ask for a million
rows by editing the URL. "Select all matching" still means every matching row, whatever the page
shows.

### Saved views

A saved view keeps a search, filters, a sort and columns under a name, such as "Unpaid this month",
so nobody has to click them together again. Switch them on for the admin:

```python
admin = Admin(engine, views=[OrderView], saved_views=True)
```

The list gets a **Saved views** menu with the views and a **Save this view** button. A view belongs
to the person who saved it. Ticking **Everyone can use it** shares it with the rest of the team;
only its owner can remove it. Without sign in, every view is everyone's.

Like the [audit log](audit.md), the views go to a SQLite file of their own, `adminsite_views.db`.
To keep them in your database instead:

```python
from adminsite import SavedViews
from adminsite.saved_views import saved_view_metadata

admin = Admin(engine, saved_views=SavedViews(engine))

# In your Alembic env.py, so the table is part of your migrations:
target_metadata = [Base.metadata, saved_view_metadata]
```

### The command palette

Press <kbd>Ctrl</kbd>+<kbd>K</kbd>, or <kbd>Cmd</kbd>+<kbd>K</kbd> on a Mac, anywhere in the admin to jump to a page or a record.
With nothing typed it lists the pages. From two letters on it also runs each view's own search
and shows the first five matches per view, named by `record_title`. Arrow keys move, Enter
opens.

It searches only views the user may open, through `scope_query`, and only views with
`searchable_fields`. Leave a view out, for example a very large table, with `global_search = False`.

### Large tables

Two things get slow once a table holds millions of rows: counting every match, and reaching deep
pages, since the database walks every row before the page it returns. Both have a setting.

```python
from adminsite import CountMode, ModelView, Pagination


class EventView(ModelView[Event]):
    fields_default_sort = ("-created_at",)
    count_mode = CountMode.ESTIMATED
    pagination = Pagination.KEYSET
```

**Counting.** `CountMode.ESTIMATED` reads the row count Postgres and MySQL already keep in their
statistics, which costs nothing, and shows "about 2,500,000". It does so only when nothing narrows
the list and the table holds more than 10,000 rows; below that an exact count is cheap. Once a
search, a filter or `scope_query` narrows the list, the count stops at 10,001 rows and shows "more
than 10,000". On SQLite, which keeps no estimate, it counts exactly.

`CountMode.NONE` skips the count altogether. The pager then shows no total and learns whether there
is a next page by reading one extra row, so a page is a single query.

**Paging.** `Pagination.KEYSET` continues from the last row seen instead of skipping rows, so page
400 costs the same as page 1. The pager shows Previous and Next, without page numbers, and the URL
carries a short cursor such as `?after=WyIyMDI2...`. The primary key is added to the order, so rows
with the same value never repeat or go missing between pages.

**Wide rows.** A list that never shows a large column still loads it on every row. Name those
columns and the list query leaves them out:

```python
class EventView(ModelView[Event]):
    list_display = ("id", "kind", "created_at")
    deferred_fields = ("payload",)
```

The record page, the form and the API load them as usual, so nothing disappears, and a column that
the list does show is loaded whatever this says, as is the key and anything `record_title`
reads. Those are the columns every row needs, and reading one afterwards would cost a query per
row. Answer per request with `get_deferred_fields(request)`.

A keyset needs columns it can compare. When the list is sorted by a column that can be empty, or by
a path through a relationship such as `customer.name`, that page falls back to page numbers. Put an
index on the columns you sort by, primary key last, such as `(created_at, id)`.

## The form

| Setting | What it does |
|---|---|
| `fields` | The fields, in order, as under [Fields](#fields). A relationship, such as `Order.customer`, gives a picker. |
| `read_only=True` on a field | Shown, but never read back from what was submitted. `get_readonly_fields(request, record)` locks more for one user or one record. |
| `can_create`, `can_edit`, `can_delete` | Switch those pages off. See [Permissions](permissions.md). |
| `can_view_detail`, `can_export` | Switch off the record page and the CSV export. |

A readonly field is safe against a tampered form: its value is never taken from the request, even
if someone adds the input back by hand.

### The record page

The record page shows every field, and the forms the ones that can be edited. A field nobody should
post back is left off the forms:

```python
class UserView(ModelView[User]):
    fields = [User.name, User.email, User.is_active, User.signed_up_at, User.invoices]
    exclude_fields_from_create = [User.signed_up_at, User.invoices]
    exclude_fields_from_edit = [User.signed_up_at, User.invoices]
```

Anything the page shows is loaded with the record, so a linked record costs no extra query. Use
`get_detail_fields(request, record)` to answer per user, and remember that a field only on the
page is never read back from a form, so it needs no `read_only=True`.

A link to many records, such as `invoices` above, is the exception. It is never loaded whole,
since a user may have thousands: the page names the first 20 and says how many more there are, in
two small queries.

### Related records in the same form

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

The lines show as a table under the order's fields, with an **Add a row** button and a button to
remove each line. A new order starts with one blank row to fill in; an order that has its lines
gets no empty row under them. Everything is saved in one transaction with the order, so a line
that fails to validate keeps the order unsaved too, and the page comes back with what was typed,
the error next to the cell, and a list of every problem at the top of the form.

| Option | What it does |
|---|---|
| `fields` | The child's columns, in order. Defaults to every field except the link back to the parent. |
| `readonly_fields` | Shown, not editable. |
| `label` | The heading above the table. Defaults to the relationship's name. |
| `blank_rows` | How many blank rows a table starts with while it has no rows yet. Defaults to 1. |
| `can_delete` | Whether rows can be removed. |
| `display_template` | How a child is named, as on a view. |

The relationship has to hold a list. Blank rows that stay empty are ignored, and a required field
only counts once something else in the row is filled in. The detail page lists the children too.

Removing a row deletes that child record. A key sent for a row that belongs to another parent is
refused.

Pick a different set per request with `get_inlines(request, record)`.

### Pages a view does not need

Some views are complete on the list, and some hold data nobody should carry out of the admin:

```python
class SessionView(ModelView[Session]):
    can_view_detail = False
    can_export = False
```

Without a detail page, rows open the form instead, or read as plain text where there is no form
either, and saving lands back on the list. Without export, the CSV button is gone and the route
refuses. Both go through `allows`, so `Permission.VIEW_DETAIL` and `Permission.EXPORT` can be decided
per user like any other permission.

### An icon in the sidebar

`icon` takes inline SVG markup, or the address of a picture:

```python
class OrderView(ModelView[Order]):
    icon = '<svg viewBox="0 0 16 16"><path d="M2 4h12v9H2z" fill="currentColor"/></svg>'
```

Markup is written into the page as it is, so keep it to icons you control. A relative address is
served from the admin, so a plugin's `add_static` folder works.

### Left out of the sidebar

Some views exist only so their records can be opened from the records that point at them: a
customer's sessions, an invoice's payments. `in_sidebar = False` leaves such a view out of the
sidebar, the command palette's list of pages and the overview's counts:

```python
class SessionView(ModelView[Session]):
    in_sidebar = False
```

Nothing else changes. Its list and record pages still open, links from other records still lead to
them, and the command palette still finds its records.

## Answering per request

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
        return field.name != "total" or request.user.is_manager

    def get_readonly_fields(
        self, request: Request, record: Order | None
    ) -> list[ColumnReference]:
        if record is not None and record.status == "shipped":
            return [Order.customer, Order.status]
        return []
```

The methods are `get_searchable_fields`, `get_list_filters`, `get_fields_default_sort`,
`get_deferred_fields`, `get_readonly_fields`, `get_inlines` and `get_actions`. Each answers with
what its setting takes, and is checked the same way. [Permissions](permissions.md#fields) says
where a refused field is left out.

## Several views of one model

A model can have as many views as you like, as long as each has its own name:

```python
class ShippedOrders(ModelView[Order]):
    name = "shipped_orders"
    label_plural = "Shipped orders"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status == OrderStatus.SHIPPED)
```

`scope_query` narrows every read the view makes. It is covered in [Permissions](permissions.md).

### Which view a link opens

A link to a customer, as a card on an order's page, in a picker, or checked when a form is saved,
goes to the first view registered for `Customer`. When customers are split between views by scope,
name the view the link belongs to:

```python
class WalletView(ModelView[Wallet]):
    fields = [Wallet.id, RelationField(Wallet.owner, view=BuyerView), Wallet.balance]
```

The owner's card then opens `BuyerView`, and the picker lists buyers. `view` also takes the name in
a view's URL, such as `"buyers"`.

Without `view`, a card for a record the first view's scope leaves out opens the first view that
does hold it, rather than a page that answers 404.
