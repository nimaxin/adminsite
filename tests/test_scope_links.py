"""A record another view's scope_query hides never shows through a link to it."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, Link, ModelView, Statement
from adminsite.database import Database
from adminsite.views.naming import mark_unseen
from tests.models import Customer, Order, OrderStatus
from tests.support import Backend, count_queries


class GermanCustomers(ModelView[Customer]):
    """Lena, in Germany, is the only customer this user may see."""

    name = "customers"
    fields = [Customer.name, Customer.email, Customer.region]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region == "DE")


class PlainCustomers(ModelView[Customer]):
    name = "customers"
    fields = [Customer.name, Customer.email, Customer.region]


class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        Order.customer,
        Link(Order.customer, Customer.email),
        Order.total,
    ]
    fields_default_sort = [Order.id]


class OpenOrders(ModelView[Order]):
    """Every order but the refunded one, Aisha's order of 101.00."""

    name = "orders"
    fields = [Order.id, Order.customer, Order.total]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status != OrderStatus.REFUNDED)


class CustomerTotals(ModelView[Customer]):
    name = "customers"
    fields = [Customer.name, Link(Customer.orders, Order.total)]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def german(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(
        Admin(database, views=[OrderView, GermanCustomers], api=True)
    ) as client:
        yield client


def row_of(page: httpx.Response, key: str) -> str:
    """The list row of one order, opened by its link."""
    found = re.search(
        rf'<tr[^>]*>(?:(?!</tr>).)*href="/admin/orders/{key}"(?:(?!</tr>).)*</tr>',
        page.text,
        re.S,
    )
    assert found is not None
    return found.group(0)


class TestALinkToOneRecord:
    async def test_the_list_hides_the_name_and_its_columns(
        self, german: httpx.AsyncClient
    ) -> None:
        page = await german.get("/admin/orders")

        assert "Lena Fischer" in row_of(page, "1")
        assert "lena@fischer.de" in row_of(page, "1")
        marco = row_of(page, "3")
        assert "Hidden" in marco
        assert "Marco Rossi" not in page.text
        assert "marco@rossi.it" not in page.text

    async def test_the_record_page_hides_it_without_a_link(
        self, german: httpx.AsyncClient
    ) -> None:
        lena = await german.get("/admin/orders/1")
        marco = await german.get("/admin/orders/3")

        assert 'href="/admin/customers/1">Lena Fischer' in lena.text
        assert "Hidden" in marco.text
        assert "Marco Rossi" not in marco.text
        assert "marco@rossi.it" not in marco.text
        assert 'href="/admin/customers/2"' not in marco.text

    async def test_the_edit_form_hides_it(self, german: httpx.AsyncClient) -> None:
        form = await german.get("/admin/orders/3/edit")

        assert form.status_code == 200
        assert "Marco Rossi" not in form.text

    async def test_the_export_hides_it(self, german: httpx.AsyncClient) -> None:
        export = await german.get("/admin/orders/export")

        assert "Lena Fischer" in export.text
        assert "Marco Rossi" not in export.text
        assert "marco@rossi.it" not in export.text

    async def test_the_api_sends_null(self, german: httpx.AsyncClient) -> None:
        lena = (await german.get("/admin/-/api/orders/1")).json()
        marco = (await german.get("/admin/-/api/orders/3")).json()

        assert lena["customer"] == "1"
        assert marco["customer"] is None
        assert marco["customer.email"] is None

    async def test_it_costs_one_query_and_only_when_the_view_narrows(
        self, database: Database, backend: Backend
    ) -> None:
        counted = []
        for customers in (PlainCustomers, GermanCustomers):
            async with serve(Admin(database, views=[OrderView, customers])) as client:
                with count_queries(backend) as queries:
                    await client.get("/admin/orders")
                counted.append(queries.count)

        assert counted[1] == counted[0] + 1


class TestALinkToMany:
    async def test_a_column_read_through_it_leaves_out_what_the_scope_hides(
        self, database: Database
    ) -> None:
        async with serve(Admin(database, views=[CustomerTotals, OpenOrders])) as client:
            page = await client.get("/admin/customers")

        aisha = page.text.split("Aisha Khan", 1)[1].split("</tr>", 1)[0]
        assert "59.00" in aisha
        assert "101.00" not in aisha


class TestTheAuditLog:
    def test_it_keeps_what_is_true(self) -> None:
        view = OrderView()
        marco = Customer(name="Marco Rossi", email="marco@rossi.it", region="IT")
        order = Order(customer=marco, total=72)
        mark_unseen(marco)

        assert view._fields.display(order, "customer") == "Hidden"
        assert view._fields.display(order, "customer", as_seen=False) == "Marco Rossi"
        assert view._audit.snapshot(order, ["customer"]) == {"customer": "Marco Rossi"}
