import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from starlette.applications import Starlette

from adminsite import Admin, Computed, ModelView
from adminsite.backends.sqlalchemy import SessionAdapter
from adminsite.exceptions import AdminSiteError
from tests.models import Customer, Order
from tests.support import Backend, count_queries


async def order_counts(
    session: SessionAdapter, customers: Sequence[Customer]
) -> Mapping[Any, Any]:
    rows = await session.execute(
        select(Order.customer_id, func.count())
        .where(Order.customer_id.in_([customer.id for customer in customers]))
        .group_by(Order.customer_id)
    )
    return dict(rows.tuples().all())


# The start of the next cell in a list row, up to the value it shows.
CELL = r"\s*<td[^>]*>\s*<span[^>]*>"


class CustomerView(ModelView, model=Customer):
    display_template = "{name}"
    list_display = ("name", "orders_placed")
    form_fields = ("name", "email", "orders_placed")
    ordering = ("name",)
    fields = (
        Computed("orders_placed", load=order_counts, label="Orders placed", default=0),
    )


@pytest.fixture
async def client(backend: Backend) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(backend.database, views=[CustomerView], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def counts_by_name(backend: Backend) -> dict[str, int]:
    async with backend.database.session() as session:
        rows = await session.execute(
            select(Customer.name, func.count(Order.id))
            .outerjoin(Order, Order.customer_id == Customer.id)
            .group_by(Customer.name)
        )
        return dict(rows.tuples().all())


class TestAValueFromTheDatabase:
    async def test_the_list_shows_it(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        expected = await counts_by_name(backend)

        page = await client.get("/admin/customers")

        for name, count in expected.items():
            row = rf">{name}</a>\s*</td>{CELL}{count}</span>"
            assert re.search(row, page.text), name

    async def test_the_page_costs_the_same_however_many_rows_it_holds(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        with count_queries(backend) as two:
            await client.get("/admin/customers?size=2")
        with count_queries(backend) as four:
            await client.get("/admin/customers?size=4")

        assert two.count == four.count

    async def test_the_record_page_and_the_form_show_it(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        lena = (await counts_by_name(backend))["Lena Fischer"]

        record = await client.get("/admin/customers/1")
        form = await client.get("/admin/customers/1/edit")

        assert re.search(rf"Orders placed</dt>\s*<dd[^>]*>\s*{lena}\s*<", record.text)
        assert f'value="{lena}"' in form.text

    async def test_the_export_and_the_api_carry_it(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        expected = await counts_by_name(backend)

        export = await client.get("/admin/customers/export")
        api = await client.get("/admin/-/api/customers")

        rows = [line.split(",") for line in export.text.strip().splitlines()[1:]]
        assert {name: int(count) for name, count in rows} == expected
        items = api.json()["items"]
        assert {item["name"]: int(item["orders_placed"]) for item in items} == expected

    async def test_a_record_the_loader_leaves_out_gets_the_default(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        async with backend.database.session() as session:
            await session.add(Customer(name="Nadia New", email="nadia@new.example"))
            await session.commit()

        page = await client.get("/admin/customers?q=Nadia")

        assert re.search(rf">Nadia New</a>\s*</td>{CELL}0</span>", page.text)


class TestDeclaringOne:
    def test_it_takes_one_way_of_working_the_value_out(self) -> None:
        with pytest.raises(AdminSiteError, match="one of the two"):
            Computed("orders_placed")
        with pytest.raises(AdminSiteError, match="one of the two"):
            Computed("orders_placed", lambda customer: 1, load=order_counts)
