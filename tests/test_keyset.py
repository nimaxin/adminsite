import html
import re
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, CountMode, ModelView, Pagination
from adminsite.backends.sqlalchemy import Database
from adminsite.backends.sqlalchemy import repository as repository_module
from adminsite.backends.sqlalchemy.cursor import decode_cursor, encode_cursor
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.query import Page, QuerySpec, Sort
from tests.models import Order
from tests.support import Backend, count_queries

NEWEST_FIRST = (Sort("created_at", descending=True),)


@pytest.fixture
def orders() -> SQLAlchemyRepository:
    return SQLAlchemyRepository(Order)


def keyset(**changes: Any) -> QuerySpec:
    return QuerySpec(limit=3, sort=NEWEST_FIRST, keyset=True).replace(**changes)


def ids(page: Page) -> list[int]:
    return [row.id for row in page]


class TestCursor:
    def test_values_come_back_as_their_types(self) -> None:
        from datetime import datetime
        from decimal import Decimal

        values = [datetime(2026, 9, 1, 10, 30), Decimal("59.00"), 3, True, None]
        token = encode_cursor(values)

        assert decode_cursor(token, [datetime, Decimal, int, bool, int]) == values

    def test_a_token_edited_by_hand_reads_as_nothing(self) -> None:
        assert decode_cursor("not-a-cursor", [int]) is None
        assert decode_cursor(encode_cursor(["abc"]), [int]) is None
        assert decode_cursor(encode_cursor([1, 2]), [int]) is None


class TestKeysetPages:
    async def test_walking_forward_visits_every_row_once(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        seen: list[int] = []
        async with database.session() as session:
            page = await orders.list(session, keyset())
            seen += ids(page)
            while page.has_next:
                page = await orders.list(session, keyset(after=page.next_cursor))
                seen += ids(page)
            everything = await orders.list(
                session, QuerySpec(limit=None, sort=NEWEST_FIRST)
            )

        assert seen == ids(everything)
        assert page.keyset is True

    async def test_ties_are_broken_by_the_key(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        seen: list[int] = []
        spec = keyset(sort=(Sort("customer_id"),), limit=2)
        async with database.session() as session:
            page = await orders.list(session, spec)
            seen += ids(page)
            while page.has_next:
                page = await orders.list(session, spec.replace(after=page.next_cursor))
                seen += ids(page)

        assert sorted(seen) == [1, 2, 3, 4, 5, 6, 7]

    async def test_going_back_shows_the_page_before(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            first = await orders.list(session, keyset())
            second = await orders.list(session, keyset(after=first.next_cursor))
            third = await orders.list(session, keyset(after=second.next_cursor))
            back = await orders.list(session, keyset(before=third.previous_cursor))

        assert ids(back) == ids(second)
        assert back.has_next is True
        assert back.has_previous is True

    async def test_going_back_to_the_start_shows_a_full_first_page(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            first = await orders.list(session, keyset())
            second = await orders.list(session, keyset(after=first.next_cursor))
            back = await orders.list(session, keyset(before=second.previous_cursor))

        assert ids(back) == ids(first)
        assert back.has_previous is False
        assert first.has_previous is False

    async def test_a_decimal_sort_pages_too(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        spec = keyset(sort=(Sort("total"),))
        async with database.session() as session:
            first = await orders.list(session, spec)
            second = await orders.list(session, spec.replace(after=first.next_cursor))

        assert max(row.total for row in first) <= min(row.total for row in second)

    async def test_the_search_still_applies(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        spec = keyset(search="lena", search_paths=("customer.name",), limit=1)
        async with database.session() as session:
            first = await orders.list(session, spec)
            second = await orders.list(session, spec.replace(after=first.next_cursor))

        assert first.total == 2
        assert len(first) == len(second) == 1
        assert second.has_next is False

    async def test_a_bad_cursor_starts_from_the_top(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            first = await orders.list(session, keyset())
            tampered = await orders.list(session, keyset(after="garbage"))

        assert ids(tampered) == ids(first)

    async def test_without_a_count_one_query_reads_a_page(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            first = await orders.list(session, keyset(count=CountMode.NONE))
            with count_queries(backend) as queries:
                await orders.list(
                    session, keyset(count=CountMode.NONE, after=first.next_cursor)
                )

        assert queries.count == 1

    @pytest.mark.parametrize("path", ["note", "customer.name", "status"])
    async def test_a_sort_it_cannot_use_falls_back_to_page_numbers(
        self, database: Database, orders: SQLAlchemyRepository, path: str
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, keyset(sort=(Sort(path),)))

        assert page.keyset is False
        assert page.total == 7


class TestEstimatedCounts:
    async def test_small_or_unknown_tables_are_counted_exactly(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(limit=3, count=CountMode.ESTIMATED)
            )

        assert page.total == 7
        assert page.estimated is False
        assert page.has_next is True

    async def test_a_big_table_shows_the_database_estimate(
        self,
        database: Database,
        orders: SQLAlchemyRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def guess(session: object) -> int:
            return 2_500_000

        monkeypatch.setattr(orders, "estimate", guess)
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(limit=3, count=CountMode.ESTIMATED)
            )

        assert page.total == 2_500_000
        assert page.estimated is True
        assert page.has_next is True

    async def test_a_narrowed_count_stops_at_the_limit(
        self,
        database: Database,
        orders: SQLAlchemyRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(repository_module, "EXACT_COUNT_LIMIT", 4)
        spec = QuerySpec(
            limit=3,
            count=CountMode.ESTIMATED,
            search="a",
            search_paths=("customer.name",),
        )
        async with database.session() as session:
            page = await orders.list(session, spec)

        assert page.total == 4
        assert page.at_least is True

    async def test_a_scope_that_narrows_is_never_estimated(
        self,
        database: Database,
        orders: SQLAlchemyRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def guess(session: object) -> int:
            return 2_500_000

        def scope(statement: Select[Any]) -> Select[Any]:
            return statement.where(Order.customer_id == 1)

        monkeypatch.setattr(orders, "estimate", guess)
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(limit=3, count=CountMode.ESTIMATED), scope
            )

        assert page.total == 2
        assert page.estimated is False

    async def test_the_estimate_can_be_read_on_every_database(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            found = await orders.estimate(session)

        if backend.engine.dialect.name == "sqlite":
            assert found is None
        else:
            assert found is None or found >= 0


class NewestOrders(ModelView, model=Order):
    name = "orders"
    list_display = ("id", "status", "total")
    ordering = ("-created_at",)
    page_size = 3
    pagination = Pagination.KEYSET


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    admin = Admin(database, title="Shop", views=[NewestOrders])
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def link(text: str, label: str) -> str:
    found = re.search(rf'aria-label="{label}"[^>]*?href="([^"]*)"', text, re.S)
    assert found is not None
    return html.unescape(found.group(1))


class TestKeysetListPage:
    async def test_the_pager_moves_by_cursor(self, client: httpx.AsyncClient) -> None:
        first = await client.get("/admin/orders")
        next_url = link(first.text, "Next")
        second = await client.get(next_url)
        back = await client.get(link(second.text, "Previous"))

        assert "after=" in next_url
        assert "Page 1" not in first.text
        assert "7 orders" in first.text
        assert second.status_code == 200
        assert back.text.count("/admin/orders/") == first.text.count("/admin/orders/")

    async def test_sorting_starts_again_from_the_top(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await client.get("/admin/orders")
        second = await client.get(link(first.text, "Next"))

        assert 'sort=total"' in second.text or "sort=total&" in second.text
        assert (
            "after=" not in re.findall(r'href="([^"]*sort=total[^"]*)"', second.text)[0]
        )
