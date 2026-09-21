# adminsite

An admin panel for SQLAlchemy models. Mount it into Starlette, FastAPI or Litestar and your
team gets pages to search, filter, read and change your data.

Under development. The first release is not on PyPI yet.

```python
from adminsite import Admin, ModelView


class OrderView(ModelView, model=Order):
    group = "Sales"
    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)


admin = Admin(engine, title="Acme", views=[OrderView])
app.mount("/admin", admin)
```

That is the whole setup. The columns, the labels, the filters, the form controls and the
validation come from your models.

## What it does

- **Reads the model.** Column types become the right controls, `created_at` becomes "Created at",
  and an enum becomes a select with its values spelled out.
- **Never lets a page fall into N+1 queries.** Listing `customer.name` loads the customers with
  the page, and a collection costs one more query. The tests count the statements.
- **Handles large tables.** Counting can be turned off per view, in which case paging costs one
  query and one extra row. Facet counts on filters can be turned off too.
- **Filters you can write yourself.** The built-in ones cover choices, booleans, number ranges,
  date ranges, links and text. A custom filter is a class that returns a condition.
- **Bulk actions over everything that matches**, not just the page. The selection is a query, so
  an action over a large table stays one statement.
- **Permissions at four levels:** the view, the action, the field and the row. `scope_query`
  narrows every read, so a row a user may not see cannot be opened by guessing its key.
- **Hooks that run inside the transaction** and receive the session, so a business rule can read
  other tables and refuse a save.
- **An audit log** with a History tab on every record and an Activity page, including bulk actions
  row by row.
- **Async or sync.** Give it an `AsyncEngine` or a plain `Engine`. Everything above the session
  adapter is written once. Tested on SQLite and on Postgres, with asyncpg and psycopg.
- **No Node, no CDN.** The CSS and JavaScript are built into the package.

## Installing

```
pip install adminsite
```

Add `aiosqlite`, `asyncpg` or whichever driver your database needs.

## Getting started

```python
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from adminsite import Admin, ModelView
from adminsite.auth import PasswordAuth, hash_password

engine = create_async_engine("postgresql+asyncpg://localhost/shop")
app = FastAPI()


class CustomerView(ModelView, model=Customer):
    display_template = "{name} ({email})"
    list_display = ("name", "email", "region")
    search_fields = ("name", "email")


admin = Admin(
    engine,
    title="Acme",
    views=[CustomerView],
    auth=PasswordAuth({"nima": hash_password("letmein")}),
    secret_key="read this from your settings",
)
app.mount("/admin", admin)
```

Signing in needs a `secret_key`, which signs the session cookie. `PasswordAuth` takes hashed
passwords, so run `hash_password` once and keep the result in your settings, never the password
itself. It suits a small internal tool. For anything larger, subclass `AuthProvider` and check
your own user table.

## Writing a view

| Setting | What it does |
|---|---|
| `list_display` | The columns on the list. Dotted paths such as `customer.name` work. |
| `search_fields` | The paths the search box looks in. |
| `list_filter` | Paths, or filter instances you built yourself. |
| `ordering` | The starting order, with `-` for descending. |
| `form_fields`, `readonly_fields`, `exclude` | What the form shows and what it locks. |
| `page_size`, `count_mode` | How many rows a page holds, and whether to count them all. |
| `display_template` | How a record is named, for example `"Order #{id}"`. |
| `fields` | Field instances that replace the ones worked out from the columns. |

Anything that depends on who is asking is a method:

```python
class OrderView(ModelView, model=Order):
    def get_list_display(self, request=None):
        if request.user.is_support:
            return ("id", "status")
        return super().get_list_display(request)

    def scope_query(self, statement, *, request=None):
        return statement.where(Order.region == request.user.region)

    async def allows(self, action, *, request=None, record=None):
        if action == Permission.DELETE:
            return request.user.is_manager
        return await super().allows(action, request=request, record=record)
```

## Hooks

```python
class OrderView(ModelView, model=Order):
    async def before_save(self, context: SaveContext) -> None:
        if context.created and not await in_stock(context.session, context.record):
            raise RefusedError("That product is out of stock.")

    async def after_save(self, context: SaveContext) -> None:
        await context.session.add(AuditEntry(order_id=context.record.id))
```

Both run inside the transaction that writes the record. Raising `RefusedError` rolls the save
back and shows the message on the form. Any other exception is treated as a fault.

## Audit log

```python
admin = Admin(engine, views=[OrderView], audit=True)
```

Every create, change and delete is written down with who did it and what each field was before
and after. A record's page gets a History tab, and the Activity page lists recent changes across
the admin. A bulk action writes one entry per record it touched, sharing a batch id, so each
record's history shows it.

Entries are written after the change commits, so a rolled back change never appears.

`audit=True` keeps the log in a SQLite file of its own, `adminsite_audit.db`, which needs no setup.
That suits a single server. With several workers or containers, keep it in your own database
instead, so every process writes to the same place:

```python
from adminsite.audit import AuditLog, audit_metadata

admin = Admin(engine, audit=AuditLog(engine))

# In your Alembic env.py, so the table is created by your migrations:
target_metadata = [Base.metadata, audit_metadata]
```

## Actions

```python
class OrderView(ModelView, model=Order):
    @action("Mark as shipped", confirm="Mark the chosen orders as shipped?")
    async def ship(self, selection: Selection) -> str:
        changed = await selection.update(status=OrderStatus.SHIPPED)
        return f"{changed} orders marked as shipped."
```

An action can ask for values before it runs. They appear in a dialog, are checked like form
fields, and reach the method by name:

```python
@action(
    "Mark as shipped",
    confirm="Mark the chosen orders as shipped?",
    inputs=[ChoiceField("carrier", choices=CARRIERS, required=True)],
)
async def ship(self, selection: Selection, carrier: str) -> str:
    changed = await selection.update(status=OrderStatus.SHIPPED, carrier=carrier)
    return f"{changed} orders sent with {carrier}."
```

The selection is either the rows that were ticked or every row the current search and filters
match. `selection.update` and `selection.delete` are single statements and skip the save hooks;
`selection.records()` loads the records when the hooks matter. Because `selection.delete` never
loads the records, it relies on the database for cascades: give the foreign keys `ondelete="CASCADE"`
where children should go with their parent.

## Custom filters

```python
class OverdueFilter(SQLFilter):
    """Orders past their delivery date."""

    async def options(self, context):
        return [FilterOption("late", "Overdue"), FilterOption("soon", "Due in 2 days")]

    def condition(self, value, repository):
        if value.first == "late":
            return Order.due_at < func.now()
        return Order.due_at < func.now() + timedelta(days=2)


class OrderView(ModelView, model=Order):
    list_filter = ("status", OverdueFilter("delivery", label="Delivery"))
```

Override `apply` instead of `condition` when the filter needs to change the statement itself.

## Trying it out

```
uv run uvicorn examples.shop:app --reload
```

Then open http://127.0.0.1:8000/admin and sign in as `nima` / `letmein`.

## Developing

```
uv sync --all-groups
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
```

The tests run on SQLite by default, async and sync. To run every database test against Postgres
as well, start one and point the tests at it:

```
docker run -d --name adminsite-postgres -p 55432:5432   -e POSTGRES_USER=adminsite -e POSTGRES_PASSWORD=adminsite -e POSTGRES_DB=adminsite   postgres:17-alpine
ADMINSITE_POSTGRES_URL=postgresql://adminsite:adminsite@localhost:55432/adminsite uv run pytest
```

The stylesheet and the vendored JavaScript are built from `frontend/`, and the results are
committed, so nobody installing adminsite needs Node:

```
cd frontend
npm install
npm run vendor
npm run build
```

## License

MIT.
