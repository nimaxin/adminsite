import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from adminsite.backends.sqlalchemy import Database
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


@pytest.fixture
def postgres_sync_engine() -> Iterator[Engine]:
    engine = create_engine(postgres_url("psycopg"))
    # Dropping and creating again resets the sequences, so every test
    # sees the same keys as it would on SQLite.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(build_sample_data())
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
async def postgres_async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(postgres_url("asyncpg"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add_all(build_sample_data())
        await session.commit()
    yield engine
    await engine.dispose()


@pytest.fixture
def mysql_sync_engine() -> Iterator[Engine]:
    engine = create_engine(mysql_url("pymysql"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(build_sample_data())
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
async def mysql_async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(mysql_url("aiomysql"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
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
