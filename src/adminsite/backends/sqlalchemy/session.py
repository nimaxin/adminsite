import asyncio
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy import Engine, Result
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Executable

from adminsite.exceptions import AdminSiteError

T = TypeVar("T")

# Rows with any number of columns. SQLAlchemy 2.1 types a result column by
# column, where Result[Any] now means a single column.
Rows = Result[*tuple[Any, ...]]

SessionSource = (
    Engine | AsyncEngine | sessionmaker[Session] | async_sessionmaker[AsyncSession]
)

logger = logging.getLogger("adminsite")


async def _run_after(moment: str, work: Callable[[], Awaitable[None]]) -> None:
    """Run work that waited on a commit or a rollback, and log it if it fails.

    What it waited on has happened by now, so a failure here must not undo
    it or stop the rest of the waiting work.
    """
    try:
        await work()
    except Exception:
        logger.exception("Work that waited on a %s failed.", moment)


class SessionAdapter(ABC):
    """One way to talk to the database, whether the session is async or not."""

    def __init__(self) -> None:
        self._before_commit: list[Callable[[], Awaitable[None]]] = []
        self._after_commit: list[Callable[[], Awaitable[None]]] = []
        self._after_rollback: list[Callable[[], Awaitable[None]]] = []

    def before_commit(self, work: Callable[[], Awaitable[None]]) -> None:
        """Run some work inside the transaction, just before it commits.

        If the work fails, the transaction is rolled back instead, so what
        it writes, such as an audit entry in the same database, is saved
        with the change it describes or not at all.
        """
        self._before_commit.append(work)

    def after_commit(self, work: Callable[[], Awaitable[None]]) -> None:
        """Run some work once the next commit has succeeded.

        A rollback drops it, so nothing that runs here, such as an audit
        entry, can describe a change that never happened. Work that fails
        is written to the log and stops nothing else, since what it waited
        on is already committed.
        """
        self._after_commit.append(work)

    def after_rollback(self, work: Callable[[], Awaitable[None]]) -> None:
        """Run some work once the next rollback is done.

        The audit log writes down what failed this way: after the rollback,
        so the entry is not undone with the work it describes, and so it
        does not wait on locks the failed transaction still holds.
        """
        self._after_rollback.append(work)

    async def commit(self) -> None:
        """Commit the open transaction, then run the work waiting on it."""
        waiting, self._before_commit = self._before_commit, []
        try:
            for work in waiting:
                await work()
        except BaseException:
            await self.rollback()
            raise
        await self._commit()
        self._after_rollback = []
        waiting, self._after_commit = self._after_commit, []
        for work in waiting:
            await _run_after("commit", work)

    async def rollback(self) -> None:
        """Undo everything done since the last commit, then run what waits on it."""
        self._before_commit = []
        self._after_commit = []
        await self._rollback()
        waiting, self._after_rollback = self._after_rollback, []
        for work in waiting:
            await _run_after("rollback", work)

    @abstractmethod
    async def execute(self, statement: Executable) -> Rows:
        """Run a statement and return its result."""

    @abstractmethod
    async def scalars(self, statement: Executable) -> ScalarResult[Any]:
        """Run a statement and return the first column of every row."""

    @abstractmethod
    async def scalar(self, statement: Executable) -> Any:
        """Run a statement and return a single value."""

    @abstractmethod
    async def get(self, model: type[T], key: Any) -> T | None:
        """Load one record by primary key, or nothing if it is missing."""

    @abstractmethod
    async def add(self, record: Any) -> None:
        """Put a new record into the session."""

    @abstractmethod
    async def delete(self, record: Any) -> None:
        """Mark a record for deletion."""

    @abstractmethod
    async def flush(self) -> None:
        """Send pending changes to the database without committing."""

    @abstractmethod
    async def _commit(self) -> None:
        """Commit the open transaction."""

    @abstractmethod
    async def _rollback(self) -> None:
        """Undo everything done since the last commit."""

    @abstractmethod
    async def refresh(
        self, record: Any, attributes: Sequence[str] | None = None
    ) -> None:
        """Reload a record from the database."""

    @abstractmethod
    async def run(self, work: Callable[[Session], T]) -> T:
        """Run a function against the underlying sync session."""

    @abstractmethod
    async def close(self) -> None:
        """Release the session and its connection."""

    @abstractmethod
    def _plain(self) -> Session:
        """The plain SQLAlchemy session underneath."""

    @asynccontextmanager
    async def no_autoflush(self) -> AsyncIterator["SessionAdapter"]:
        """Hold back automatic flushes while a record is still being filled in.

        Loading a linked record normally flushes the session first, which
        would try to write a new record before all its values are set.
        """
        plain = self._plain()
        before = plain.autoflush
        plain.autoflush = False
        try:
            yield self
        finally:
            plain.autoflush = before

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["SessionAdapter"]:
        """Commit when the block ends, or roll back if it raises."""
        try:
            yield self
        except BaseException:
            await self.rollback()
            raise
        else:
            await self.commit()


class AsyncSessionAdapter(SessionAdapter):
    """Talks to an `AsyncSession` directly."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()
        self.session = session

    async def execute(self, statement: Executable) -> Rows:
        """Run a statement and return its result."""
        return await self.session.execute(statement)

    async def scalars(self, statement: Executable) -> ScalarResult[Any]:
        """Run a statement and return the first column of every row."""
        return await self.session.scalars(statement)

    async def scalar(self, statement: Executable) -> Any:
        """Run a statement and return a single value."""
        return await self.session.scalar(statement)

    async def get(self, model: type[T], key: Any) -> T | None:
        """Load one record by primary key, or nothing if it is missing."""
        return await self.session.get(model, key)

    async def add(self, record: Any) -> None:
        """Put a new record into the session."""
        self.session.add(record)

    async def delete(self, record: Any) -> None:
        """Mark a record for deletion."""
        await self.session.delete(record)

    async def flush(self) -> None:
        """Send pending changes to the database without committing."""
        await self.session.flush()

    async def _commit(self) -> None:
        await self.session.commit()

    async def _rollback(self) -> None:
        await self.session.rollback()

    async def refresh(
        self, record: Any, attributes: Sequence[str] | None = None
    ) -> None:
        """Reload a record from the database."""
        await self.session.refresh(record, attributes)

    async def run(self, work: Callable[[Session], T]) -> T:
        """Run a function against the underlying sync session."""
        return await self.session.run_sync(lambda session: work(session))

    async def close(self) -> None:
        """Release the session and its connection."""
        await self.session.close()

    def _plain(self) -> Session:
        return self.session.sync_session


class SyncSessionAdapter(SessionAdapter):
    """Talks to a plain `Session`, keeping it on one worker thread."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self._worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="adminsite-db"
        )

    async def execute(self, statement: Executable) -> Rows:
        """Run a statement and return its result."""
        return await self.run(lambda session: session.execute(statement))

    async def scalars(self, statement: Executable) -> ScalarResult[Any]:
        """Run a statement and return the first column of every row."""
        return await self.run(lambda session: session.scalars(statement))

    async def scalar(self, statement: Executable) -> Any:
        """Run a statement and return a single value."""
        return await self.run(lambda session: session.scalar(statement))

    async def get(self, model: type[T], key: Any) -> T | None:
        """Load one record by primary key, or nothing if it is missing."""
        return await self.run(lambda session: session.get(model, key))

    async def add(self, record: Any) -> None:
        """Put a new record into the session."""
        await self.run(lambda session: session.add(record))

    async def delete(self, record: Any) -> None:
        """Mark a record for deletion."""
        await self.run(lambda session: session.delete(record))

    async def flush(self) -> None:
        """Send pending changes to the database without committing."""
        await self.run(lambda session: session.flush())

    async def _commit(self) -> None:
        await self.run(lambda session: session.commit())

    async def _rollback(self) -> None:
        await self.run(lambda session: session.rollback())

    async def refresh(
        self, record: Any, attributes: Sequence[str] | None = None
    ) -> None:
        """Reload a record from the database."""
        await self.run(lambda session: session.refresh(record, attributes))

    async def run(self, work: Callable[[Session], T]) -> T:
        """Run a function on the thread that owns this session."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._worker, work, self.session)

    async def close(self) -> None:
        """Release the session, its connection and its worker thread."""
        try:
            await self.run(lambda session: session.close())
        finally:
            self._worker.shutdown(wait=False)

    def _plain(self) -> Session:
        return self.session


class Database:
    """Hands out sessions, hiding whether the engine is async or not."""

    def __init__(self, source: SessionSource) -> None:
        self.is_async = isinstance(source, AsyncEngine | async_sessionmaker)
        self._factory = self._factory_for(source)
        # A session factory names its engine as its bind, if it has one.
        bind = source if isinstance(source, Engine | AsyncEngine) else None
        if bind is None and isinstance(source, sessionmaker | async_sessionmaker):
            found = source.kw.get("bind")
            bind = found if isinstance(found, Engine | AsyncEngine) else None
        self.engine: Engine | AsyncEngine | None = bind

    def same_as(self, other: "Database") -> bool:
        """Whether both talk to one database, so one transaction can hold both.

        The same engine is one database, and so are two engines at one
        address, such as an async and a sync engine on one file. Two SQLite
        databases in memory are two, however alike their addresses.
        """
        if self.engine is None or other.engine is None:
            return False
        if self.engine is other.engine:
            return True
        mine = _address(self.engine)
        return mine is not None and mine == _address(other.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[SessionAdapter]:
        """Open a session and close it when the block ends."""
        adapter = self.open()
        try:
            yield adapter
        finally:
            await adapter.close()

    def open(self) -> SessionAdapter:
        """Open a session the caller is responsible for closing."""
        session = self._factory()
        if isinstance(session, AsyncSession):
            return AsyncSessionAdapter(session)
        return SyncSessionAdapter(session)

    def _factory_for(
        self, source: SessionSource
    ) -> Callable[[], Session | AsyncSession]:
        if isinstance(source, AsyncEngine):
            return async_sessionmaker(source, expire_on_commit=False)
        if isinstance(source, Engine):
            return sessionmaker(source, expire_on_commit=False)
        if isinstance(source, async_sessionmaker | sessionmaker):
            return source
        raise AdminSiteError(
            f"Pass an engine or a session factory, not {type(source).__name__}."
        )


def _address(engine: Engine | AsyncEngine) -> tuple[Any, ...] | None:
    """Where an engine's database is, whatever driver reaches it.

    None for a SQLite database in memory, which only its own engine sees.
    """
    url = engine.url
    backend = url.get_backend_name()
    database = url.database or ""
    if backend == "sqlite":
        if database in ("", ":memory:") or url.query.get("mode") == "memory":
            return None
        database = os.path.abspath(database)
    return (backend, url.username, url.host, url.port, database)
