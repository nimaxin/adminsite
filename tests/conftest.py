import os
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
from jinja2 import BytecodeCache, Environment
from jinja2.bccache import Bucket
from sqlalchemy import Connection, Engine, Table, create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from adminsite.backends.sqlalchemy import Database
from adminsite.http import templating
from tests.factories import build_sample_data
from tests.models import Base
from tests.support import Backend

# One in-memory database per test, kept alive by a single pooled connection.
SYNC_URL = "sqlite://"
ASYNC_URL = "sqlite+aiosqlite://"

# Set this to also run every database test against Postgres, for example
# postgresql://adminsite:adminsite@localhost:55432/adminsite
POSTGRES_URL = os.environ.get("ADMINSITE_POSTGRES_URL", "")

# And this for MySQL, for example
# mysql://adminsite:adminsite@localhost:53306/adminsite
MYSQL_URL = os.environ.get("ADMINSITE_MYSQL_URL", "")

BACKENDS = ["async", "sync"]
if POSTGRES_URL:
    BACKENDS += ["postgres-async", "postgres-sync"]
if MYSQL_URL:
    BACKENDS += ["mysql-async", "mysql-sync"]


def enforce_foreign_keys(engine: Engine) -> None:
    """Make SQLite check foreign keys, which it skips unless asked.

    Postgres always checks them, so without this a delete that breaks a
    link passes on SQLite and fails in production.
    """

    @event.listens_for(engine, "connect")
    def switch_on(connection: object, record: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def postgres_url(driver: str) -> str:
    """The Postgres address with the driver SQLAlchemy should use."""
    return POSTGRES_URL.replace("postgresql://", f"postgresql+{driver}://", 1)


def mysql_url(driver: str) -> str:
    """The MySQL address with the driver SQLAlchemy should use."""
    return MYSQL_URL.replace("mysql://", f"mysql+{driver}://", 1)


class CompiledTemplates(BytecodeCache):
    """Each compiled template, kept in memory for every admin the tests build."""

    def __init__(self) -> None:
        self.compiled: dict[str, bytes] = {}

    def load_bytecode(self, bucket: Bucket) -> None:
        code = self.compiled.get(bucket.key)
        if code is not None:
            bucket.bytecode_from_string(code)

    def dump_bytecode(self, bucket: Bucket) -> None:
        self.compiled[bucket.key] = bucket.bytecode_to_string()


COMPILED_TEMPLATES = CompiledTemplates()


class SharedEnvironment(Environment):
    """The admin's template environment, with the templates compiled once."""

    def __init__(self, **options: Any) -> None:
        super().__init__(bytecode_cache=COMPILED_TEMPLATES, **options)


@pytest.fixture(scope="session", autouse=True)
def templates_compiled_once() -> Iterator[None]:
    """Compile each template once per run, not once per test.

    Every test builds its own admin, and each one compiled every template
    again, which took most of a test's time. A template whose source
    changes is compiled again, since Jinja checks it against the cache.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(templating, "Environment", SharedEnvironment)
        yield


@pytest.fixture
def sync_engine() -> Iterator[Engine]:
    # A sync session runs on a worker thread, so SQLite has to allow the
    # connection to move. Only one thread uses it at a time.
    engine = create_engine(
        SYNC_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    enforce_foreign_keys(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(build_sample_data())
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def sync_session(sync_engine: Engine) -> Iterator[Session]:
    with Session(sync_engine) as session:
        yield session


@pytest.fixture
def sync_session_factory(sync_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(sync_engine, expire_on_commit=False)


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(ASYNC_URL, poolclass=StaticPool)
    enforce_foreign_keys(engine.sync_engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add_all(build_sample_data())
        await session.commit()
    yield engine
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(async_engine) as session:
        yield session


@pytest.fixture
def async_session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, expire_on_commit=False)


def fresh_tables(url: str) -> list[Table]:
    """Make the tables on a database server, once per run."""
    engine = create_engine(url)
    # Dropping them first picks up any change to the models since last time.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    return list(Base.metadata.sorted_tables)


def empty(connection: Connection, tables: Sequence[Table]) -> None:
    """Empty the tables and start their keys at 1 again.

    Much quicker than dropping and making them again, above all on MySQL,
    and every test still sees the same keys as it would on SQLite.
    """
    quote = connection.dialect.identifier_preparer.format_table
    if connection.dialect.name == "postgresql":
        names = ", ".join(quote(table) for table in tables)
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        return
    # MySQL's TRUNCATE drops the table and makes it again, which is no
    # quicker. Deleting the rows, children first, and resetting the counter is.
    for table in reversed(tables):
        connection.execute(table.delete())
        if table.autoincrement_column is not None:
            connection.execute(text(f"ALTER TABLE {quote(table)} AUTO_INCREMENT = 1"))


@pytest.fixture(scope="session")
def postgres_tables() -> list[Table]:
    return fresh_tables(postgres_url("psycopg"))


@pytest.fixture(scope="session")
def mysql_tables() -> list[Table]:
    return fresh_tables(mysql_url("pymysql"))


@pytest.fixture
def postgres_sync_engine(postgres_tables: list[Table]) -> Iterator[Engine]:
    engine = create_engine(postgres_url("psycopg"))
    with engine.begin() as connection:
        empty(connection, postgres_tables)
    with Session(engine) as session:
        session.add_all(build_sample_data())
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
async def postgres_async_engine(
    postgres_tables: list[Table],
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url("asyncpg"))
    async with engine.begin() as connection:
        await connection.run_sync(empty, postgres_tables)
    async with AsyncSession(engine) as session:
        session.add_all(build_sample_data())
        await session.commit()
    yield engine
    await engine.dispose()


@pytest.fixture
def mysql_sync_engine(mysql_tables: list[Table]) -> Iterator[Engine]:
    engine = create_engine(mysql_url("pymysql"))
    with engine.begin() as connection:
        empty(connection, mysql_tables)
    with Session(engine) as session:
        session.add_all(build_sample_data())
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
async def mysql_async_engine(mysql_tables: list[Table]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(mysql_url("aiomysql"))
    async with engine.begin() as connection:
        await connection.run_sync(empty, mysql_tables)
    async with AsyncSession(engine) as session:
        session.add_all(build_sample_data())
        await session.commit()
    yield engine
    await engine.dispose()


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> Backend:
    """The same data behind every engine under test, async and sync."""
    if request.param == "async":
        engine: AsyncEngine = request.getfixturevalue("async_engine")
        return Backend(Database(engine), engine.sync_engine, is_async=True)
    if request.param == "postgres-async":
        remote: AsyncEngine = request.getfixturevalue("postgres_async_engine")
        return Backend(Database(remote), remote.sync_engine, is_async=True)
    if request.param == "postgres-sync":
        plain: Engine = request.getfixturevalue("postgres_sync_engine")
        return Backend(Database(plain), plain, is_async=False)
    if request.param == "mysql-async":
        mysql: AsyncEngine = request.getfixturevalue("mysql_async_engine")
        return Backend(Database(mysql), mysql.sync_engine, is_async=True)
    if request.param == "mysql-sync":
        mysql_plain: Engine = request.getfixturevalue("mysql_sync_engine")
        return Backend(Database(mysql_plain), mysql_plain, is_async=False)
    sync: Engine = request.getfixturevalue("sync_engine")
    return Backend(Database(sync), sync, is_async=False)


@pytest.fixture
def database(backend: Backend) -> Database:
    """The database behind the current backend."""
    return backend.database
