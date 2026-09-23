"""Bulk actions on a table whose key is two columns."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from adminsite.audit import AuditLog
from adminsite.backends.sqlalchemy import Database
from adminsite.backends.sqlalchemy.session import SessionAdapter
from tests.models import Shelf


class ShelfView(ModelView, model=Shelf):
    name = "shelves"
    list_display = ("aisle", "slot", "label")
    ordering = ("aisle", "slot")

    @action("Relabel")
    async def relabel(self, selection: Selection) -> str:
        return f"{await selection.update(label='Moved')} relabelled."

    @action("Clear")
    async def clear(self, selection: Selection) -> str:
        return f"{await selection.delete()} cleared."


def chosen(view: ModelView, session: SessionAdapter, *keys: str) -> Selection:
    return Selection(view=view, session=session, spec=view.build_spec(), keys=keys)


async def labels(session: SessionAdapter) -> dict[str, str]:
    rows = await session.execute(select(Shelf.aisle, Shelf.slot, Shelf.label))
    return {f"{aisle},{slot}": label for aisle, slot, label in rows.all()}


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[ShelfView], secret_key="for-the-session")
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestWhatASelectionCovers:
    async def test_one_key_is_one_row(self, database: Database) -> None:
        view = ShelfView()
        async with database.session() as session:
            selection = chosen(view, session, "A,1")

            assert await selection.count() == 1
            assert await selection.covered_keys() == ["A,1"]
            assert [str(row) for row in await selection.records()] == ["A1"]

    async def test_a_key_with_a_part_missing_covers_nothing(
        self, database: Database
    ) -> None:
        view = ShelfView()
        async with database.session() as session:
            assert await chosen(view, session, "A").count() == 0
            assert await chosen(view, session, "A,x").count() == 0

    async def test_an_update_stays_on_the_row_that_was_ticked(
        self, database: Database
    ) -> None:
        """Rows sharing the first half of the key are left alone."""
        view = ShelfView()
        async with database.session() as session:
            changed = await chosen(view, session, "A,1").update(label="Moved")
            await session.commit()

            assert changed == 1
            assert await labels(session) == {
                "A,1": "Moved",
                "A,2": "Scarves",
                "B,1": "Bags",
            }

    async def test_a_delete_does_too(self, database: Database) -> None:
        view = ShelfView()
        async with database.session() as session:
            gone = await chosen(view, session, "A,2", "B,1").delete()
            await session.commit()

            assert gone == 2
            assert list(await labels(session)) == ["A,1"]

    async def test_everything_that_matches_still_means_everything(
        self, database: Database
    ) -> None:
        view = ShelfView()
        async with database.session() as session:
            selection = Selection(
                view=view, session=session, spec=view.build_spec(), everything=True
            )

            assert await selection.count() == 3


class TestFromTheList:
    async def test_the_ticked_row_is_the_one_acted_on(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/shelves")
        token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        assert token is not None

        answer = await client.post(
            "/admin/shelves/action/relabel",
            data={"_csrf": token.group(1), "keys": ["A,2"]},
        )

        assert answer.status_code == 303
        async with database.session() as session:
            assert await labels(session) == {
                "A,1": "Shirts",
                "A,2": "Moved",
                "B,1": "Bags",
            }


class TestTheLog:
    async def test_it_names_the_whole_key(
        self, database: Database, tmp_path: Path
    ) -> None:
        log = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
        try:
            admin = Admin(
                database, views=[ShelfView], audit=log, secret_key="for-the-session"
            )
            app = Starlette()
            app.mount("/admin", admin)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                page = await client.get("/admin/shelves")
                token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
                assert token is not None
                await client.post(
                    "/admin/shelves/action/relabel",
                    data={"_csrf": token.group(1), "keys": ["A,2"]},
                )

            entries = await log.recent()
        finally:
            log.close()

        assert [entry.record_key for entry in entries] == ["A,2"]
        assert entries[0].changes["label"] == ("Scarves", "Moved")
