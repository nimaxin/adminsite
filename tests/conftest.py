from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, create_engine
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

# One in-memory database per test, kept alive by a single pooled connection.
SYNC_URL = "sqlite://"
ASYNC_URL = "sqlite+aiosqlite://"


@pytest.fixture
def sync_engine() -> Iterator[Engine]:
    # A sync session runs on a worker thread, so SQLite has to allow the
    # connection to move. Only one thread uses it at a time.
    engine = create_engine(
        SYNC_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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


@pytest.fixture(params=["async", "sync"])
def database(request: pytest.FixtureRequest) -> Database:
    """The same database twice, once behind an async engine and once sync."""
    engine = request.getfixturevalue(f"{request.param}_engine")
    return Database(engine)
