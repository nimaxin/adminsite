import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import (
    Admin,
    Chart,
    ModelCounts,
    ModelView,
    Permission,
    RecentRecords,
    Stat,
    Widget,
)
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.dashboard import ChartData, label_text
from tests.models import Customer, Order, OrderStatus


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"


class CustomerView(ModelView, model=Customer):
    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return False


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def overview(database: Database, *cards: Widget, **options: Any) -> str:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, CustomerView],
        dashboard=cards,
        **options,
    )
    async with serve(admin) as client:
        page = await client.get("/admin/")
    assert page.status_code == 200
    return page.text


async def load(database: Database, widget: Widget) -> Any:
    admin = Admin(database, views=[OrderView])
    return await widget.load(admin, Request({"type": "http", "headers": []}))


class TestStat:
    async def test_the_value_is_formatted(self, database: Database) -> None:
        text = await overview(
            database,
            Stat("Revenue", select(func.sum(Order.total)), format="€{:,.2f}"),
        )

        assert "Revenue" in text
        assert "€519.50" in text

    async def test_a_change_on_the_period_before(self, database: Database) -> None:
        paid = select(func.count()).where(Order.status == OrderStatus.PAID)
        shipped = select(func.count()).where(Order.status == OrderStatus.SHIPPED)
        stat = Stat("Paid", paid, previous=select(func.count()).select_from(Order))

        data = await load(database, stat)
        grew = await load(database, Stat("Shipped", shipped, previous=paid))

        assert data.value == "2"
        assert data.change == pytest.approx(-71.43, abs=0.01)
        assert grew.change == 0

    async def test_nothing_shows_a_dash(self, database: Database) -> None:
        data = await load(
            database, Stat("None", select(func.sum(Order.total)).where(Order.id < 0))
        )

        assert data.value == "—"

    async def test_a_function_can_give_the_value(self, database: Database) -> None:
        async def active(session: SessionAdapter) -> int:
            return int(await session.scalar(select(func.count(Customer.id))) or 0)

        data = await load(database, Stat("Customers", active))

        assert data.value == "4"


class TestChart:
    async def test_bars_are_scaled_to_the_highest(self, database: Database) -> None:
        async def rows(session: SessionAdapter) -> list[tuple[str, int]]:
            return [("a", 1), ("b", 2), ("c", 4)]

        data: ChartData = await load(database, Chart("Sales", rows))

        assert [point.height for point in data.points] == [24, 48, 96]
        assert [point.x for point in data.points] == [0, 10, 20]
        assert data.width == 30
        assert data.highest == "4"

    async def test_a_select_gives_the_rows(self, database: Database) -> None:
        text = await overview(
            database,
            Chart(
                "Orders by status",
                select(Order.status, func.count()).group_by(Order.status),
            ),
        )

        assert "<svg" in text
        assert "<caption>Orders by status</caption>" in text

    async def test_a_line_chart_draws_a_line(self, database: Database) -> None:
        day = func.date(Order.created_at)
        text = await overview(
            database,
            Chart(
                "Orders per day",
                select(day, func.count()).group_by(day).order_by(day),
                kind="line",
            ),
        )

        assert "<polyline" in text

    async def test_no_rows_says_so(self, database: Database) -> None:
        text = await overview(
            database,
            Chart("Refunds", select(Order.status, Order.id).where(Order.id < 0)),
        )

        assert "No data yet." in text

    def test_only_bars_and_lines(self) -> None:
        with pytest.raises(ValueError, match="'bar' or 'line'"):
            Chart("Pie", select(Order.id), kind="pie")

    def test_labels_are_short(self) -> None:
        assert label_text(datetime.date(2026, 9, 14)) == "Sep 14"
        assert label_text(Decimal("1234.5")) == "1,234.5"
        assert label_text(OrderStatus.PAID) == "paid"


class TestRecentRecords:
    async def test_the_latest_records_link_to_their_pages(
        self, database: Database
    ) -> None:
        text = await overview(
            database,
            RecentRecords(
                "Latest orders", "orders", sort="-created_at", detail="total"
            ),
        )

        assert "Latest orders" in text
        assert 'href="/admin/orders/7"' in text
        assert text.index("Order #7") < text.index("Order #6")
        assert "Order #2" not in text

    async def test_a_view_you_may_not_open_hides_the_card(
        self, database: Database
    ) -> None:
        text = await overview(database, RecentRecords("Latest customers", "customers"))

        assert "Latest customers" not in text


class TestTheOverview:
    async def test_the_counts_are_the_default(self, database: Database) -> None:
        admin = Admin(database, views=[OrderView, CustomerView])
        async with serve(admin) as client:
            text = (await client.get("/admin/")).text

        assert isinstance(admin.dashboard[0], ModelCounts)
        assert 'href="/admin/orders"' in text
        assert ">7</div>" in text

    async def test_a_broken_card_does_not_break_the_page(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def broken(session: SessionAdapter) -> int:
            raise RuntimeError("the warehouse is down")

        text = await overview(
            database,
            Stat("Stock", broken),
            Stat("Orders", select(func.count(Order.id))),
        )

        assert "This card could not be loaded." in text
        assert "Orders" in text
        assert "the warehouse is down" in caplog.text

    async def test_a_card_of_your_own(self, database: Database, tmp_path: Path) -> None:
        (tmp_path / "weather.html").write_text(
            "<p>{{ widget.title }}: {{ data }}</p>", encoding="utf-8"
        )

        class Weather(Widget):
            title = "Weather"
            template = "weather.html"

            async def load(self, admin: Admin, request: Request) -> str:
                return "sunny"

        text = await overview(database, Weather(), template_dirs=[tmp_path])

        assert "<p>Weather: sunny</p>" in text

    async def test_a_card_can_be_hidden(self, database: Database) -> None:
        class Secret(Stat):
            async def allows(self, admin: Admin, request: Request) -> bool:
                return False

        text = await overview(database, Secret("Margin", select(func.count(Order.id))))

        assert "Margin" not in text
