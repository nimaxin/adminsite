import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Computed, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.fields import Field
from tests.models import Customer, Order, OrderStatus
from tests.support import Backend, count_queries


class Money(Field):
    """An amount that reads with the region of the customer beside it."""

    widget = "number"

    def text_for(self, record: Any, value: Any) -> str:
        if value is None:
            return ""
        return f"{value} ({record.customer.region})"


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total", "lines")
    detail_fields = ("id", "status", "total", "lines", "biggest")
    form_fields = ("status", "note")
    fields = (
        Money("total", label="Total"),
        Computed(
            "lines",
            lambda order: len(order.items),
            label="Lines",
            needs=("items", "customer"),
        ),
        Computed(
            "biggest",
            lambda order: max((item.quantity for item in order.items), default=0),
            label="Biggest line",
            needs=("items",),
        ),
    )


class StatusView(ModelView, model=Order):
    name = "statuses"
    list_display = ("id", "state")
    fields = (
        Computed(
            "state",
            lambda order: (
                "waiting" if order.status is OrderStatus.PENDING else order.status.value
            ),
            label="State",
        ),
    )


class CustomerView(ModelView, model=Customer):
    pass


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[OrderView, StatusView, CustomerView], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestAFieldThatReadsTheRecord:
    def test_the_text_comes_from_both_columns(self) -> None:
        view = OrderView()

        assert view.field_for("total").text_for(FakeOrder(), 12) == "12 (DE)"

    async def test_the_list_and_the_page_use_it(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")
        page = await client.get("/admin/orders/1")

        assert "107.00 (DE)" in listed.text
        assert "107.00 (DE)" in page.text

    async def test_the_export_uses_it(self, client: httpx.AsyncClient) -> None:
        exported = await client.get("/admin/orders/export")

        assert "107.00 (DE)" in exported.text


class FakeOrder:
    customer = type("Customer", (), {"region": "DE"})()


class TestComputed:
    async def test_it_shows_in_the_list_and_on_the_page(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")
        page = await client.get("/admin/orders/1")

        assert re.search(r">\s*Lines\s*</th>", listed.text)
        assert "Biggest line" in page.text

    async def test_it_is_not_sortable(self, client: httpx.AsyncClient) -> None:
        listed = await client.get("/admin/orders")

        assert "sort=status" in listed.text
        assert "sort=lines" not in listed.text

    async def test_a_list_costs_no_query_per_row(self, backend: Backend) -> None:
        admin = Admin(backend.database, views=[OrderView])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            with count_queries(backend) as queries:
                page = await client.get("/admin/orders")

        assert page.status_code == 200
        # The page, the count, and one query for the lines of every row.
        assert queries.count <= 4

    async def test_it_is_never_written(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/orders/1/edit")
        answer = await client.post(
            "/admin/-/api/orders/1", json={"lines": 3}, headers={"X-CSRF-Token": "x"}
        )

        assert 'name="lines"' not in form.text
        assert answer.status_code in (405, 422)

    async def test_the_api_reads_it(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/admin/-/api/orders/1")).json()

        assert body["lines"] == "2"

    async def test_a_value_that_needs_nothing_loaded(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/statuses")

        assert "waiting" in listed.text
