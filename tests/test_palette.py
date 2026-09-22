from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product


class CustomerView(ModelView, model=Customer):
    display_template = "{name} ({email})"
    search_fields = ("name", "email")

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region != "SE")


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"
    search_fields = ("customer.name",)
    can_create = False


class ProductView(ModelView, model=Product):
    search_fields = ("name",)
    global_search = False


class SecretView(ModelView, model=Product):
    name = "secrets"
    search_fields = ("name",)

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return False


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[CustomerView, OrderView, ProductView, SecretView],
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestPages:
    async def test_with_nothing_typed_every_page_is_offered(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search")

        assert 'href="/admin/customers"' in found.text
        assert 'href="/admin/customers/new"' in found.text
        assert 'href="/admin/orders"' in found.text

    async def test_pages_are_narrowed_by_name(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=cust")

        assert 'href="/admin/customers"' in found.text
        assert 'href="/admin/orders"' not in found.text

    async def test_pages_you_may_not_use_are_left_out(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search")

        assert 'href="/admin/orders/new"' not in found.text
        assert 'href="/admin/secrets"' not in found.text


class TestRecords:
    async def test_records_are_found_by_each_views_search(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search?q=lena")

        assert "Lena Fischer (lena@fischer.de)" in found.text
        assert 'href="/admin/customers/1"' in found.text
        assert "Order #1" in found.text

    async def test_the_scope_still_applies(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=jonas")

        assert "Jonas Berg" not in found.text

    async def test_a_view_can_stay_out_of_the_palette(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search?q=linen")

        assert "Linen shirt" not in found.text

    async def test_one_letter_does_not_search(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=l")

        assert "Lena Fischer" not in found.text

    async def test_no_match_says_so(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=zzzz")

        assert "Nothing matches." in found.text


class TestTheLayout:
    async def test_every_page_carries_the_palette(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/")

        assert 'id="palette"' in page.text
        assert 'hx-get="/admin/-/search"' in page.text
        assert "Ctrl K" in page.text
