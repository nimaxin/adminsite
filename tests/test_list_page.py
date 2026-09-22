import html
import re

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at")
    ordering = ("-created_at",)
    page_size = 5


class CustomerView(ModelView, model=Customer):
    list_display = ("name", "email", "region", "is_active")
    search_fields = ("name", "email")
    list_filter = ("region", "is_active")


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    admin = Admin(database, title="Shop")
    admin.add_view(OrderView)
    admin.add_view(CustomerView)
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestSearch:
    async def test_the_search_box_narrows_the_list(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert "1 to 2 of 2" in response.text

    async def test_the_search_reaches_through_a_relationship(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=fischer.de")

        assert "1 to 2 of 2" in response.text

    async def test_the_term_stays_in_the_box(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert 'value="lena"' in response.text

    async def test_a_search_matching_nothing_says_so(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=nobody")

        assert "No orders to show." in response.text


class TestFilters:
    async def test_a_filter_narrows_the_list(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=SHIPPED")

        assert "1 to 2 of 2" in response.text

    async def test_two_values_match_either(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=SHIPPED&status=PAID")

        assert "1 to 4 of 4" in response.text

    async def test_the_filter_panel_shows_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")

        assert "Shipped" in response.text
        assert "Refunded" in response.text

    async def test_an_active_filter_shows_a_chip(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED")

        assert "Status: Shipped" in response.text

    async def test_the_chip_links_to_the_list_without_it(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED&q=a")

        removal = re.search(
            r'href="([^"]+)"\s+aria-label="Remove this filter"', response.text
        )
        assert removal is not None
        link = html.unescape(removal.group(1))
        assert "q=a" in link
        assert "status=" not in link

    async def test_a_range_filter_reads_from_the_url(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?total=100,")

        assert "Total: 100," in response.text

    async def test_search_and_filter_work_together(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena&status=SHIPPED")

        assert "1 to 1 of 1" in response.text

    async def test_an_unknown_parameter_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?colour=green")

        assert "1 to 5 of 7" in response.text


class TestSorting:
    async def test_a_column_can_be_sorted(self, client: httpx.AsyncClient) -> None:
        ascending = await client.get("/admin/orders?sort=total")
        descending = await client.get("/admin/orders?sort=-total")

        assert ascending.text != descending.text

    async def test_the_heading_shows_which_way_it_is_sorted(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?sort=total")

        assert "&uarr;" in response.text

    async def test_clicking_the_same_column_turns_it_around(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?sort=total")

        assert "sort=-total" in response.text

    async def test_sorting_keeps_the_search(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?q=lena&sort=total")

        assert "q=lena" in response.text
        assert "1 to 2 of 2" in response.text


class TestPaging:
    async def test_paging_keeps_the_filters(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=PAID&page=1")

        assert "status=PAID" in response.text

    async def test_the_second_page_carries_on(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?page=2")

        assert "6 to 7 of 7" in response.text


class TestPartialUpdates:
    async def test_htmx_gets_only_the_table_back(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/admin/orders?q=lena", headers={"HX-Request": "true"}
        )

        assert response.status_code == 200
        assert "<!doctype html>" not in response.text.lower()
        assert 'id="records"' in response.text

    async def test_a_normal_request_gets_the_whole_page(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert "<!doctype html>" in response.text.lower()


class TestExportLink:
    async def test_the_export_link_keeps_the_filters(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=PAID&q=lena")

        assert "/admin/orders/export?" in response.text
        assert "status=PAID" in response.text
