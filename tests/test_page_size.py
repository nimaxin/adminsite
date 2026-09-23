import re
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    list_filter = ("status",)
    search_fields = ("customer.name",)
    ordering = ("id",)
    page_size = 3
    page_sizes = (3, 5, 100)


class CustomerView(ModelView, model=Customer):
    """A view with no sizes on offer keeps the one it was given."""

    page_size = 2


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database, views=[OrderView, CustomerView], secret_key="for-the-session"
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def rows(page: httpx.Response) -> int:
    body = page.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return body.count("<tr")


class TestChoosing:
    def test_the_views_own_size_is_among_them(self) -> None:
        assert OrderView().get_page_sizes() == (3, 5, 100)
        assert CustomerView().get_page_sizes() == ()

    def test_only_a_size_on_offer_counts(self) -> None:
        view = OrderView()

        assert view.pick_page_size(5) == 5
        assert view.pick_page_size(1000) == 3
        assert view.pick_page_size(None) == 3


class TestOnThePage:
    async def test_the_list_follows_the_size(self, client: httpx.AsyncClient) -> None:
        small = await client.get("/admin/orders")
        larger = await client.get("/admin/orders?size=5")

        assert rows(small) == 3
        assert rows(larger) == 5

    async def test_the_menu_shows_the_sizes(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders?size=5")

        assert 'aria-label="Rows per page"' in page.text
        assert re.search(r'<option value="5"\s+selected>', page.text)
        assert "100 rows" in page.text

    async def test_a_size_that_is_not_offered_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?size=1000")

        assert rows(page) == 3

    async def test_it_is_remembered(self, client: httpx.AsyncClient) -> None:
        await client.get("/admin/orders?size=5")
        again = await client.get("/admin/orders")

        assert rows(again) == 5

    async def test_it_stays_while_paging_and_filtering(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.get("/admin/orders?size=5")
        second = await client.get("/admin/orders?page=2")
        filtered = await client.get("/admin/orders?status=PAID")

        assert "1 to 5 of 7" not in second.text
        assert "6 to 7 of 7" in second.text
        assert rows(filtered) == 2

    async def test_paging_follows_the_size(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders?size=5")

        assert "1 to 5 of 7" in page.text

    async def test_a_view_without_sizes_shows_no_menu(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers")

        assert 'aria-label="Rows per page"' not in page.text
        assert rows(page) == 2

    async def test_the_export_is_unaffected(self, client: httpx.AsyncClient) -> None:
        exported = await client.get("/admin/orders?size=3")

        assert exported.status_code == 200
