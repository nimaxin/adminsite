# Databases

adminsite works with the engine you already have, async or sync, on SQLite, Postgres and MySQL.

## Async or sync

Give the admin an engine or a session factory, whichever you have:

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

Both behave the same, and the hooks and actions you write work with either.

## Tested on

- SQLite, with `aiosqlite` and with the standard library driver
- Postgres, with `asyncpg` and with `psycopg`
- MySQL 8, with `aiomysql` and with `pymysql`

The whole test suite runs against each of these in CI.

## SQLModel

A SQLModel class with `table=True` is a SQLAlchemy model, so the admin works with it as it is.
A type checker reads a SQLModel attribute such as `Book.pages` as the `int` it holds, not as a
column, so name the columns as strings instead. The admin checks each name when it starts.

```python
from sqlmodel import Field, SQLModel

from adminsite import ModelView


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=200)
    pages: int = 0


class BookView(ModelView[Book]):
    fields = ["title", "pages"]
    searchable_fields = ["title"]
```

## SQLite with a sync engine

A sync session runs on a worker thread, and SQLite refuses a connection that moves between threads
unless told otherwise. Only one thread uses the connection at a time, so this is safe:

```python
from sqlalchemy import create_engine

engine = create_engine("sqlite:///shop.db", connect_args={"check_same_thread": False})
```

## Foreign keys on SQLite

Postgres and MySQL enforce foreign keys. SQLite does only when you switch it on for each
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

With an async engine, listen on `engine.sync_engine`, since SQLAlchemy takes no listener on the
async engine itself. Deleting a record that others still point at is then refused with a message.

Bulk deletes from an [action](actions.md) never load the records, so they rely on the database for
cascades. Give a child table's foreign key `ondelete="CASCADE"` where children should go with their
parent:

```python
order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
```

## MySQL

adminsite handles two of MySQL's differences for you. A list sorted by a native `ENUM` column uses
page numbers, since keyset paging would skip rows there, and `CountMode.ESTIMATED` reads InnoDB's
own row count, which can be far off on a table that changes often.

In a [filter](filters.md) of your own, keep in mind that MySQL refuses a `LIMIT` straight inside
`IN (...)`. Wrap the limited select in a subquery first.

## When the database refuses a change

A save the database refuses, such as a unique value that is already taken, shows on the form, and
nothing is saved. A refused delete shows above the list.

In code of your own, such as a [page](pages.md) or an [action](actions.md), `commit()` and
`transaction()` on adminsite's session raise `adminsite.exceptions.IntegrityError` for such a
change:

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

Inside the block, `flush()` and `execute()` raise SQLAlchemy's own error, which the transaction
turns into adminsite's as it leaves the block.

## After a rollback

When a save fails, SQLAlchemy expires the objects in the session, so reading an attribute
afterwards goes back to the database. With an async session that raises `MissingGreenlet`. In
your own code around a failed save, read what you need first, or load the record again.
