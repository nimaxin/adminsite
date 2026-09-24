import re
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RefusedError
from adminsite.views import model_view
from adminsite.views.writing import DeleteContext
from tests.models import Product


class ProductView(ModelView, model=Product):
    list_display = ("id", "name")
    search_fields = ("name",)

    async def before_delete(self, context: DeleteContext) -> None:
        if context.record.name == "Keep me":
            raise RefusedError("This one stays in the catalogue.")


class LockedProducts(ModelView, model=Product):
    name = "locked"

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == Permission.DELETE and record is not None:
            return bool(record.name != "Locked")
        return await super().allows(action, request=request, record=record)


class ReadOnlyProducts(ModelView, model=Product):
    name = "read_only"
    can_delete = False


class OneAtATime(ModelView, model=Product):
    name = "one_at_a_time"
    bulk_delete = False


class OwnDelete(ModelView, model=Product):
    name = "own_delete"

    @action("Archive instead", name="delete_selected")
    async def archive(self, selection: Selection) -> str:
        return f"{await selection.count()} archived."


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(
        database,
        views=[ProductView, LockedProducts, ReadOnlyProducts, OneAtATime, OwnDelete],
        audit=log,
        secret_key="for-the-session",
    )
    app = Starlette()
    app.mount("/admin", site)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def add_products(database: Database, *names: str) -> list[str]:
    """Products no order refers to, so only the admin's rules can keep them."""
    async with database.session() as session:
        made = [Product(name=name, price=Decimal("5.00")) for name in names]
        for product in made:
            await session.add(product)
        await session.commit()
        return [str(product.id) for product in made]


async def names_left(database: Database) -> set[str]:
    async with database.session() as session:
        return set((await session.scalars(select(Product.name))).all())


async def delete(
    client: httpx.AsyncClient, view: str, keys: list[str], query: str = "", **more: str
) -> httpx.Response:
    page = await client.get(f"/admin/{view}")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    return await client.post(
        f"/admin/{view}/action/delete_selected{query}",
        data={"_csrf": token.group(1), "keys": keys, **more},
        follow_redirects=True,
    )


class TestDeletingTheChosenRecords:
    async def test_each_goes_through_the_hooks_and_the_log(
        self, client: httpx.AsyncClient, database: Database, log: AuditLog
    ) -> None:
        keys = await add_products(database, "Spare A", "Spare B", "Spare C")

        page = await delete(client, "products", keys)

        entries = await log.find(AuditQuery(view="products"), limit=10)
        assert "3 products deleted." in page.text
        assert not {"Spare A", "Spare B", "Spare C"} & await names_left(database)
        assert sorted(entry.record_key for entry in entries) == sorted(keys)
        assert {entry.event for entry in entries} == {AuditEvent.DELETED}
        assert entries[0].changes["name"][1] is None

    async def test_every_match_can_go_at_once(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await add_products(database, "Spare A", "Spare B")

        page = await delete(client, "products", [], query="?q=Spare", everything="1")

        assert "2 products deleted." in page.text
        left = await names_left(database)
        assert not {"Spare A", "Spare B"} & left
        assert "Linen shirt" in left


class TestWhenOneIsRefused:
    async def test_a_hook_that_refuses_keeps_every_record(
        self, client: httpx.AsyncClient, database: Database, log: AuditLog
    ) -> None:
        keys = await add_products(database, "Spare A", "Keep me", "Spare B")

        page = await delete(client, "products", keys)

        assert (
            "Nothing was deleted, because Keep me cannot be: "
            "This one stays in the catalogue." in page.text
        )
        assert {"Spare A", "Keep me", "Spare B"} <= await names_left(database)
        assert await log.find(AuditQuery(), limit=10) == []

    async def test_a_record_the_person_may_not_delete_keeps_every_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        keys = await add_products(database, "Spare A", "Locked")

        page = await delete(client, "locked", keys)

        assert "Nothing was deleted, because Locked cannot be" in page.text
        assert {"Spare A", "Locked"} <= await names_left(database)

    async def test_a_record_others_refer_to_keeps_every_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        keys = await add_products(database, "Spare A")

        # Order lines still point at the first product.
        page = await delete(client, "products", ["1", *keys])

        assert (
            "Nothing was deleted, because other records still refer to Linen shirt."
            in page.text
        )
        assert {"Spare A", "Linen shirt"} <= await names_left(database)

    async def test_more_than_the_limit_is_refused(
        self,
        client: httpx.AsyncClient,
        database: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(model_view, "BULK_DELETE_LIMIT", 2)
        keys = await add_products(database, "Spare A", "Spare B", "Spare C")

        page = await delete(client, "products", keys)

        assert "Delete at most 2 at a time. Narrow the list first." in page.text
        assert {"Spare A", "Spare B", "Spare C"} <= await names_left(database)


class TestWhereItIsOffered:
    async def test_in_the_selection_bar_after_a_warning(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/products")

        assert "Delete the chosen products? This cannot be undone." in page.text
        assert re.search(r'class="btn btn-sm btn-error"[^>]*>\s*Delete\s*<', page.text)

    async def test_not_where_deleting_is_off(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        keys = await add_products(database, "Spare A")

        for view in ("read_only", "one_at_a_time"):
            answer = await delete(client, view, keys)

            assert answer.status_code == 404
        assert "Spare A" in await names_left(database)

    async def test_a_view_of_its_own_takes_the_name(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        keys = await add_products(database, "Spare A")

        page = await delete(client, "own_delete", keys)

        assert "1 archived." in page.text
        assert "Spare A" in await names_left(database)
