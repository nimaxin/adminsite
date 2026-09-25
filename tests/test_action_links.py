import re
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.fields import RelationField
from tests.models import Order, Product

seen: dict[str, Any] = {}


class ProductView(ModelView, model=Product):
    search_fields = ("name",)

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        # The scarf is off the shelves, so nobody may pick it.
        return statement.where(Product.name != "Wool scarf")


class OrderView(ModelView, model=Order):
    list_display = ("id", "note")
    ordering = ("id",)

    @action(
        "Assign to product",
        inputs=[RelationField("product", target=Product, required=True)],
    )
    async def assign(self, selection: Selection, product: Product) -> str:
        seen["product"] = product
        changed = await selection.update(note=f"For {product.name}")
        return f"{changed} orders assigned."

    @action(
        "Feature",
        on="record",
        inputs=[RelationField("product", target=Product, required=True)],
    )
    async def feature(
        self, record: Order, session: SessionAdapter, product: Product
    ) -> str:
        record.note = f"Featuring {product.name}"
        return "Featured."

    @action(
        "Bundle",
        inputs=[RelationField("products", target=Product, collection=True)],
    )
    async def bundle(self, selection: Selection, products: list[Product]) -> str:
        seen["products"] = products
        return "Bundled."


class ReadOnlyOrderView(ModelView, model=Order):
    """Everyone may look, nobody may change, so no action may run."""

    name = "archived_orders"

    @action("Assign", inputs=[RelationField("product", target=Product)])
    async def assign(self, selection: Selection, product: Product) -> str:
        return "Assigned."

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return action == Permission.VIEW


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, ProductView, ReadOnlyOrderView],
        audit=log,
        secret_key="for-the-session",
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def dialog(page: httpx.Response, name: str) -> str:
    found = re.search(rf'<dialog id="action-{name}".*?</dialog>', page.text, re.S)
    assert found is not None, name
    return found.group(0)


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def run(
    client: httpx.AsyncClient, name: str, data: dict[str, Any]
) -> httpx.Response:
    page = await client.get("/admin/orders")
    return await client.post(
        f"/admin/orders/action/{name}",
        data={"_csrf": token_in(page), **data},
        follow_redirects=True,
    )


async def note_of(database: Database, key: int = 1) -> str | None:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None
        return order.note


async def add_products(database: Database, count: int) -> None:
    async with database.session() as session:
        for number in range(count):
            await session.add(Product(name=f"Sock {number}", price=Decimal(5)))
        await session.commit()


class TestTheDialog:
    async def test_it_offers_the_products_their_view_lets_you_see(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        shown = dialog(page, "assign")
        assert re.search(r'<option value="1"\s*>\s*Linen shirt', shown)
        assert re.search(r'<option value="2"\s*>\s*Canvas tote', shown)
        assert "Wool scarf" not in shown

    async def test_a_product_added_since_is_on_offer(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.get("/admin/orders")
        async with database.session() as session:
            await session.add(Product(name="Linen cap", price=Decimal(19)))
            await session.commit()

        page = await client.get("/admin/orders")

        assert "Linen cap" in dialog(page, "assign")

    async def test_two_dialogs_asking_for_a_product_keep_apart(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert 'id="field-assign-product"' in page.text
        assert 'id="field-feature-product"' in page.text
        assert 'id="field-product"' not in page.text

    async def test_the_record_page_asks_for_it_too(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1")

        shown = dialog(page, "feature")
        assert re.search(r'<option value="2"\s*>\s*Canvas tote', shown)

    async def test_above_a_hundred_it_searches_instead(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await add_products(database, 101)

        page = await client.get("/admin/orders")
        found = await client.get(
            "/admin/orders/action/assign/lookup/product", params={"q": "Linen"}
        )
        hidden = await client.get(
            "/admin/orders/action/assign/lookup/product", params={"q": "scarf"}
        )

        assert 'hx-get="/admin/orders/action/assign/lookup/product"' in dialog(
            page, "assign"
        )
        assert "Linen shirt" in found.text
        assert "Wool scarf" not in hidden.text


class TestTheLookup:
    async def test_it_needs_what_running_the_action_needs(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/archived_orders/action/assign/lookup/product")

        assert answer.status_code == 403

    async def test_only_a_link_can_be_looked_up(
        self, client: httpx.AsyncClient
    ) -> None:
        missing = await client.get("/admin/orders/action/assign/lookup/colour")
        no_action = await client.get("/admin/orders/action/paint/lookup/product")

        assert missing.status_code == 404
        assert no_action.status_code == 404


class TestRunningIt:
    async def test_the_method_gets_the_product_itself(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await run(client, "assign", {"keys": "1", "product": "2"})

        assert isinstance(seen["product"], Product)
        assert seen["product"].name == "Canvas tote"
        assert await note_of(database) == "For Canvas tote"

    async def test_a_product_its_view_hides_is_refused(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "assign", {"keys": "1", "product": "3"})

        assert "Product: choose from the records offered." in answer.text
        assert await note_of(database) is None

    async def test_it_can_be_required(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "assign", {"keys": "1", "product": ""})

        assert "Assign to product was not done." in answer.text
        assert await note_of(database) is None

    async def test_a_record_action_gets_it_too(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await run(client, "feature", {"keys": "1", "product": "1"})

        assert await note_of(database) == "Featuring Linen shirt"

    async def test_a_link_to_many_gets_each_product(
        self, client: httpx.AsyncClient
    ) -> None:
        await run(client, "bundle", {"keys": "1", "products": ["1", "2"]})

        assert [product.name for product in seen["products"]] == [
            "Linen shirt",
            "Canvas tote",
        ]


class TestTheLog:
    async def test_it_names_the_product(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "assign", {"keys": "1", "product": "2"})

        entry = (await log.find(AuditQuery(events=[AuditEvent.ACTION]), limit=1))[0]

        assert entry.inputs == {"product": "Canvas tote"}


class TestTheApi:
    async def test_it_takes_the_key_and_answers_like_the_page(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/orders")
        headers = {"X-CSRF-Token": token_in(page)}

        done = await client.post(
            "/admin/-/api/orders/actions/assign",
            json={"keys": ["1"], "inputs": {"product": "2"}},
            headers=headers,
        )
        hidden = await client.post(
            "/admin/-/api/orders/actions/assign",
            json={"keys": ["1"], "inputs": {"product": "3"}},
            headers=headers,
        )
        several = await client.post(
            "/admin/-/api/orders/actions/bundle",
            json={"keys": ["1"], "inputs": {"products": ["1", "2"]}},
            headers=headers,
        )

        assert done.json() == {"message": "1 orders assigned."}
        assert await note_of(database) == "For Canvas tote"
        assert hidden.status_code == 422
        assert hidden.json()["errors"] == {
            "product": "Product: choose from the records offered."
        }
        assert several.status_code == 200
        assert len(seen["products"]) == 2
