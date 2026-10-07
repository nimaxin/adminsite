"""A list shows its rows at once, and their total follows in a request of its own."""

import json
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


class FilteredOrders(Orders):
    """Has a search and a filter, either of which changes the total."""

    list_filters = [Order.status]
    searchable_fields = ["customer.name"]


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


def kept_from(total: str) -> str:
    """What the table sends back of the total this answer shows."""
    found = re.search(r"data-kept='([^']*)'", total)
    assert found is not None
    return found.group(1)


def table_request(kept: str) -> dict[str, str]:
    """The headers of a table HTMX redraws, sending back the total it shows."""
    return {"HX-Request": "true", "Adminsite-Total": kept}


class TestAKeptTotal:
    @pytest.mark.parametrize(
        "address",
        [
            "/admin/orders?status=PAID&status=SHIPPED&page=2",
            "/admin/orders?status=PAID&status=SHIPPED&sort=-total",
            "/admin/orders?status=SHIPPED&status=PAID",
        ],
    )
    async def test_a_new_page_or_sort_shows_it_without_counting(
        self, backend: Backend, address: str
    ) -> None:
        async with open_admin(backend, FilteredOrders) as client:
            total = await counted(client, "/admin/orders?status=PAID&status=SHIPPED")
            with count_queries(backend) as queries:
                table = await client.get(
                    address, headers=table_request(kept_from(total))
                )

        assert totals(queries.statements) == []
        assert counter(table.text) is None
        assert "of 4" in table.text
        assert "4 orders" in table.text
        assert kept_from(table.text) == kept_from(total)

    @pytest.mark.parametrize(
        "address", ["/admin/orders?status=PAID", "/admin/orders?q=lena"]
    )
    async def test_a_new_search_or_filter_counts_again(
        self, backend: Backend, address: str
    ) -> None:
        async with open_admin(backend, FilteredOrders) as client:
            total = await counted(client, "/admin/orders")
            table = await client.get(address, headers=table_request(kept_from(total)))

        assert counter(table.text) is not None
        assert "of 7" not in table.text

    @pytest.mark.parametrize(
        "change",
        [
            {"of": "another list"},
            {"total": -1},
            {"total": "7"},
            {"total": True},
            {"estimated": "no"},
        ],
    )
    async def test_a_total_that_does_not_fit_is_ignored(
        self, client: httpx.AsyncClient, change: dict[str, Any]
    ) -> None:
        kept = json.loads(kept_from(await counted(client, "/admin/orders")))
        sent = json.dumps({**kept, **change})
        table = await client.get("/admin/orders?page=2", headers=table_request(sent))

        assert counter(table.text) is not None
        assert "of 7" not in table.text

    async def test_a_header_that_is_not_one_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        table = await client.get(
            "/admin/orders?page=2", headers=table_request("seven, honestly")
        )

        assert counter(table.text) is not None
