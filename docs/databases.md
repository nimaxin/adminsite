# Databases

## Async or sync

Give the admin whichever you already have:

```python
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from adminsite import Admin

async_engine = create_async_engine("postgresql+asyncpg://localhost/shop")
sync_engine = create_engine("postgresql+psycopg://localhost/shop")

Admin(async_engine)
Admin(sync_engine)
Admin(async_sessionmaker(async_engine))
Admin(sessionmaker(sync_engine))
```

Everything above the session is written once, so both behave the same. A sync session runs on a
single worker thread for its whole life, which keeps SQLAlchemy from ever being used by two threads
at once.

The sessions the admin opens keep their records loaded after a commit, as if made with
`expire_on_commit=False`, whatever the factory you give says. The pages and the
[committed hooks](hooks.md#once-the-change-is-committed) read a record once it is saved, and an
async session cannot load it again there.

## Tested on

- SQLite, with `aiosqlite` and with the standard library driver
- Postgres, with `asyncpg` and with `psycopg`
- MySQL 8, with `aiomysql` and with `pymysql`

The whole test suite runs against each of these in CI.

## MySQL

Two things behave differently on MySQL, and adminsite accounts for both:

- A native `ENUM` column sorts in the order its values were declared but compares as text. Keyset
  pagination would skip rows on such a column, so a list sorted by an enum uses page numbers
  instead.
- `CountMode.ESTIMATED` reads `information_schema.tables`, whose row count for InnoDB is itself an
  estimate and can be off by a lot on a table that changes often. Below 10,000 rows the count is
  exact anyway.

If you write a [filter](filters.md) of your own, note that MySQL refuses a `LIMIT` straight inside
`IN (...)`; wrap the limited select in a subquery first.

## SQLite with a sync engine

A sync session runs on a worker thread, and SQLite refuses a connection that moves between threads
unless told otherwise:

```python
from sqlalchemy import create_engine

engine = create_engine("sqlite:///shop.db", connect_args={"check_same_thread": False})
```

Only one thread uses the connection at a time, so this is safe.

## Foreign keys

Postgres always enforces foreign keys. SQLite does not, unless you switch it on for each
connection:

```python
from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry


@event.listens_for(engine, "connect")
def enforce_foreign_keys(
    connection: DBAPIConnection, record: ConnectionPoolEntry
) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

With an async engine, listen on `engine.sync_engine`: SQLAlchemy takes no listener on the async
engine itself.

With them on, deleting a record that others still point at is refused, and the admin shows "This
customer cannot be deleted, because other records still refer to it."

Bulk deletes from an [action](actions.md) are single statements and never load the records, so
they rely on the database for cascades. Give child tables `ondelete="CASCADE"` where children should
go with their parent:

```python
order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
```

## When the database refuses a change

A save the database refuses, because a value that must be unique is already taken or a required
value is missing, is shown on the form, and nothing is saved. A refused delete is shown above the
list. It says other records still refer to the record only when the database refused the delete
itself. When it refused something else in the same transaction, such as a row a [hook](hooks.md)
wrote, it says "This customer could not be deleted." and names the likely causes, as a save does.
The database's own message is never shown or written to the audit log, since it can hold the values
it refused.

In code of your own, such as a [page](pages.md) or an [action](actions.md), `commit()` and
`transaction()` on adminsite's session raise `adminsite.exceptions.IntegrityError` for such a
change, with SQLAlchemy's error as its cause:

```python
from adminsite.exceptions import IntegrityError


async def add_customer(name: str, email: str) -> str:
    async with admin.database.session() as session:
        try:
            async with session.transaction():
                await session.add(Customer(name=name, email=email))
        except IntegrityError:
            return "That email is already taken."
    return "Customer added."
```

Inside the block, `flush()` and `execute()` raise SQLAlchemy's own error; the transaction turns it
into adminsite's as it leaves the block. An action that lets the error through is not done, and the
list says why.

## After a rollback

When a save is refused or fails, SQLAlchemy expires the objects in the session, so reading an
attribute afterwards goes back to the database. With an async session, that raises
`MissingGreenlet`. In your own code around a failed save, read what you need first, or load the
record again.
