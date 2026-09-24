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

New versions of adminsite add columns to this table. Every one of them may be empty, so a table
adminsite created itself gets them added the first time the log is used. For a table in your own
migrations, generate a migration after upgrading.

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

## Who did it, and from where

Each entry names the person twice. `entry.user` is the name the admin shows for them, and
`entry.user_key` is what your [auth provider](auth.md)'s `identity(user)` returns. The name reads
well; the key still finds the same person after their name changes. For `PasswordAuth` both are the
username. Without signing in, entries show "Someone".

Each entry also keeps the IP address and the browser's own description of itself, as `entry.ip`
and `entry.user_agent`. The History tab shows the address, and the browser when you point at it.
The address is the one your ASGI server worked out, so behind a proxy it is the visitor's only when
the server trusts that proxy, for example with uvicorn's `--forwarded-allow-ips`.

## Signing in

With a [sign in](auth.md) set up, the log also keeps every sign in, every failed attempt and every
sign out, each with its address and browser. A failed attempt is filed under the account when there
is one, and otherwise under the name that was typed. `PasswordAuth` says whether the username was
unknown or the password wrong, and your own provider gives its reason by raising `SignInRefused`
from `verify`. The reason stays in the log; the person signing in is never told it.

Signing in happens to no model, so these entries have an empty `view` and no view's permissions
decide who reads them. By default the Activity page shows them to someone who may read the history
of every model. Override `AuthProvider.may_read_sign_ins` to let others see them too.

## Reading it yourself

```python
from adminsite.audit import AuditEvent, AuditQuery

entries = await admin.audit.find(AuditQuery(view="orders", record_key="42"), limit=50)
deletes = await admin.audit.find(
    AuditQuery(user="nima", events=[AuditEvent.DELETED], since=last_monday), limit=100
)

for entry in entries:
    print(entry.occurred_at, entry.user, entry.event, entry.changes)
```

`entry.changes` maps each field name to a pair of values, before and after. Times are stored in
UTC. Every field of `AuditQuery` narrows the result, and `older_than`, the time and id of the last
entry you have, gives the page after it.

## Keeping the log somewhere else

The admin writes and reads the log through two methods, `record` and `find`, and anything that has
them can take `AuditLog`'s place: a table your application already has, or a service you send logs
to. The History tab and the Activity page read through `find`, so they show whatever your store
returns, including rows written before the admin existed.

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adminsite.audit import AuditEntry, AuditEvent, AuditQuery


class ChangeLogStore:
    """The admin's log, in the application's own change_log table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        async with self.sessions() as session:
            session.add_all(
                ChangeLog(
                    at=entry.occurred_at,
                    table_name=entry.view,
                    row_key=entry.record_key,
                    event=entry.event.value,
                    changes={name: list(pair) for name, pair in entry.changes.items()},
                    payload=dict(entry.inputs),
                    error=entry.error,
                    who=entry.user_key,
                    who_name=entry.user,
                    ip=entry.ip,
                    user_agent=entry.user_agent,
                )
                for entry in entries
            )
            await session.commit()

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        statement = select(ChangeLog).order_by(ChangeLog.at.desc(), ChangeLog.id.desc())
        if query.view is not None:
            statement = statement.where(ChangeLog.table_name == query.view)
        if query.record_key is not None:
            statement = statement.where(ChangeLog.row_key == query.record_key)
        # ...and the other fields of the query, the same way.
        async with self.sessions() as session:
            rows = await session.scalars(statement.limit(limit))
            return [
                AuditEntry(
                    id=row.id,
                    occurred_at=row.at,
                    view=row.table_name,
                    record_key=row.row_key,
                    event=AuditEvent(row.event),
                    changes={name: tuple(pair) for name, pair in row.changes.items()},
                    inputs=row.payload or {},
                    error=row.error,
                    user_key=row.who,
                    user=row.who_name,
                    ip=row.ip,
                    user_agent=row.user_agent,
                )
                for row in rows
            ]


admin = Admin(engine, views=[OrderView], audit=ChangeLogStore(sessions))
```

A row whose kind has no `AuditEvent` of its own can come back as `AuditEvent.ACTION`, with its name
in `action`. `diff` and `as_json`, which the admin uses to describe a change, are in
`adminsite.audit` too, for code that writes entries of its own.

A store can also wrap another. This one keeps the log as usual, without the addresses:

```python
from dataclasses import replace

from adminsite.audit import AuditLog, AuditStore


class WithoutAddresses:
    """The log as usual, but no IP address or browser is kept."""

    def __init__(self, store: AuditStore) -> None:
        self.store = store

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        await self.store.record(
            [replace(entry, ip=None, user_agent=None) for entry in entries]
        )

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        return await self.store.find(query, limit=limit)


admin = Admin(engine, views=[OrderView], audit=WithoutAddresses(AuditLog(engine)))
```
