"""The reference order view and the field gallery run, and not only type-check.

The order view runs on async SQLite alone: export_day asks for an
AsyncSession, which the admin refuses to start with on a sync database. The
gallery runs on async and sync SQLite.
"""

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.reference import gallery, orders
from tests.reference.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Promotion,
    Supplier,
    Tag,
)

only_async = pytest.mark.parametrize("reference_database", ["async"], indirect=True)


async def become_manager(admin: Admin, request: Request) -> Response:
    """Stands in for a sign in that makes someone a manager."""
    request.session["manager"] = True
    return PlainTextResponse("You manage the shop now.")


async def join_region(admin: Admin, request: Request) -> Response:
    """Stands in for a sign in that ties someone to one region."""
    request.session["region"] = request.query_params["region"]
    return PlainTextResponse("You see one region now.")


@pytest.fixture
async def shop(reference_database: Database) -> Database:
    """Two customers in two regions, and an order for each."""
    async with reference_database.session() as session:
        shirt = Product(
            sku="SHIRT-1",
            name="Linen shirt",
            supplier=Supplier(
                name="Linen Mill", email="mill@example.com", country="NL"
            ),
            price=Decimal("40.00"),
            cost=Decimal("20.00"),
        )
        lena = Customer(name="Lena Fischer", email="lena@example.com", region="EU")
        sam = Customer(name="Sam Carter", email="sam@example.com", region="US")
        for order in [
            Order(
                customer=lena,
                total=Decimal("80.00"),
                items=[OrderItem(product=shirt, quantity=2, unit_price=shirt.price)],
            ),
            Order(
                customer=sam,
                status=OrderStatus.PAID,
                total=Decimal("40.00"),
                note="Paid by bank transfer.",
                items=[OrderItem(product=shirt, quantity=1, unit_price=shirt.price)],
            ),
        ]:
            await session.add(order)
        await session.commit()
    return reference_database


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(shop: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        shop,
        views=[orders.OrderView, orders.CustomerView],
        secret_key="for-the-session",
    )
    admin.add_route("/-/manager", become_manager)
    admin.add_route("/-/region", join_region)
    async with serve(admin) as client:
        yield client


async def token(client: httpx.AsyncClient, path: str = "/admin/orders") -> str:
    page = await client.get(path)
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def run(
    client: httpx.AsyncClient, name: str, data: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"/admin/orders/action/{name}",
        data={"_csrf": await token(client), **data},
        follow_redirects=True,
    )


async def stored(database: Database, key: int) -> Order:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None
        return order


async def placed_on(database: Database, key: int) -> datetime:
    return (await stored(database, key)).created_at


@only_async
class TestTheOrderPages:
    @pytest.mark.parametrize(
        "path",
        [
            "/admin/orders",
            "/admin/orders/1",
            "/admin/orders/1/edit",
            "/admin/orders/new",
            "/admin/customers",
        ],
    )
    async def test_each_opens(self, client: httpx.AsyncClient, path: str) -> None:
        page = await client.get(path)

        assert page.status_code == 200

    async def test_the_list_names_each_customer(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert "Lena Fischer (lena@example.com)" in page.text
        assert "Sam Carter (sam@example.com)" in page.text

    async def test_staff_of_a_region_see_only_its_orders(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.get("/admin/-/region", params={"region": "US"})

        page = await client.get("/admin/orders")
        hidden = await client.get("/admin/orders/1")

        assert "sam@example.com" in page.text
        assert "lena@example.com" not in page.text
        assert hidden.status_code == 404

    async def test_only_a_manager_sees_the_total(
        self, client: httpx.AsyncClient
    ) -> None:
        staff = await client.get("/admin/orders")
        await client.get("/admin/-/manager")
        manager = await client.get("/admin/orders")

        assert "€80.00" not in staff.text
        assert "€80.00" in manager.text


@only_async
class TestTheOrderHooks:
    async def test_marking_an_order_paid_by_hand_needs_a_note(
        self, client: httpx.AsyncClient, shop: Database
    ) -> None:
        answer = await client.post(
            "/admin/orders/new",
            data={
                "_csrf": await token(client),
                "customer": "1",
                "status": "paid",
                "note": "",
                "items-count": "0",
            },
        )

        assert "Say why the order was marked paid by hand." in answer.text
        async with shop.session() as session:
            assert len((await session.scalars(select(Order))).all()) == 2

    async def test_a_new_order_sends_a_confirmation(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confirmed: list[Order] = []

        async def confirm(order: Order) -> None:
            confirmed.append(order)

        monkeypatch.setattr(orders, "send_confirmation", confirm)

        answer = await client.post(
            "/admin/orders/new",
            data={
                "_csrf": await token(client),
                "customer": "2",
                "status": "pending",
                "note": "",
                "items-count": "0",
            },
        )

        assert answer.status_code == 303
        assert [order.id for order in confirmed] == [3]

    async def test_a_deleted_order_sends_a_cancellation(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cancelled: list[Order] = []

        async def cancel(order: Order) -> None:
            cancelled.append(order)

        monkeypatch.setattr(orders, "send_cancellation", cancel)

        await client.post(
            "/admin/orders/2/delete",
            data={"_csrf": await token(client, "/admin/orders/2")},
        )

        assert [order.id for order in cancelled] == [2]


@only_async
class TestTheOrderActions:
    async def test_marking_orders_shipped(
        self, client: httpx.AsyncClient, shop: Database
    ) -> None:
        answer = await run(client, "mark_shipped", {"keys": ["1", "2"]})

        assert "2 orders marked as shipped." in answer.text
        assert (await stored(shop, 1)).status is OrderStatus.SHIPPED
        assert (await stored(shop, 2)).status is OrderStatus.SHIPPED

    async def test_a_shipped_order_keeps_its_customer_and_status(
        self, client: httpx.AsyncClient
    ) -> None:
        before = await client.get("/admin/orders/1/edit")
        await run(client, "mark_shipped", {"keys": ["1"]})

        form = await client.get("/admin/orders/1/edit")

        assert 'name="status"' in before.text
        assert 'name="customer"' in before.text
        assert 'name="status"' not in form.text
        assert 'name="customer"' not in form.text
        assert 'name="note"' in form.text

    async def test_refunding_an_order_asks_why(
        self, client: httpx.AsyncClient, shop: Database
    ) -> None:
        answer = await run(client, "refund", {"keys": "1", "reason": "Arrived torn."})

        refunded = await stored(shop, 1)
        assert "Order #1 is refunded." in answer.text
        assert refunded.status is OrderStatus.REFUNDED
        assert refunded.note == "Arrived torn. (refunded by staff)"

    async def test_exporting_a_day_runs_its_own_query(
        self, client: httpx.AsyncClient, shop: Database
    ) -> None:
        day = (await placed_on(shop, 1)).date()

        answer = await run(client, "export_day", {"day": day.isoformat()})

        assert answer.headers["content-type"].startswith("text/csv")
        assert sorted(answer.text.splitlines()) == ["1,80.00", "2,40.00"]

    async def test_a_region_exports_only_its_orders(
        self, client: httpx.AsyncClient, shop: Database
    ) -> None:
        day = (await placed_on(shop, 1)).date()
        await client.get("/admin/-/region", params={"region": "US"})

        answer = await run(client, "export_day", {"day": day.isoformat()})

        assert answer.text.splitlines() == ["2,40.00"]


class TestTheGallery:
    def test_its_admin_starts(self) -> None:
        assert [view.name for view in gallery.admin.views] == [
            "promotions",
            "suppliers",
            "tags",
        ]

    @pytest.fixture
    async def client(
        self, reference_database: Database
    ) -> AsyncIterator[httpx.AsyncClient]:
        async with reference_database.session() as session:
            await session.add(Promotion(title="Summer sale", tags=[Tag(name="sale")]))
            await session.commit()
        admin = Admin(
            reference_database,
            views=[gallery.PromotionView, gallery.SupplierView, ModelView[Tag]],
        )
        async with serve(admin) as client:
            yield client

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/promotions",
            "/admin/promotions/1",
            "/admin/promotions/1/edit",
            "/admin/promotions/new",
        ],
    )
    async def test_each_page_opens(self, client: httpx.AsyncClient, path: str) -> None:
        page = await client.get(path)

        assert page.status_code == 200

    async def test_the_secret_code_is_stored_as_a_hash(
        self, client: httpx.AsyncClient, reference_database: Database
    ) -> None:
        answer = await client.post(
            "/admin/promotions/new",
            data={"title": "Winter sale", "secret_code": "let-me-in"},
        )

        async with reference_database.session() as session:
            promotion = await session.scalar(
                select(Promotion).where(Promotion.title == "Winter sale")
            )
        assert answer.status_code == 303
        assert promotion is not None
        assert promotion.secret_code_hash
        assert "let-me-in" not in promotion.secret_code_hash
