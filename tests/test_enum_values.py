"""An EnumField keeps what its column holds through a form and back."""

import enum
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, ModelView, SaveContext
from adminsite.database import Database
from adminsite.fields import EnumField
from tests.models import Customer, Order, OrderStatus

seen: dict[str, Any] = {}


class Region(enum.StrEnum):
    GERMANY = "DE"
    ITALY = "IT"
    BRITAIN = "UK"
    SWEDEN = "SE"
    EUROPE = "EU"


class OrderView(ModelView[Order]):
    # Keyed by the enum's values, as a translated list of statuses often is.
    fields = [
        Order.id,
        EnumField(
            Order.status,
            choices=[
                ("pending", "Waiting"),
                ("paid", "Paid up"),
                ("shipped", "On its way"),
                ("refunded", "Money back"),
            ],
        ),
        Order.note,
    ]

    async def before_save(self, context: SaveContext[Order]) -> None:
        seen["status"] = context.values[Order.status].get()


class CustomerView(ModelView[Customer]):
    fields = [Customer.name, EnumField(Customer.region, enum=Region)]


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    seen.clear()
    admin = Admin(database, views=[OrderView, CustomerView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def selected(page: httpx.Response, name: str) -> list[str]:
    """The options a select on the page has chosen, as a browser sends them."""
    select_tag = re.search(rf'<select[^>]*name="{name}".*?</select>', page.text, re.S)
    assert select_tag is not None
    return re.findall(r'<option value="([^"]*)"\s+selected', select_tag.group(0))


class TestChoicesOnAnEnumColumn:
    async def test_the_form_opens_on_the_stored_status(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/2/edit")

        assert selected(page, "status") == ["paid"]

    async def test_saving_another_field_keeps_the_status(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/orders/2/edit")
        sent = {"status": selected(page, "status"), "note": "Leave at the door"}

        answer = await client.post("/admin/orders/2/edit", data=sent)

        assert answer.status_code == 303
        assert seen["status"] is OrderStatus.PAID
        async with database.session() as session:
            order = await session.scalar(select(Order).where(Order.id == 2))
            assert order is not None
            assert (order.status, order.note) == (OrderStatus.PAID, "Leave at the door")

    async def test_the_record_page_shows_the_label_given(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/2")

        assert "Paid up" in page.text


class TestAnEnumOnAStringColumn:
    async def test_the_form_opens_on_the_stored_value(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers/1/edit")

        assert selected(page, "region") == ["DE"]

    async def test_saving_another_field_keeps_the_value(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        page = await client.get("/admin/customers/1/edit")
        sent = {"name": "Lena Weber", "region": selected(page, "region")}

        answer = await client.post("/admin/customers/1/edit", data=sent)

        assert answer.status_code == 303
        async with database.session() as session:
            lena = await session.scalar(select(Customer).where(Customer.id == 1))
            assert lena is not None
            assert (lena.name, lena.region) == ("Lena Weber", "DE")

    async def test_another_member_is_stored_as_its_value(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        sent = {"name": "Lena Fischer", "region": "IT"}

        answer = await client.post("/admin/customers/1/edit", data=sent)

        assert answer.status_code == 303
        async with database.session() as session:
            region = await session.scalar(
                select(Customer.region).where(Customer.id == 1)
            )
            assert region == "IT"

    async def test_the_record_page_shows_the_member_s_label(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers/1")

        assert "Germany" in page.text
