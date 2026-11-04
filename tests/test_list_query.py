"""list_query keeps records off the list, while links still reach them."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, Inline, ModelView, Statement
from adminsite.actions import Selection, action
from adminsite.database import Database
from tests.models import Customer, Order, OrderItem, Product
from tests.support import counted


class CustomerView(ModelView[Customer]):
    """Marco Rossi, the only customer in Italy, is off the list."""

    fields = [Customer.name, Customer.email, Customer.region]
    record_title = "{name}"
    searchable_fields = [Customer.name]
    list_filters = [Customer.region]

    def list_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region != "IT")

    @action("Move to France")
    async def move(self, selection: Selection[Customer]) -> str:
        return f"{await selection.update(region='FR')} moved."


class OutsideGermany(CustomerView):
    """The same list, for a user who may only see customers outside Germany."""

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region != "DE")


class OrderView(ModelView[Order]):
    fields = [Order.id, Order.customer, Order.total]
    record_title = "Order #{id}"
    fields_default_sort = [Order.id]


class ProductView(ModelView[Product]):
    """The wool scarf is off the list. Only order 2 holds it."""

    record_title = "{name}"

    def list_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Product.name != "Wool scarf")


class OrderWithItems(ModelView[Order]):
    name = "orders"
    fields = [Order.customer, Order.total]
    inlines = [Inline(Order.items, fields=[OrderItem.product, OrderItem.quantity])]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[CustomerView, OrderView], api=True)
    async with serve(admin) as client:
        yield client


class TestTheList:
    async def test_it_leaves_the_record_out(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers")

        assert "Lena Fischer" in page.text
        assert "Marco Rossi" not in page.text

    async def test_its_count_leaves_it_out_too(self, client: httpx.AsyncClient) -> None:
        total = await counted(client, "/admin/customers")

        assert "3 customers" in total

    async def test_its_filters_offer_only_what_it_shows(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers?region=IT")

        assert "Marco Rossi" not in page.text

    async def test_the_export_leaves_it_out(self, client: httpx.AsyncClient) -> None:
        export = await client.get("/admin/customers/export")

        assert "Lena Fischer" in export.text
        assert "Marco Rossi" not in export.text

    async def test_the_api_list_leaves_it_out(self, client: httpx.AsyncClient) -> None:
        listed = (await client.get("/admin/-/api/customers")).json()

        assert listed["total"] == 3
        assert "Marco Rossi" not in [item["name"] for item in listed["items"]]

    async def test_selecting_everything_leaves_it_out(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post("/admin/customers/action/move", data={"everything": "1"})
        marco = (await client.get("/admin/-/api/customers/2")).json()
        lena = (await client.get("/admin/-/api/customers/1")).json()

        assert marco["region"] == "IT"
        assert lena["region"] == "FR"

    async def test_the_command_palette_leaves_it_out(
        self, client: httpx.AsyncClient
    ) -> None:
        marco = await client.get("/admin/-/search?q=marco")
        lena = await client.get("/admin/-/search?q=lena")

        assert "Marco Rossi" not in marco.text
        assert "Lena Fischer" in lena.text

    async def test_a_picker_on_another_form_leaves_it_out(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/orders/lookup/customer")

        assert "Lena Fischer" in found.text
        assert "Marco Rossi" not in found.text


class TestTheRecordStaysReachable:
    async def test_its_own_page_opens(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers/2")

        assert page.status_code == 200
        assert "Marco Rossi" in page.text

    async def test_a_list_linking_to_it_names_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert "Marco Rossi" in page.text
        assert "Hidden" not in page.text

    async def test_a_record_linking_to_it_opens_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/3")

        assert 'href="/admin/customers/2">Marco Rossi' in page.text

    async def test_a_form_holding_it_keeps_it(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/orders/3/edit")

        options = form.text.split('name="customer"', 1)[1].split("</select>", 1)[0]
        assert re.search(r'<option value="2"\s+selected', options)
        assert "Marco Rossi" in options

    async def test_a_form_holding_another_offers_only_the_list(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        options = form.text.split('name="customer"', 1)[1].split("</select>", 1)[0]
        assert "Lena Fischer" in options
        assert "Marco Rossi" not in options

    async def test_an_inline_row_holding_it_keeps_it(self, database: Database) -> None:
        admin = Admin(database, views=[OrderWithItems, ProductView])
        async with serve(admin) as client:
            holding = await client.get("/admin/orders/2/edit")
            other = await client.get("/admin/orders/3/edit")

        assert "Wool scarf" in holding.text
        assert "Wool scarf" not in other.text

    async def test_the_api_reads_it(self, client: httpx.AsyncClient) -> None:
        marco = await client.get("/admin/-/api/customers/2")

        assert marco.status_code == 200
        assert marco.json()["name"] == "Marco Rossi"


class TestWithAScope:
    async def test_the_list_follows_both(self, database: Database) -> None:
        admin = Admin(database, views=[OutsideGermany, OrderView])
        async with serve(admin) as client:
            page = await client.get("/admin/customers")

        assert "Aisha Khan" in page.text
        assert "Jonas Berg" in page.text
        assert "Lena Fischer" not in page.text
        assert "Marco Rossi" not in page.text

    async def test_the_scope_still_hides_the_record_page(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[OutsideGermany, OrderView])
        async with serve(admin) as client:
            lena = await client.get("/admin/customers/1")
            marco = await client.get("/admin/customers/2")

        assert lena.status_code == 404
        assert marco.status_code == 200
