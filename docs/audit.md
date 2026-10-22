# Audit log

Switch it on and adminsite writes down every create, change and delete: who did it, when, and what
each field was before and after.

The examples on this page use the [example models](models.md).

```python
admin = Admin(engine, views=[OrderView, CustomerView], audit=True)
```

Every record's page gets a **History** tab, and the sidebar gets an **Activity** page listing
everything across the admin. Values are written the way the admin shows them, so a change reads
"Customer: Lena Fischer → Marco Rossi" rather than "customer_id: 1 → 2". A save that changed
nothing, or was rolled back, writes no entry.

## Secrets

A field whose name says it holds a secret, such as `password_hash` or `api_key`, is kept as `***`,
so the log shows that it changed and never what it holds. Give a field `secret=True` to hide a
value whose name does not say so, or `secret=False` to keep one whose name only looks secret:

```python
from adminsite import Field


class WalletView(ModelView[Wallet]):
    fields = [Wallet.name, Field(Wallet.seed_ciphertext, secret=True)]
```

The values an [action](#actions) is run with follow the same rule. A field kept from someone by
[`can_access_field`](permissions.md#fields) is still written down, but that person does not see
its values.

## Where the log is kept

`audit=True` keeps the log in a SQLite file of its own, `adminsite_audit.db` in the working
directory. It needs no setup and no migration.

!!! warning "The default log can miss a change"

    Your data and the default log are in two databases, so they cannot be saved in one transaction.
    Each entry is saved after its change, and if that fails, the change stays but is not logged.

With several worker processes or containers, each gets a file of its own. In both cases, keep the
log in your own database, where each change and its entries are saved together:

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

Or let adminsite create it, for a quick start, with `AuditLog(engine, create_table=True)`. New
versions of adminsite may add columns: a table adminsite created gets them by itself, and for one
in your migrations, generate a migration after upgrading.

## Actions

Every action run is written down:

- An action on one record writes an entry for it, with the fields it changed.
- A bulk action writes an entry for each record it touched, and the entries share a batch id.
- An action on the whole view writes one entry, with no record.

Each entry keeps the values the action was run with, in `entry.inputs`, with [secrets](#secrets)
as `***` and a file by its name, type and size only. It keeps what the action answered too, unless
the answer holds a secret shown once, such as a new API key:

```python
class AccountView(ModelView[Account]):
    @action("Rotate the key", on="record", audit_answer=False)
    async def rotate(self, account: Account, session: SessionAdapter) -> str:
        key = await issue_key(session, account)
        return f"The new key is {key}. Copy it now: it is not shown again."
```

An action that is refused or fails is written down as failed, with the reason in `entry.error`.

A CSV export writes one entry, with the list's search, filters, sort and columns in
`entry.inputs`.

## Who did it, and from where

`entry.user` is the name the admin shows for the person, and `entry.user_key` is what your
[auth provider](auth.md)'s `identity(user)` returns, which still finds them after their name
changes. Each entry keeps the IP address and the browser too, as `entry.ip` and
`entry.user_agent`. Behind a proxy, the address is the visitor's only when your server trusts that
proxy, as with uvicorn's `--forwarded-allow-ips`.

With a [sign in](auth.md) set up, the log keeps every sign in, failed attempt and sign out. By
default the Activity page shows them to whoever may read the history of every view, and with
[`AdminUsers`](auth.md#users-and-roles-in-the-admin) to every superuser.
`AuthProvider.may_read_sign_ins` changes who.

## The Activity page

The Activity page lists the log newest first. Each person sees what their
[permissions](permissions.md#history) let them read. Its tabs narrow it to one view, and its filters
to a kind of entry, a person, a range of days or one record.

## Read the log yourself

Keep the log you give the admin, and read from it:

```python
from adminsite.audit import AuditEvent, AuditLog, AuditQuery

audit = AuditLog(engine)
admin = Admin(engine, views=[OrderView], audit=audit)

entries = await audit.find(AuditQuery(view="orders", record_key="42"), limit=50)
deletes = await audit.find(
    AuditQuery(user="nima", events=[AuditEvent.DELETED], since=last_monday), limit=100
)

for entry in entries:
    print(entry.occurred_at, entry.user, entry.event, entry.changes)
```

`entry.changes` maps each field name to its value before and after. Times are in UTC. Every field
of `AuditQuery` narrows the result, and `older_than`, the time and id of the last entry you have,
gives the page after it.

## Keep the log somewhere else

The admin writes and reads the log through two methods, `record` and `find`, and anything that has
them can take `AuditLog`'s place, such as a table your application already has:

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adminsite.audit import AuditEntry, AuditEvent, AuditQuery


def change_log_row(entry: AuditEntry) -> ChangeLog:
    """The entry as a row of the change_log table."""
    return ChangeLog(
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


class ChangeLogStore:
    """The admin's log, in the application's own change_log table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        async with self.sessions() as session:
            session.add_all(change_log_row(entry) for entry in entries)
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


admin = Admin(engine, views=[OrderView], audit=ChangeLogStore(session_factory))
```

This store writes after the change has committed, so a failure leaves the change saved but not
logged. A store that keeps its entries in the admin's own database can join the transaction
instead, with two more methods:

```python
from adminsite.database import Database, SessionAdapter


class SharedChangeLogStore(ChangeLogStore):
    """The same log, written in the transaction of the change it describes."""

    def lives_in(self, database: Database) -> bool:
        """Whether the entries are kept in that database."""
        return database.same_as(Database(self.sessions))

    async def record_within(
        self, session: SessionAdapter, entries: Sequence[AuditEntry]
    ) -> None:
        """Write the entries through the admin's session, without committing."""
        for entry in entries:
            await session.add(change_log_row(entry))
```

A store can also wrap another. This one keeps the log as usual, without the addresses. A wrapper
that should write in the transaction passes `lives_in` and `record_within` on as well.

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
