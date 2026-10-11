"""An input left empty never asks a column that cannot be null to hold None.

A new record left without a value gets the column's default. A record that
exists has none to fall back on: text stores "", and any other field is
refused on the form, as a required one is.
"""

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, Field, Inline, ModelView
from adminsite._importing import build_plan, read_table
from adminsite.database import Database
from adminsite.fields import FileField
from adminsite.files import LocalStorage
from tests.models import Customer, Order, Product
from tests.support import REFUSED, request_from


class CustomerView(ModelView[Customer]):
    fields = ["name", "email", "region", "is_active"]
    can_import = True


class OrderView(ModelView[Order]):
    fields = ["customer", "status", "total", "note", "created_at"]
    inlines = (Inline("items", fields=("product", "quantity", "unit_price")),)
    can_import = True


class ProductView(ModelView[Product]):
    fields = ["name", "price", "description"]


ORDER_FORM = {
    "customer": "1",
    "status": "SHIPPED",
    "total": "118.00",
    "note": "",
    "created_at": "2026-09-01T10:30",
}


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    admin = Admin(database, views=[CustomerView, OrderView, ProductView], api=True)
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def loaded(database: Database, view: ModelView[Any], key: int) -> Any:
    """A record as its edit form reads it, its inline rows included."""
    async with database.session() as session:
        record = await view._reader.fetch_record(
            session,
            key,
            paths=view._pages.load_paths(request_from()),
            request=request_from(),
        )
    assert record is not None
    return record


async def region_of(database: Database, email: str) -> str | None:
    async with database.session() as session:
        region: str | None = await session.scalar(
            select(Customer.region).where(Customer.email == email)
        )
        return region


def control(page: str, name: str) -> str:
    """The input a form draws for a field, with a select's options."""
    select = re.search(rf'<select[^>]*name="{name}".*?</select>', page, re.DOTALL)
    if select is not None:
        return select.group(0)
    found = re.search(rf'<(input|textarea)[^>]*name="{name}"[^>]*>', page)
    assert found is not None
    return found.group(0)


def label(page: str, name: str) -> str:
    """What the legend above a field says."""
    found = re.search(rf'id="field-{name}-label">(.*?)</legend>', page, re.DOTALL)
    assert found is not None
    return found.group(1)


class TestTheForms:
    async def test_text_left_empty_on_a_record_saves_as_empty_text(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        saved = await client.post(
            "/admin/customers/1/edit",
            data={"name": "Lena Weber", "email": "lena@fischer.de", "region": ""},
        )

        assert saved.status_code == 303
        async with database.session() as session:
            customer = await session.get(Customer, 1)
            assert customer is not None
            assert (customer.name, customer.region) == ("Lena Weber", "")

    async def test_a_new_record_left_empty_gets_the_default(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        created = await client.post(
            "/admin/customers/new",
            data={"name": "Mia Jansen", "email": "mia@jansen.nl", "region": ""},
        )

        assert created.status_code == 303
        assert await region_of(database, "mia@jansen.nl") == "EU"

    async def test_text_that_may_be_null_left_empty_is_none(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            product = await session.get(Product, 1)
            assert product is not None
            product.description = "Soft and light"
            await session.commit()

        saved = await client.post(
            "/admin/products/1/edit",
            data={"name": "Linen shirt", "price": "59.00", "description": ""},
        )

        assert saved.status_code == 303
        async with database.session() as session:
            product = await session.get(Product, 1)
            assert product is not None
            assert product.description is None

    async def test_any_other_field_left_empty_is_refused_under_it(
        self, client: httpx.AsyncClient
    ) -> None:
        refused = await client.post(
            "/admin/orders/1/edit", data={**ORDER_FORM, "total": ""}
        )

        assert refused.status_code == 422
        note = re.search(r'id="field-total-note">(.*?)</p>', refused.text, re.DOTALL)
        assert note is not None
        assert "This field is required." in note.group(1)
        assert REFUSED not in refused.text


class TestWhatTheEditFormDraws:
    async def test_a_field_the_record_needs_is_drawn_required(
        self, client: httpx.AsyncClient
    ) -> None:
        edit = (await client.get("/admin/orders/1/edit")).text
        new = (await client.get("/admin/orders/new")).text

        assert re.search(r"\brequired\b", control(edit, "total"))
        assert "required" in label(edit, "total")
        assert '<option value="">' not in control(edit, "status")
        assert not re.search(r"\brequired\b", control(new, "total"))
        assert '<option value="">' in control(new, "status")

    async def test_text_and_a_checkbox_are_not(self, client: httpx.AsyncClient) -> None:
        edit = (await client.get("/admin/customers/1/edit")).text

        assert not re.search(r"\brequired\b", control(edit, "region"))
        assert "required" not in label(edit, "region")
        assert "required" not in label(edit, "is_active")


class TestARecordThatExists:
    async def test_a_choice_left_unset_is_refused(self, database: Database) -> None:
        view = OrderView()
        order = await loaded(database, view, 1)

        result = view._forms.parse(
            {**ORDER_FORM, "status": ""}, record=order, request=request_from()
        )

        assert result.errors == {"status": "This field is required."}

    async def test_a_field_the_view_lets_be_empty_is_left_to_the_hooks(
        self, database: Database
    ) -> None:
        class FilledOrders(ModelView[Order]):
            fields = ["customer", Field("total", required=False)]

        view = FilledOrders()
        order = await loaded(database, view, 1)

        result = view._forms.parse(
            {"customer": "1", "total": ""}, record=order, request=request_from()
        )

        assert result.ok
        assert result.values["total"] is None

    async def test_its_inline_rows_are_held_to_it_too(self, database: Database) -> None:
        view = OrderView()
        order = await loaded(database, view, 1)
        first = order.items[0]

        result = view._forms.parse(
            {
                **ORDER_FORM,
                "items-count": "1",
                "items-0-key": str(first.id),
                "items-0-product": str(first.product_id),
                "items-0-quantity": "",
                "items-0-unit_price": "59.00",
            },
            record=order,
            request=request_from(),
        )

        assert result.errors == {"items-0-quantity": "This field is required."}

    async def test_a_file_removed_leaves_empty_text(
        self, database: Database, tmp_path: Path
    ) -> None:
        class BadgeView(ModelView[Customer]):
            fields = [
                "name",
                "email",
                FileField("region", storage=LocalStorage(tmp_path)),
            ]

        view = BadgeView()
        customer = await loaded(database, view, 1)

        result = view._forms.parse(
            {"name": "Lena", "email": "lena@fischer.de", "region-remove": "on"},
            record=customer,
            request=request_from(),
        )

        assert result.values["region"] == ""

    async def test_the_api_holds_it_to_the_same(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        cleared = await client.patch("/admin/-/api/customers/1", json={"region": ""})
        refused = await client.patch("/admin/-/api/orders/1", json={"total": None})

        assert cleared.status_code == 200
        assert await region_of(database, "lena@fischer.de") == ""
        assert refused.status_code == 422
        assert refused.json()["errors"] == {"total": "This field is required."}

    async def test_an_import_holds_it_to_the_same(self, database: Database) -> None:
        async with database.session() as session:
            customers = await build_plan(
                CustomerView(),
                session,
                read_table("a.csv", b"id,region\n1,\n"),
                request=request_from(),
            )
            orders = await build_plan(
                OrderView(),
                session,
                read_table("a.csv", b"id,total\n1,\n"),
                request=request_from(),
            )

        assert customers.rows[0].values == {"region": ""}
        assert orders.rows[0].errors == {"total": "This field is required."}
