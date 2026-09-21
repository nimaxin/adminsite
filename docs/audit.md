# Audit log

Switch it on and adminsite writes down every create, change and delete: who did it, when, and what
each field was before and after.

```python
admin = Admin(engine, views=[OrderView, CustomerView], audit=True)
```

Every record's page gets a **History** tab, and the sidebar gets an **Activity** page listing recent
changes across the admin, which can be narrowed to one model.

Values are written the way the admin shows them, so a change reads
"Customer: Lena Fischer → Marco Rossi" rather than "customer_id: 1 → 2".

## Where the entries go

`audit=True` keeps the log in a SQLite file of its own, `adminsite_audit.db` in the working
directory. It needs no setup and no migration.

That suits a single server. Each worker process or container gets its own file, though, so with
several of them, or with containers that are rebuilt, keep the log in your own database instead:

```python
from adminsite.audit import AuditLog

admin = Admin(engine, views=[OrderView], audit=AuditLog(engine))
```

The table is `adminsite_audit_log`, defined on `audit_metadata`. adminsite never creates a table in
your database uninvited, so add it to your migrations:

```python
# alembic/env.py
from adminsite.audit import audit_metadata

target_metadata = [Base.metadata, audit_metadata]
```

Or let adminsite create it, for a quick start:

```python
audit = AuditLog(engine, create_table=True)
```

A file of its own at another path works too: `AuditLog("sqlite:////var/lib/shop/audit.db")`.

## Bulk actions

A bulk action writes one entry for each record it touched, and the entries share a batch id. Every
record's history shows the action, and the batch can still be read as one event. This is how
Laravel Nova and Django's bulk delete record them.

The records are read before the action runs. "Mark every pending order as shipped" would find no
pending orders afterwards, so reading them first is the only way to know which ones it changed. For
`selection.update`, the old values are read in the same single query.

## What is never recorded

- A change that was rolled back. Entries are written only after the commit succeeds, so a hook that
  refuses a save leaves no trace.
- A save that changed nothing. Saving a form without edits writes no entry.

## Who did it

The user comes from your [auth provider](auth.md): whatever `identity(user)` returns, which is the
username for `PasswordAuth`. Without signing in, entries show "Someone".

## Reading it yourself

```python
entries = await admin.audit.history("orders", "42")
latest = await admin.audit.recent(view="orders", limit=20)

for entry in entries:
    print(entry.occurred_at, entry.user, entry.event, entry.changes)
```

`entry.changes` maps each field name to a pair of values, before and after. Times are stored in
UTC.
