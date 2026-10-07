"""A list shows its rows at once, and their total follows in a request of its own."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, CountMode, ModelView, Permission, Statement
from tests.models import Order, OrderStatus
from tests.support import TOTAL_REQUEST, Backend, count_queries, counted


class Orders(ModelView[Order]):
    """Counts its records exactly, three to a page."""

    name = "orders"
    fields = ["id", "status", "total"]
    page_size = 3


class EstimatedOrders(Orders):
    """Says its table is too big to count exactly."""

    count_mode = CountMode.ESTIMATED


class UncountedOrders(Orders):
    """Says its table is too big to count at all."""

    count_mode = CountMode.NONE


class PaidOrders(Orders):
    """Holds only the paid orders."""

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status == OrderStatus.PAID)


class HiddenOrders(Orders):
    """Kept from this user altogether."""

    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        return False


@asynccontextmanager
async def open_admin(
    backend: Backend, view: type[ModelView[Any]]
) -> AsyncIterator[httpx.AsyncClient]:
    """A client of an admin holding this one view."""
    app = Starlette()
    app.mount("/admin", Admin(backend.database, views=[view]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def client(backend: Backend) -> AsyncIterator[httpx.AsyncClient]:
    async with open_admin(backend, Orders) as opened:
        yield opened


def totals(statements: list[str]) -> list[str]:
    """The statements that count records rather than read them."""
    return [item for item in statements if "count(" in item.lower()]


def counter(page: str) -> str | None:
    """The element that asks for the list's total, as the page draws it."""
    found = re.search(r'<div id="records-counter"[^>]*>', page)
    return found.group(0) if found else None


def next_is_offered(page: str) -> bool:
    """Whether the pager's Next button leads anywhere."""
    found = re.search(r'<a class="([^"]*)"\s+aria-label="Next"', page)
    assert found is not None
    return "btn-disabled" not in found.group(1)


class TestTheRows:
    @pytest.mark.parametrize("view", [Orders, EstimatedOrders, UncountedOrders])
    @pytest.mark.parametrize("htmx", [False, True], ids=["page", "table"])
    async def test_they_never_wait_for_a_count(
        self, backend: Backend, view: type[ModelView[Order]], htmx: bool
    ) -> None:
        async with open_admin(backend, view) as client:
            with count_queries(backend) as queries:
                response = await client.get(
                    "/admin/orders", headers={"HX-Request": "true"} if htmx else {}
                )

        assert response.status_code == 200
        assert totals(queries.statements) == []

    async def test_they_still_say_whether_more_follow(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await client.get("/admin/orders")
        last = await client.get("/admin/orders?page=3")

        assert next_is_offered(first.text)
        assert not next_is_offered(last.text)

    @pytest.mark.parametrize("view", [Orders, EstimatedOrders])
    async def test_the_table_asks_for_their_total(
        self, backend: Backend, view: type[ModelView[Order]]
    ) -> None:
        async with open_admin(backend, view) as client:
            page = await client.get("/admin/orders?page=2&sort=-total")

        asking = counter(page.text)
        assert asking is not None
        assert 'hx-get="/admin/orders?page=2&amp;sort=-total"' in asking
        assert 'hx-trigger="load"' in asking
        assert "1 to 3" not in page.text
        assert "4 to 6" in page.text
        assert "of 7" not in page.text

    async def test_a_view_that_never_counts_asks_for_nothing(
        self, backend: Backend
    ) -> None:
        async with open_admin(backend, UncountedOrders) as client:
            page = await client.get("/admin/orders")

        assert counter(page.text) is None


class TestTheTotal:
    async def test_it_comes_with_every_place_that_shows_it(
        self, backend: Backend, client: httpx.AsyncClient
    ) -> None:
        with count_queries(backend) as queries:
            total = await counted(client, "/admin/orders?page=2")

        assert len(totals(queries.statements)) == 1
        for part in ("records-total", "records-count", "page-numbers"):
            found = re.search(rf'<[a-z]+ id="{part}"[^>]*>', total)
            assert found is not None, part
            assert 'hx-swap-oob="true"' in found.group(0)
        assert "7 orders" in total
        assert "4 to 6 of 7" in total
        assert 'aria-label="Page 3"' in total

    async def test_it_leaves_the_rows_as_they_are(
        self, client: httpx.AsyncClient
    ) -> None:
        total = await counted(client, "/admin/orders")

        assert 'id="records"' not in total
        assert "Order #" not in total

    async def test_it_counts_only_what_the_scope_holds(self, backend: Backend) -> None:
        async with open_admin(backend, PaidOrders) as client:
            total = await counted(client, "/admin/orders")

        assert "2 orders" in total

    async def test_it_is_refused_to_whoever_cannot_view_the_list(
        self, backend: Backend
    ) -> None:
        async with open_admin(backend, HiddenOrders) as client:
            refused = await client.get("/admin/orders", headers=TOTAL_REQUEST)

        assert refused.status_code == 403
