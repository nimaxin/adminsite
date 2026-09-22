# adminsite

An admin panel for SQLAlchemy models. Mount it into Starlette, FastAPI or Litestar, and your team
gets pages to search, filter, read and change your data.

```python
from adminsite import Admin, ModelView


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)


admin = Admin(engine, title="Acme", views=[OrderView])
app.mount("/admin", admin)
```

That is a working admin. The columns, labels, filters, form controls and validation all come
from your models.

!!! note "Alpha"
    adminsite is in alpha. It is tested and works, but names may still change before 0.1.0.
    Install it with the exact version: `pip install adminsite==0.1.0a3`.

## What you get

- **Pages worked out from your models.** Column types pick the right controls, `created_at` reads
  "Created at", an enum becomes a select, and a foreign key becomes a picker for the record it
  points at.
- **No N+1 queries.** Showing `customer.name` loads the customers with the page. Tests count the
  statements so it stays that way.
- **Fast on large tables.** Counting can be switched off per view, which makes a page one query.
- **Filters you can write yourself**, next to the built-in ones.
- **Bulk actions over everything that matches**, not only the rows on the page, and actions that
  ask for values first.
- **Permissions at four levels**: the view, the action, the field and the row.
- **Hooks inside the transaction** that can read other tables and refuse a save.
- **An audit log** with a history on every record.
- **Async or sync.** An `AsyncEngine` or a plain `Engine` both work. Tested on SQLite and Postgres.
- **No Node and no CDN.** The CSS and JavaScript ship inside the package.

## Where to go next

- [Getting started](getting-started.md) sets up a working admin in a few minutes.
- [Views](views.md) covers everything a `ModelView` can say about a model.
- [Permissions](permissions.md) is worth reading before you put the admin in front of anyone.
