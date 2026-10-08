"""A view can keep its open list up to date, reading its rows again."""

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, AdminSiteError, ModelView
from adminsite.database import Database
from adminsite.inspector import SQLAlchemyInspector
from tests.models import Order, OrderStatus

TABLE = {"HX-Request": "true"}


class LiveOrders(ModelView[Order]):
    name = "orders"
    fields = ["id", "status", "total"]
    list_filters = [Order.status]
    list_refresh_seconds = 30


class StillOrders(ModelView[Order]):
    name = "orders"
    fields = ["id", "status", "total"]


def serve(database: Database, view: type[ModelView[Order]]) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", Admin(database, views=[view]))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestTheList:
    async def test_it_reads_its_rows_again_as_often_as_the_view_says(
        self, database: Database
    ) -> None:
        async with serve(database, LiveOrders) as client:
            page = await client.get("/admin/orders")

        refresher = page.text[page.text.index('<div id="records-refresh"') :]
        refresher = refresher[: refresher.index("</div>")]
        assert 'hx-trigger="every 30s [listIdle()], refresh-list"' in refresher
        assert 'hx-get="/admin/orders"' in refresher
        assert 'hx-target="#records"' in refresher
        assert 'data-seconds="30"' in refresher
        assert "function listIdle()" in page.text

    async def test_the_reading_sits_outside_the_table_it_draws_again(
        self, database: Database
    ) -> None:
        async with serve(database, LiveOrders) as client:
            table = await client.get("/admin/orders", headers=TABLE)

        assert table.text.startswith('<div id="records"')
        assert "records-refresh" not in table.text

    async def test_a_view_that_does_not_ask_never_reads_again(
        self, database: Database
    ) -> None:
        async with serve(database, StillOrders) as client:
            page = await client.get("/admin/orders")

        assert 'id="records-refresh"' not in page.text

    async def test_rows_read_again_show_what_came_since(
        self, database: Database
    ) -> None:
        async with serve(database, LiveOrders) as client:
            before = await client.get("/admin/orders?status=PAID", headers=TABLE)
            async with database.session() as session:
                for status in (OrderStatus.PAID, OrderStatus.PENDING):
                    await session.add(
                        Order(
                            customer_id=1,
                            status=status,
                            total=Decimal("123.45"),
                            created_at=datetime(2026, 9, 30, 12, 0),
                        )
                    )
                await session.commit()
            after = await client.get("/admin/orders?status=PAID", headers=TABLE)

        assert "123.45" not in before.text
        assert after.text.count("123.45") == 2


class TestTheSetting:
    @pytest.mark.parametrize("seconds", [0, -5, 2.5, "30", True])
    def test_it_takes_a_whole_number_of_seconds(self, seconds: Any) -> None:
        class Refreshing(ModelView[Order]):
            list_refresh_seconds = seconds

        with pytest.raises(AdminSiteError, match="whole number of seconds"):
            Refreshing(SQLAlchemyInspector())
