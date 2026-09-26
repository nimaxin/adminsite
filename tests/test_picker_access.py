from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, OrderItem, Product


class CustomerView(ModelView, model=Customer):
    """Only German customers, and nobody may open the view itself."""

    display_template = "{name}"
    search_fields = ("name",)

    def scope_query(
        self, statement: Select[Any], *, request: Any = None
    ) -> Select[Any]:
        return statement.where(Customer.region == "DE")


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"
    form_fields = ("customer", "status")


class ClosedCustomers(ModelView, model=Customer):
    """A view nobody may see at all."""

    name = "closed_customers"
    display_template = "{name}"

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return False


class ItemView(ModelView, model=OrderItem):
    form_fields = ("product", "quantity")


class ReadOnlyProducts(ModelView, model=Product):
    display_template = "{name}"
    can_create = False
    can_edit = False


def mount(admin: Admin) -> Starlette:
    app = Starlette()
    app.mount("/admin", admin)
    return app


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database, views=[OrderView, CustomerView], secret_key="for-the-session"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mount(admin)),
        base_url="http://testserver",
    ) as client:
        yield client


class TestTheScopeOfTheTargetView:
    async def test_the_lookup_only_offers_what_the_scope_allows(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/orders/lookup/customer")

        assert "Lena Fischer" in found.text
        assert "Marco Rossi" not in found.text

    async def test_a_search_cannot_reach_past_it(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/orders/lookup/customer?q=Marco")

        assert "Marco" not in found.text
        assert 'role="option"' not in found.text

    async def test_the_select_on_a_form_follows_it_too(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        options = form.text.split('name="customer"', 1)[1].split("</select>", 1)[0]
        assert "Lena Fischer" in options
        assert "Marco Rossi" not in options


class TestAViewNobodyMaySee:
    async def test_the_lookup_refuses(self, database: Database) -> None:
        admin = Admin(
            database, views=[OrderView, ClosedCustomers], secret_key="a-secret"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            found = await client.get("/admin/orders/lookup/customer")

        assert found.status_code == 403

    async def test_the_form_still_draws_with_nothing_to_pick(
        self, database: Database
    ) -> None:
        admin = Admin(
            database, views=[OrderView, ClosedCustomers], secret_key="a-secret"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            form = await client.get("/admin/orders/1/edit")

        assert form.status_code == 200
        options = form.text.split('name="customer"', 1)[1].split("</select>", 1)[0]
        assert "Lena Fischer" not in options


class TestTheSourceView:
    async def test_a_view_that_cannot_be_written_has_no_lookup(
        self, database: Database
    ) -> None:
        class LockedOrders(ModelView, model=Order):
            name = "locked_orders"
            form_fields = ("customer",)
            can_create = False
            can_edit = False

        admin = Admin(
            database, views=[LockedOrders, CustomerView], secret_key="a-secret"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            found = await client.get("/admin/locked_orders/lookup/customer")

        assert found.status_code == 403

    async def test_a_view_that_can_only_create_still_has_one(
        self, database: Database
    ) -> None:
        class NewOnly(ModelView, model=Order):
            name = "new_orders"
            form_fields = ("customer",)
            can_edit = False

        admin = Admin(database, views=[NewOnly, CustomerView], secret_key="a-secret")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            found = await client.get("/admin/new_orders/lookup/customer")

        assert found.status_code == 200
        assert "Lena Fischer" in found.text


class TestAnInlineCell:
    async def test_it_follows_the_target_view_as_well(self, database: Database) -> None:
        class ScopedProducts(ModelView, model=Product):
            display_template = "{name}"

            def scope_query(
                self, statement: Select[Any], *, request: Any = None
            ) -> Select[Any]:
                return statement.where(Product.name == "Linen shirt")

        from adminsite import Inline

        class OrderWithItems(ModelView, model=Order):
            name = "packed_orders"
            form_fields = ("status",)
            inlines = (Inline("items", fields=("product", "quantity")),)

        admin = Admin(
            database,
            views=[OrderWithItems, ScopedProducts],
            secret_key="a-secret",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            form = await client.get("/admin/packed_orders/1/edit")

        assert "Linen shirt" in form.text
        assert "Canvas tote" not in form.text


class TestATargetWithNoView:
    async def test_it_still_offers_its_records(self, database: Database) -> None:
        admin = Admin(database, views=[ItemView], secret_key="a-secret")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mount(admin)),
            base_url="http://testserver",
        ) as client:
            form = await client.get("/admin/order_items/1/edit")

        assert "Linen shirt" in form.text
