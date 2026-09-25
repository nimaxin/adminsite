import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.audit import (
    AuditEntry,
    AuditEvent,
    AuditLog,
    AuditQuery,
    audit_metadata,
)
from adminsite.audit.store import record_or_warn
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from tests.models import Order


class OrderView(ModelView, model=Order):
    list_display = ("id", "note")
    form_fields = ("note",)


class Broken:
    """A store that fails every write, as one on a full disk would."""

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        raise OSError("No space left on the device.")

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        return []


@pytest.fixture
async def kept(database: Database) -> AuditLog:
    """A log in the database under test, starting empty on every engine."""

    def start_over(session: Session) -> None:
        audit_metadata.drop_all(session.connection())

    async with database.session() as session:
        await session.run(start_over)
        await session.commit()
    return AuditLog(database, create_table=True)


def serve(admin: Admin, *, errors_as_pages: bool = False) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=not errors_as_pages
        ),
        base_url="http://testserver",
    )


async def change_note(client: httpx.AsyncClient, note: str) -> httpx.Response:
    form = await client.get("/admin/orders/1/edit")
    token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
    assert token is not None
    return await client.post(
        "/admin/orders/1/edit", data={"_csrf": token.group(1), "note": note}
    )


async def note_of(database: Database) -> str | None:
    async with database.session() as session:
        order = await session.get(Order, 1)
        assert order is not None
        return order.note


class TestInYourOwnDatabase:
    def test_the_admin_writes_entries_with_the_change(
        self, database: Database, kept: AuditLog
    ) -> None:
        admin = Admin(database, views=[OrderView], audit=kept, secret_key="s")

        assert admin.views.get("orders").audit_with_changes

    async def test_a_change_and_its_entry_are_saved_together(
        self, database: Database, kept: AuditLog
    ) -> None:
        admin = Admin(database, views=[OrderView], audit=kept, secret_key="s")
        async with serve(admin) as client:
            answer = await change_note(client, "Gift wrap")

        assert answer.status_code == 303
        assert await note_of(database) == "Gift wrap"
        entry = (await kept.history("orders", "1"))[0]
        assert entry.changes["note"] == ("", "Gift wrap")

    async def test_an_entry_that_cannot_be_written_undoes_the_change(
        self, database: Database, kept: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fail(*args: Any) -> None:
            raise OSError("The audit table is gone.")

        monkeypatch.setattr(kept, "record_within", fail)
        admin = Admin(database, views=[OrderView], audit=kept, secret_key="s")
        async with serve(admin, errors_as_pages=True) as client:
            answer = await change_note(client, "Gift wrap")

        assert answer.status_code == 500
        assert await note_of(database) is None


class TestInASeparateDatabase:
    async def test_a_log_that_fails_leaves_the_change_saved(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        admin = Admin(database, views=[OrderView], audit=Broken(), secret_key="s")
        async with serve(admin) as client:
            answer = await change_note(client, "Gift wrap")

        assert not admin.views.get("orders").audit_with_changes
        assert answer.status_code == 303
        assert await note_of(database) == "Gift wrap"
        assert "could not be written: updated orders 1." in caplog.text

    async def test_signing_in_still_works(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        admin = Admin(
            database,
            views=[OrderView],
            audit=Broken(),
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key="s",
        )
        async with serve(admin) as client:
            page = await client.get("/admin/login")
            token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
            assert token is not None
            answer = await client.post(
                "/admin/login",
                data={
                    "username": "nima",
                    "password": "letmein",
                    "_csrf": token.group(1),
                },
            )

        assert answer.status_code == 303
        assert "could not be written: signed_in." in caplog.text


class TestWorkAroundACommit:
    async def test_work_after_it_that_fails_stops_nothing_else(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        done: list[str] = []

        async def fail() -> None:
            raise OSError("The file is locked.")

        async def tidy() -> None:
            done.append("tidied")

        async with database.session() as session:
            session.after_commit(fail)
            session.after_commit(tidy)
            await session.commit()

        assert done == ["tidied"]
        assert "Work that waited on a commit failed." in caplog.text

    async def test_work_before_it_that_fails_undoes_the_change(
        self, database: Database
    ) -> None:
        async def fail() -> None:
            raise OSError("The audit table is gone.")

        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            order.note = "Gift wrap"
            await session.flush()
            session.before_commit(fail)
            with pytest.raises(OSError, match="audit table"):
                await session.commit()

        assert await note_of(database) is None


class TestOneDatabaseOrTwo:
    def test_one_engine_is_one_database(self) -> None:
        engine = create_engine("sqlite://")

        assert Database(engine).same_as(Database(engine))

    def test_two_engines_on_one_file_are_one_database(self, tmp_path: Path) -> None:
        file = tmp_path / "shop.db"
        sync = create_engine(f"sqlite:///{file}")
        async_ = create_async_engine(f"sqlite+aiosqlite:///{file}")

        assert Database(sync).same_as(Database(async_))

    def test_two_databases_in_memory_are_two(self) -> None:
        first = Database(create_engine("sqlite://"))
        second = Database(create_engine("sqlite://"))

        assert not first.same_as(second)

    def test_two_files_are_two(self, tmp_path: Path) -> None:
        shop = Database(create_engine(f"sqlite:///{tmp_path / 'shop.db'}"))
        log = Database(create_engine(f"sqlite:///{tmp_path / 'audit.db'}"))

        assert not shop.same_as(log)


class TestTheLoggedName:
    async def test_it_names_the_entries_that_were_lost(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        entries = [
            AuditEntry(view="orders", record_key=str(key), event=AuditEvent.DELETED)
            for key in range(1, 8)
        ]

        with caplog.at_level(logging.ERROR, logger="adminsite"):
            await record_or_warn(Broken(), entries)

        assert "deleted orders 1, deleted orders 2" in caplog.text
        assert "and 2 more." in caplog.text
