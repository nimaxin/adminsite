import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.fields import RelationField
from adminsite.http.picker import RESULT_LIMIT
from tests.models import Customer, Order, OrderItem, OrderStatus


class CustomerView(ModelView, model=Customer):
    """A customer holds many orders, picked from a table that grew."""

    form_fields = ("name", "email", "orders")
    fields = (
        RelationField(
            "orders",
            target=Order,
            collection=True,
            display_template="Order #{id}",
        ),
    )


class OrderItemView(ModelView, model=OrderItem):
    """A line holds one order, picked from the same large table."""

    form_fields = ("order", "quantity")
    fields = (RelationField("order", target=Order, display_template="Order #{id}"),)


@pytest.fixture
async def big_database(database: Database) -> Database:
    """More orders than the picker lists, so it searches instead."""
    async with database.session() as session:
        for number in range(PLENTY):
            await session.add(
                Order(
                    customer_id=4,
                    status=OrderStatus.PENDING,
                    total=Decimal("1.00"),
                    created_at=datetime(2026, 9, 20, 9, 0),
                    note="rush" if number == 0 else None,
                )
            )
        await session.commit()
    return database


PLENTY = 120


@pytest.fixture
async def client(big_database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        big_database,
        views=[CustomerView, OrderItemView],
        secret_key="for-the-session",
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def picked_in(page: httpx.Response) -> list[dict[str, str]]:
    """The records the picker starts with, read back out of the page."""
    found = re.search(r"picked: (\[.*?\])\}\)'", page.text)
    assert found is not None
    picked: list[dict[str, str]] = json.loads(found.group(1))
    return picked


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


class TestALinkThatSearches:
    async def test_a_large_table_is_searched(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/customers/1/edit")

        assert "recordPicker(" in form.text
        assert "/admin/customers/lookup/orders" in form.text
        assert "<select" not in form.text.split('name="orders"')[0][-400:]

    async def test_it_holds_every_record_already_linked(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        assert picked_in(form) == [
            {"value": "1", "label": "Order #1"},
            {"value": "2", "label": "Order #2"},
        ]

    async def test_a_link_holding_many_says_so(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/customers/1/edit")

        assert "multiple: true" in form.text

    async def test_a_link_holding_one_says_so(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/order_items/1/edit")

        assert "multiple: false" in form.text
        assert picked_in(form) == [{"value": "1", "label": "Order #1"}]

    async def test_a_new_record_starts_with_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/new")

        assert picked_in(form) == []

    async def test_a_result_hands_the_record_to_the_picker(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/customers/lookup/orders")

        assert 'choose("1", "Order #1")' in found.text

    async def test_a_column_the_names_do_not_show_is_not_searched(
        self, client: httpx.AsyncClient
    ) -> None:
        """A note nobody can see cannot be read a letter at a time."""
        found = await client.get("/admin/customers/lookup/orders?q=rush")

        assert found.text.count("choose(") == RESULT_LIMIT

    async def test_what_the_target_view_offers_is_searched(
        self, big_database: Database
    ) -> None:
        class OrderView(ModelView, model=Order):
            display_template = "Order #{id}"
            search_fields = ("note",)

        admin = Admin(big_database, views=[CustomerView, OrderView])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            found = await client.get("/admin/customers/lookup/orders?q=rush")

        assert found.text.count("choose(") == 1


class TestSaving:
    async def test_every_record_picked_is_kept(
        self, client: httpx.AsyncClient, big_database: Database
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        answer = await client.post(
            "/admin/customers/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "Lena Fischer",
                "email": "lena@fischer.de",
                "orders": ["1", "2", "3"],
            },
        )

        assert answer.status_code == 303
        async with big_database.session() as session:
            kept = await session.scalars(
                select(Order.id).where(Order.customer_id == 1).order_by(Order.id)
            )
            assert list(kept) == [1, 2, 3]

    async def test_what_was_picked_survives_an_error(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        answer = await client.post(
            "/admin/customers/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "",
                "email": "lena@fischer.de",
                "orders": ["1", "3"],
            },
        )

        assert answer.status_code == 422
        assert picked_in(answer) == [
            {"value": "1", "label": "Order #1"},
            {"value": "3", "label": "Order #3"},
        ]

    async def test_taking_them_all_away_empties_the_link(
        self, client: httpx.AsyncClient, big_database: Database
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        answer = await client.post(
            "/admin/customers/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "Lena Fischer",
                "email": "lena@fischer.de",
            },
        )

        assert answer.status_code == 303
        async with big_database.session() as session:
            left = await session.scalars(select(Order.id).where(Order.customer_id == 1))
            assert list(left) == []


class TestASmallTable:
    async def test_it_is_still_listed(self, database: Database) -> None:
        admin = Admin(database, views=[CustomerView], secret_key="for-the-session")
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            form = await client.get("/admin/customers/1/edit")

        assert "x-data='recordPicker(" not in form.text
        assert '<select class="select w-full' in form.text
