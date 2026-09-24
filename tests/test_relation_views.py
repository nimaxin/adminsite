from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import Select, select
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import RelationField
from tests.models import Customer, Order, OrderStatus
from tests.support import Backend, count_queries


class CustomerView(ModelView, model=Customer):
    """Every customer outside Sweden."""

    display_template = "{name}"
    detail_fields = ("name", "email", "orders")

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region != "SE")


class NordicView(ModelView, model=Customer):
    """The Swedish customers, the ones the first view leaves out."""

    name = "nordic"
    display_template = "{name}"

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region == "SE")


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"
    form_fields = ("customer", "status")


class NordicOrderView(ModelView, model=Order):
    """Orders whose customer is picked from, and linked to, the Nordic view."""

    name = "nordic_orders"
    display_template = "Order #{id}"
    form_fields = ("customer", "status")
    fields = (FieldOptions("customer", view="nordic"),)


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database, views=[CustomerView, NordicView, OrderView, NordicOrderView]
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def customer_and_order(database: Database, email: str) -> tuple[int, int]:
    async with database.session() as session:
        wanted = select(Customer.id).where(Customer.email == email)
        customer = await session.scalar(wanted)
        order = await session.scalar(
            select(Order.id).where(Order.customer_id == customer).order_by(Order.id)
        )
    assert customer is not None and order is not None
    return int(customer), int(order)


class TestAFieldThatNamesItsView:
    async def test_its_picker_lists_from_that_view(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/nordic_orders/lookup/customer")

        assert "Jonas Berg" in found.text
        assert "Lena Fischer" not in found.text

    async def test_its_link_opens_that_view(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, order = await customer_and_order(database, "jonas@berg.se")

        page = await client.get(f"/admin/nordic_orders/{order}")

        assert f'href="/admin/nordic/{jonas}"' in page.text

    async def test_saving_checks_the_key_against_that_view(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, order = await customer_and_order(database, "jonas@berg.se")
        lena, _other = await customer_and_order(database, "lena@fischer.de")

        refused = await client.post(
            f"/admin/nordic_orders/{order}/edit",
            data={"customer": str(lena), "status": "PAID"},
        )
        kept = await client.post(
            f"/admin/nordic_orders/{order}/edit",
            data={"customer": str(jonas), "status": "PAID"},
        )

        assert refused.status_code == 422
        assert "Choose a record." in refused.text
        assert kept.status_code == 303

    def test_a_view_that_does_not_exist_is_named(self, database: Database) -> None:
        admin = Admin(database, views=[CustomerView])
        field = RelationField("customer", target=Customer, view="buyers")

        with pytest.raises(AdminSiteError, match="'buyers'"):
            admin.views.for_relation(field)

    def test_a_view_of_another_model_is_named(self, database: Database) -> None:
        admin = Admin(database, views=[CustomerView, OrderView])
        field = RelationField("customer", target=Customer, view="orders")

        with pytest.raises(AdminSiteError, match="shows Order, not Customer"):
            admin.views.for_relation(field)


class TestALinkWithoutAView:
    async def test_it_opens_the_view_that_holds_the_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, jonas_order = await customer_and_order(database, "jonas@berg.se")
        lena, lena_order = await customer_and_order(database, "lena@fischer.de")

        swedish = await client.get(f"/admin/orders/{jonas_order}")
        german = await client.get(f"/admin/orders/{lena_order}")

        # The first view leaves Swedish customers out, so rather than a
        # link that answers 404, it takes the one that holds them.
        assert f'href="/admin/nordic/{jonas}"' in swedish.text
        assert f'href="/admin/customers/{lena}"' in german.text


class TestAToManyLinkOnTheRecordPage:
    async def test_it_names_twenty_and_counts_the_rest(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        lena, _order = await customer_and_order(backend.database, "lena@fischer.de")
        async with backend.database.session() as session:
            for day in range(1, 31):
                await session.add(
                    Order(
                        customer_id=lena,
                        status=OrderStatus.PENDING,
                        created_at=datetime(2026, 8, day % 28 + 1),
                    )
                )
            await session.commit()
            hers = select(Order.id).where(Order.customer_id == lena)
            total = len((await session.scalars(hers)).all())

        with count_queries(backend) as queries:
            page = await client.get(f"/admin/customers/{lena}")

        assert f"and {total - 20:,} more" in page.text
        assert page.text.count("Order #") == 20
        orders = [
            item.lower() for item in queries.statements if "from orders" in item.lower()
        ]
        assert orders
        assert all("limit" in item or "count(" in item for item in orders)
