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

The settings are lists in the class body. adminsite reads them when it builds the view and never
changes them, so ruff's RUF012, which asks for `ClassVar` on such a list, can be switched off for
the module that holds your views:

```toml
[tool.ruff.lint.per-file-ignores]
"app/admin.py" = ["RUF012"]
```

## Naming columns

A setting names a column by its attribute, `Order.total`, or by its name as a string, `"total"`.
`Link(Order.customer, Customer.email)` is a column of a related model, which a string writes as
`"customer.email"`. Links nest to go further: `Link(OrderItem.order, Link(Order.customer,
Customer.name))`. Through a link to many, such as `Link(Customer.orders, Link(Order.items,
OrderItem.quantity))`, a column holds the value of every record it reaches, shown as `1, 2, 1`.

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
`fields_default_sort`, a sort through a relationship holding many records, a related model's
column in `deferred_fields`, or a relationship in a `record_title`.

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
database or the model fills in too, such as an autoincrement id or a `uuid4` default. A key people
choose, such as a product code, is on the form for a new record, required, and read only once the
record exists, since its URL, its history and the rows pointing at it depend on it. So is a link
made only of key columns, such as the customer of a profile keyed by its customer. A hook can still
change a key, and the page follows the record to it.

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

A list names only fields the view shows: leaving off one it never shows would do nothing, so that
stops the admin when it starts. With `fields` empty, a foreign key is shown as its relationship, so
leave the customer off with `Order.customer`, not `Order.customer_id`.

`hidden_in_list=True` keeps a column off the list until someone turns it on in the
[Columns menu](#choosing-columns). A field hidden in the list and excluded from it at once asks for
two different things, and stops the admin when it starts.

## Naming

`name` is the view's part of the URL, `label` and `label_plural` what its records are called in
headings, buttons and the sidebar, and `group` the sidebar section it sits under. Each one left out
is worked out from the model, as [`ModelView`][adminsite.ModelView] says. `record_title` says how a
record is named everywhere else, for example `"{name} ({email})"`.

Without a `record_title`, a record is named by its model's `__str__`. A model without one, as
SQLAlchemy models start out, is named by the view's label and the record's key, `Order #12`,
rather than `<Order object at 0x...>`. A `__repr__` is not used as a name, since one written for a
dataclass prints every column.

`record_title` is also used wherever another model links to this one: in its list cells, on
its record pages, in its export, in its audit log and in its pickers. A customer then reads
`Lena Fischer (lena@fischer.de)` on every order, not just the name. A link given its own
`record_title`, as `RelationField(Order.customer, record_title="{email}")`, keeps it.

Each name in braces is checked against the model when the admin starts, so
`record_title = "Order #{nmae}"` stops it with a message listing the columns the model has. The
names are the model's own columns. A relationship, as in `{customer.name}`, stops the admin too,
since every page that names an order would have to load its customer as well. A column's own
attribute, such as `{created_at.year}`, is fine. A name no template can build comes from
`get_record_title`, which gets the record with its columns loaded, not its links:

```python
class SupplierView(ModelView[Supplier]):
    def get_record_title(self, supplier: Supplier, /) -> str:
        if supplier.closed_at is not None:
            return f"{supplier.name} (closed)"
        return supplier.name
```

The record is passed by position, so the parameter can be named after the model.

## The list

The list is shaped by `searchable_fields`, `sortable_fields`, `fields_default_sort`,
`list_filters` (see [Filters](filters.md)), `page_size`, `page_size_options`, `count_mode`,
`pagination`, `deferred_fields` and `global_search`. [`ModelView`][adminsite.ModelView] says what each
one takes and what it does left out; the sections below show them at work.

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
    page_size_options = [25, 100, 500]
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
from adminsite import CountMode, Descending, ModelView, Pagination


class EventView(ModelView[Event]):
    fields_default_sort = [Descending(Event.created_at)]
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
columns in `deferred_fields` and the list query leaves them out:

```python
class EventView(ModelView[Event]):
    fields = [Event.id, Event.kind, Event.created_at, Event.payload]
    exclude_fields_from_list = [Event.payload]
    deferred_fields = [Event.payload]
```

The record page, the form and the API load them as usual, so nothing disappears, and a column that
the list does show is loaded whatever this says, as is the key and anything `record_title`
reads. Those are the columns every row needs, and reading one afterwards would cost a query per
row. Answer per request with `get_deferred_fields(request)`.

A keyset needs columns it can compare. When the list is sorted by a column that can be empty, or by
a path through a relationship such as `customer.name`, that page falls back to page numbers. Put an
index on the columns you sort by, primary key last, such as `(created_at, id)`.

## The form

The forms show the view's `fields` in order, as under [Fields](#fields), arranged by
`form_layout` as under [Arranging the fields](#arranging-the-fields). A relationship, such as
`Order.customer`, gives a picker. `read_only=True` on a field shows it and never reads it back, and
`get_readonly_fields(request, record)` locks more for one user or one record. `can_create`,
`can_edit` and `can_delete` switch those pages off, and `can_view_detail` and `can_export` the
record page and the CSV export; see [Permissions](permissions.md).

A readonly field is safe against a tampered form: its value is never taken from the request, even
if someone adds the input back by hand. `read_only=True` locks a key as well, on the form for a new
record too, as in starlette-admin, so the model or a hook fills it in.

### The record page

The record page lists the fields as a table, a row per field with its label beside its value, and
the forms show the ones that can be edited, one to a line. A field nobody should post back is left
off the forms:

```python
class UserView(ModelView[User]):
    fields = [User.name, User.email, User.is_active, User.signed_up_at, User.invoices]
    exclude_fields_from_create = [User.signed_up_at, User.invoices]
    exclude_fields_from_edit = [User.signed_up_at, User.invoices]
```

Anything the page shows is loaded with the record, so a linked record costs no extra query.
`can_access_field` with `RequestAction.DETAIL` answers per user, as under
[Answering per request](#answering-per-request). A field only on the page is never read back from
a form, so it needs no `read_only=True`.

A link to many records, such as `invoices` above, is the exception. It is never loaded whole,
since a user may have thousands: the page names the first 20 and says how many more there are, in
two small queries. Both read through the linked model's own view, so its `scope_query` applies to
the names and to the count.

A linked record's name links to its own page wherever its view lets this user open it, for a link
to one record and for each record a link to many names. With the [audit log](audit.md) on, a line
under the title says who changed the record last.

### Arranging the fields

A model with many fields reads better in groups. `form_layout` arranges the create and edit forms,
and the record page follows the same panels, so a field stays where it was when you press Edit:

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
| A field, by attribute or name | The field, on a line of its own. |
| `PanelWidget(title, children, description="", collapsible=False, collapsed=False)` | A titled card. Holding one field, it leaves that field's label to its title. A folded panel opens when it holds a mistake. |
| `FieldsetWidget(legend, children)` | A bordered group with a caption, inside a card. |
| `RowWidget(children)`, or a tuple | Fields side by side on a wide screen, one under the other on a phone. |
| A list | Fields one under the other, such as a column inside a row. |
| `TabsWidget([(label, children), ...])` | One tab at a time. The form opens on the first tab holding a mistake and marks each that holds one; the record page shows each tab as a card of its own. |

The names and the shorthand are starlette-admin's. A field the layout leaves out comes last, so
none is ever lost; one placed twice, or one that is not among `fields`, stops the admin starting
with a message saying where it is. Each page still shows only what it would show anyway: a field
left off the edit form, or kept from this user by `can_access_field`, leaves the layout too, and a
panel left empty is not drawn. [Inlines](#related-records-in-the-same-form) come after the layout.

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

`fields` lists the child's fields as a view's are, so `Field(OrderItem.added_at, read_only=True)`
is shown and not edited. [`Inline`][adminsite.Inline] describes it and the other options: `label`,
`blank_rows`, `can_delete` and `record_title`.

The relationship has to hold a list. Blank rows that stay empty are ignored, and a required field
only counts once something else in the row is filled in. The detail page lists the children too.

The relationship fills in the child's columns it joins on, such as `OrderItem.order_id`, so the rows
never show them, nor a link made of them, such as `OrderItem.order`, even when `fields` names one.
That holds for a child with no relationship back to the parent too, and for a column that is part of
the child's key. Another link to the parent's model is a field like any other: in
`Inline(Team.home_matches, fields=[Match.away_team, Match.week])` each row picks the away team.

The fields behave as they do on a view. A new row takes the create page's fields and an existing
child the edit page's, and the table on the record page shows the record page's, so
`exclude_from_edit=True` sets a value once, when the row is added. A `Link` is shown on the record
page and never edited. The parent's `can_access_field` and `get_readonly_fields` answer for the
children too, naming each field by its path from the parent, such as `items.unit_price`: see
[Permissions](permissions.md#inline-fields).

A read-only or locked field is never read from the form, a new row's included, so a new child takes
its value from somewhere else, such as a default on the model: `added_at` above would be declared
with `server_default=func.now()`. A required column with no default cannot be read only here, since
no new row could be saved.

Removing a row deletes that child record. A key sent for a row that belongs to another parent is
refused.

Pick a different set per request with `get_inlines(request, record)`.

### Pages a view does not need

Some views are complete on the list, and some hold data nobody should carry out of the admin:

```python
class ApiKeyView(ModelView[ApiKey]):
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

Some views exist only so their records can be opened from the records that point at them: an
invoice's payments, an order's lines. `in_sidebar = False` leaves such a view out of the sidebar,
the command palette's list of pages and the overview's counts:

```python
class PaymentView(ModelView[Payment]):
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
a view's URL, such as `"buyers"`. A view the admin does not have, misspelt or never registered, or
a view of another model, stops the admin before its first page, with the views of that model
listed.

Without `view`, a card for a record the first view's scope leaves out opens the first view that
does hold it, rather than a page that answers 404.
