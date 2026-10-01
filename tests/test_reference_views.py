"""The large reference view runs: its pages, its save hook and its actions.

Each test runs on async and sync SQLite.
"""

import re
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from adminsite import Admin, ModelView, RefusedError
from adminsite.backends.sqlalchemy import Database
from tests.reference.models import Product, ProductStatus, Supplier
from tests.reference.products import ProductView, SupplierView


async def become_manager(admin: Admin, request: Request) -> Response:
    """Stands in for a sign in that makes someone a manager."""
    request.session["manager"] = True
    return PlainTextResponse("You manage the catalogue now.")


@pytest.fixture
async def catalogue(reference_database: Database) -> Database:
    """The reference shop with a supplier and two of its products."""
    async with reference_database.session() as session:
        mill = Supplier(name="Linen Mill", email="hello@mill.example", country="NL")
        for product in [
            Product(
                sku="SHIRT-1",
                name="Linen shirt",
                supplier=mill,
                status=ProductStatus.LIVE,
                price=Decimal("40.00"),
                cost=Decimal("20.00"),
                stock=3,
            ),
            Product(
                sku="SCARF-1",
                name="Wool scarf",
                supplier=mill,
                status=ProductStatus.LIVE,
                price=Decimal("25.00"),
                cost=Decimal("24.00"),
                stock=20,
            ),
        ]:
            await session.add(product)
        await session.commit()
    return reference_database


@pytest.fixture
def admin(catalogue: Database) -> Admin:
    admin = Admin(
        catalogue, views=[ProductView, SupplierView], secret_key="for-the-session"
    )
    admin.add_route("/-/manager", become_manager)
    return admin


@pytest.fixture
async def client(admin: Admin) -> AsyncIterator[httpx.AsyncClient]:
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def products(admin: Admin) -> ModelView[Any]:
    view = admin.views.find("products")
    assert view is not None
    return view


async def run(
    client: httpx.AsyncClient,
    name: str,
    data: dict[str, Any],
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    page = await client.get("/admin/products")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    return await client.post(
        f"/admin/products/action/{name}",
        data={"_csrf": token.group(1), **data},
        files=files,
        follow_redirects=True,
    )


async def stored(database: Database, sku: str) -> Product:
    async with database.session() as session:
        found = select(Product).where(Product.sku == sku)
        product: Product | None = await session.scalar(found)
        assert product is not None
        return product


class TestThePages:
    @pytest.mark.parametrize(
        "path",
        ["/admin/products", "/admin/products/1", "/admin/products/1/edit"],
    )
    async def test_each_opens(self, client: httpx.AsyncClient, path: str) -> None:
        page = await client.get(path)

        assert page.status_code == 200
        assert "Linen shirt" in page.text

    async def test_the_list_works_out_its_computed_fields(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/products")

        assert "Running low" in page.text
        assert "50%" in page.text


class TestTheSaveHook:
    async def test_it_writes_the_sku_in_capitals(self, admin: Admin) -> None:
        async with admin.database.session() as session:
            record = await products(admin)._saver.save(
                session,
                {
                    "sku": " shirt-2 ",
                    "name": "Linen shirt, blue",
                    "supplier": "1",
                    "price": Decimal("40.00"),
                    "cost": Decimal("20.00"),
                },
            )

        assert record.sku == "SHIRT-2"

    async def test_a_price_below_the_cost_is_refused_beside_the_price(
        self, admin: Admin
    ) -> None:
        async with admin.database.session() as session:
            with pytest.raises(RefusedError) as refused:
                await products(admin)._saver.save(
                    session,
                    {
                        "sku": "SHIRT-3",
                        "name": "Linen shirt, cheap",
                        "supplier": "1",
                        "price": Decimal("10.00"),
                        "cost": Decimal("20.00"),
                    },
                )

        assert refused.value.field == "price"


class TestTheActions:
    async def test_changing_prices_asks_for_a_manager(
        self, client: httpx.AsyncClient, catalogue: Database
    ) -> None:
        answer = await run(
            client, "change_prices", {"keys": ["1"], "change.percent": "10"}
        )

        assert answer.status_code == 403
        assert (await stored(catalogue, "SHIRT-1")).price == Decimal("40.00")

    async def test_a_manager_changes_prices_rounded_and_never_below_cost(
        self, client: httpx.AsyncClient, catalogue: Database
    ) -> None:
        await client.get("/admin/-/manager")

        answer = await run(
            client,
            "change_prices",
            {
                "keys": ["1", "2"],
                "change.percent": "-10",
                "change.round_to": "0.50",
                "change.never_below_cost": "on",
            },
        )

        assert "2 prices changed." in answer.text
        assert (await stored(catalogue, "SHIRT-1")).price == Decimal("36.00")
        assert (await stored(catalogue, "SCARF-1")).price == Decimal("24.00")

    async def test_retiring_marks_the_chosen_products(
        self, client: httpx.AsyncClient, catalogue: Database
    ) -> None:
        answer = await run(client, "retire", {"keys": ["2"]})

        assert "1 products retired." in answer.text
        assert (await stored(catalogue, "SCARF-1")).status is ProductStatus.RETIRED

    async def test_ordering_more_asks_for_a_supplier_and_a_quantity(
        self, client: httpx.AsyncClient, catalogue: Database
    ) -> None:
        answer = await run(
            client, "order_more", {"keys": "1", "supplier": "1", "quantity": ""}
        )

        assert "Ordered 10 of Linen shirt from Linen Mill." in answer.text
        assert (await stored(catalogue, "SHIRT-1")).stock == 13

    async def test_counting_runs_its_own_query(self, client: httpx.AsyncClient) -> None:
        answer = await run(client, "count_running_low", {})

        assert "1 products are running low." in answer.text

    async def test_a_price_list_is_read_from_the_file(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.get("/admin/-/manager")

        answer = await run(
            client,
            "import_prices",
            {"read_as.has_header": "on", "read_as.delimiter": ","},
            files={
                "prices": (
                    "prices.csv",
                    b"sku,price\nSHIRT-1,45.00\nSCARF-1,30.00\n",
                    "text/csv",
                )
            },
        )

        assert "2 prices read from prices.csv." in answer.text
        assert "See the products" in answer.text
