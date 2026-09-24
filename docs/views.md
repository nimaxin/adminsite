# Views

A `ModelView` says how one model appears in the admin. Class attributes describe the list and the
form. Methods whose names start with `get_` answer the same questions per request, for when the
answer depends on who is asking.

```python
from adminsite import CountMode, ModelView


class OrderView(ModelView, model=Order):
    group = "Sales"
    display_template = "Order #{id}"

    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)
    page_size = 50

    form_fields = ("customer", "status", "note")
    readonly_fields = ("total",)
```

## Naming

| Setting | Default | Used for |
|---|---|---|
| `name` | the model name, plural and snake case: `orders`, `order_items` | the URL |
| `label` | `Order`, `Order item` | headings and buttons |
| `label_plural` | `Orders`, `Order items` | the sidebar and the list heading |
| `group` | none | the sidebar section the view sits under |
| `display_template` | `str(record)` | how a record is named elsewhere, for example `"{name} ({email})"` |

`display_template` is also used when another model links to this one, so a customer picker on the
order form shows `Lena Fischer (lena@fischer.de)` instead of just the name.

## The list

| Setting | What it does |
|---|---|
| `list_display` | The columns, in order. Dotted paths such as `customer.name` follow links. |
| `search_fields` | The paths the search box looks in. Text matches anywhere in the value, numbers match exactly. |
| `list_filter` | Paths, or filters you built yourself. See [Filters](filters.md). |
| `ordering` | The starting order. `-created_at` means newest first. |
| `page_size` | Rows per page. 25 unless you say otherwise. |
| `page_sizes` | The sizes people may switch between. Empty leaves the size fixed. |
| `count_mode` | `EXACT` counts every match, `ESTIMATED` guesses on big tables, `NONE` skips the count. |
| `deferred_fields` | Columns the list never shows, left out of its query. |
| `global_search` | Whether the command palette searches this view. On by default. |
| `list_columns` | More columns people can add from the Columns menu. |
| `icon` | The sidebar icon: inline SVG markup, or the address of a picture. |
| `pagination` | `Pagination.OFFSET` for page numbers, `Pagination.KEYSET` for big tables. |

With no `list_display`, every column is shown, and a foreign key such as `customer_id` appears as
its relationship, `customer`.

Anything the list shows is loaded with the page. `customer.name` joins the customer into the same
query; a path through a collection such as `items.quantity` costs one more query for the whole
page, not one per row.

### How the search matches

The search box looks for the term inside every search field, which is what people expect of a name
or a note. On a table of millions of rows it cannot use an index, so every search reads the whole
table. `search_condition` lets the view decide the condition instead:

```python
import re


class ContactView(ModelView, model=Contact):
    search_fields = ("phone", "name")

    def search_condition(self, term, *, request=None):
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

The **Columns** menu above the list hides and shows columns. It offers the columns of
`list_display`, plus any in `list_columns`:

```python
class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")
    list_columns = ("customer.email", "note", "created_at")
```

The choice goes in the URL as `?cols=id&cols=total`, so it can be bookmarked and shared, and it is
remembered in the session, so the list keeps those columns next time. The CSV export follows it
too. Only columns on offer can be picked: a column you hide from some users in
`get_list_display` stays hidden, whatever the URL says. Override `get_column_choices(request)` to
offer different extras per user.

### Rows per page

`page_size` sets how many rows a page holds. Offer a few sizes and a menu appears above the list:

```python
class OrderView(ModelView, model=Order):
    page_size = 25
    page_sizes = (25, 100, 500)
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
and shows the first five matches per view, named by `display_template`. Arrow keys move, Enter
opens.

It searches only views the user may open, through `scope_query`, and only views with
`search_fields`. Leave a view out, for example a very large table, with `global_search = False`.

### Large tables

Two things get slow once a table holds millions of rows: counting every match, and reaching deep
pages, since the database walks every row before the page it returns. Both have a setting.

```python
from adminsite import CountMode, ModelView, Pagination


class EventView(ModelView, model=Event):
    ordering = ("-created_at",)
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
class EventView(ModelView, model=Event):
    list_display = ("id", "kind", "created_at")
    deferred_fields = ("payload",)
```

The record page, the form and the API load them as usual, so nothing disappears, and a column that
the list does show is loaded whatever this says, as is the key and anything `display_template`
reads. Those are the columns every row needs, and reading one afterwards would cost a query per
row. Answer per request with `get_deferred_fields(request)`.

A keyset needs columns it can compare. When the list is sorted by a column that can be empty, or by
a path through a relationship such as `customer.name`, that page falls back to page numbers. Put an
index on the columns you sort by, primary key last, such as `(created_at, id)`.

## The form

| Setting | What it does |
|---|---|
| `form_fields` | The fields, in order. A relationship name, such as `customer`, gives a picker. |
| `readonly_fields` | Shown, but never read back from what was submitted. |
| `exclude` | Left out of both the list and the form. |
| `fields` | Field objects that replace the ones worked out from the columns. See [Fields](fields.md). |
| `can_create`, `can_edit`, `can_delete` | Switch those pages off. See [Permissions](permissions.md). |
| `detail_fields` | What the record page shows, when that differs from the form. |
| `can_detail`, `can_export` | Switch off the record page and the CSV export. |

A readonly field is safe against a tampered form: its value is never taken from the request, even
if someone adds the input back by hand.

### The record page

The record page shows the form's fields unless you name its own. That is how a page shows things
nobody should post back, and how a form keeps fields the page has no reason to repeat:

```python
class UserView(ModelView, model=User):
    form_fields = ("name", "email", "is_active")
    detail_fields = (
        "name",
        "email",
        "is_active",
        "signed_up_at",
        "invoices",
        "raw_payload",
    )
```

Anything the page names is loaded with the record, so a linked record costs no extra query. Use
`get_detail_fields(request, record)` to answer per user, and remember that a field only on the
page is never read back from a form, so it needs no `readonly_fields` entry.

### Related records in the same form

An order and its lines belong together, so edit them on one page. Name the relationship in
`inlines`:

```python
from adminsite import Inline


class OrderView(ModelView, model=Order):
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)
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
| `extra` | How many blank rows a table starts with while it has no rows yet. Defaults to 1. |
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
class SessionView(ModelView, model=Session):
    can_detail = False
    can_export = False
```

Without a detail page, rows open the form instead, or read as plain text where there is no form
either, and saving lands back on the list. Without export, the CSV button is gone and the route
refuses. Both go through `allows`, so `Permission.DETAIL` and `Permission.EXPORT` can be decided
per user like any other permission.

### An icon in the sidebar

`icon` takes inline SVG markup, or the address of a picture:

```python
class OrderView(ModelView, model=Order):
    icon = '<svg viewBox="0 0 16 16"><path d="M2 4h12v9H2z" fill="currentColor"/></svg>'
```

Markup is written into the page as it is, so keep it to icons you control. A relative address is
served from the admin, so a plugin's `add_static` folder works.

### Left out of the sidebar

Some views exist only so their records can be opened from the records that point at them: a
customer's sessions, an invoice's payments. `in_sidebar = False` leaves such a view out of the
sidebar, the command palette's list of pages and the overview's counts:

```python
class SessionView(ModelView, model=Session):
    in_sidebar = False
```

Nothing else changes. Its list and record pages still open, links from other records still lead to
them, and the command palette still finds its records.

## Answering per request

Every `get_` method receives the request, so the answer can depend on the user:

```python
class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total")

    def get_list_display(self, request=None):
        if request.user.is_support:
            return ("id", "status")
        return super().get_list_display(request)

    def get_readonly_fields(self, request=None, record=None):
        if record is not None and record.status == "shipped":
            return ("customer", "status", "total")
        return ()
```

The methods are `get_list_display`, `get_search_fields`, `get_filters`, `get_ordering`,
`get_form_fields`, `get_readonly_fields` and `get_actions`.

## Several views of one model

A model can have as many views as you like, as long as each has its own name:

```python
class ShippedOrders(ModelView, model=Order):
    name = "shipped_orders"
    label_plural = "Shipped orders"

    def scope_query(self, statement, *, request=None):
        return statement.where(Order.status == OrderStatus.SHIPPED)
```

`scope_query` narrows every read the view makes. It is covered in [Permissions](permissions.md).
