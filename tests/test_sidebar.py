from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"


class CustomerView(ModelView, model=Customer):
    """Customers, only ever opened from their orders."""

    display_template = "{name}"
    search_fields = ("name",)
    in_sidebar = False


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, title="Shop", views=[OrderView, CustomerView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestAViewLeftOutOfTheSidebar:
    async def test_it_is_not_listed_in_the_sidebar(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert 'href="/admin/orders"' in page.text
        assert 'href="/admin/customers"' not in page.text

    async def test_nor_among_the_palette_pages_or_the_overview_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        palette = await client.get("/admin/-/search")
        overview = await client.get("/admin/")

        assert "Orders" in palette.text
        assert "Customers" not in palette.text
        assert "New customer" not in palette.text
        assert "Customers" not in overview.text

    async def test_its_records_are_still_found_by_the_palette(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search?q=lena")

        assert "Lena Fischer" in found.text

    async def test_its_pages_still_open(self, client: httpx.AsyncClient) -> None:
        listing = await client.get("/admin/customers")
        record = await client.get("/admin/customers/1")

        assert listing.status_code == 200
        assert record.status_code == 200

    async def test_a_related_record_still_links_to_it(
        self, client: httpx.AsyncClient
    ) -> None:
        order = await client.get("/admin/orders/1")

        assert 'href="/admin/customers/' in order.text
