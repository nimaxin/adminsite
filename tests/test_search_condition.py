from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import ColumnElement
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from tests.models import Customer, Order
from tests.support import Backend, count_queries


class CustomerView(ModelView, model=Customer):
    display_template = "{name}"
    search_fields = ("name", "email")

    def search_condition(
        self, term: str, *, request: Any = None
    ) -> ColumnElement[bool] | None:
        # An address is looked up whole, which an index on the column can
        # answer. Anything else goes to the usual search.
        if "@" in term:
            return Customer.email == term.lower()
        return None

    @action("Count them")
    async def count_them(self, selection: Selection) -> str:
        return f"{await selection.count()} chosen"


class OrderView(ModelView, model=Order):
    display_template = "Order #{id}"
    form_fields = ("customer", "status")


@pytest.fixture
async def client(backend: Backend) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(backend.database, views=[CustomerView, OrderView], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def customer_queries(statements: list[str]) -> list[str]:
    return [item.lower() for item in statements if "customers" in item.lower()]


class TestTheViewsOwnCondition:
    async def test_the_list_uses_it_and_no_like(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        with count_queries(backend) as queries:
            page = await client.get("/admin/customers?q=LENA@fischer.de")

        assert "Lena Fischer" in page.text
        assert "Marco Rossi" not in page.text
        assert "1 customer" in page.text
        assert not any("like" in item for item in customer_queries(queries.statements))

    async def test_a_term_it_leaves_alone_is_searched_as_usual(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers?q=len")

        assert "Lena Fischer" in page.text
        assert "Marco Rossi" not in page.text

    async def test_the_export_uses_it(self, client: httpx.AsyncClient) -> None:
        export = await client.get("/admin/customers/export?q=lena@fischer.de")

        rows = export.text.strip().splitlines()[1:]
        assert len(rows) == 1
        assert "Lena Fischer" in rows[0]

    async def test_the_palette_uses_it(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=marco@rossi.it")

        assert "Marco Rossi" in found.text
        assert "Lena Fischer" not in found.text

    async def test_a_picker_on_another_form_uses_it(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/orders/lookup/customer?q=aisha@khan.co.uk")

        assert "Aisha Khan" in found.text
        assert "Lena Fischer" not in found.text

    async def test_selecting_everything_that_matches_uses_it(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post(
            "/admin/-/api/customers/actions/count_them?q=jonas@berg.se",
            json={"everything": True},
        )

        assert answer.json() == {"message": "1 chosen"}
