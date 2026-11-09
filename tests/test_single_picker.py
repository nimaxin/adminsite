"""A choice or a link to one record among many is a picker that narrows as you type."""

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

import httpx
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette

from adminsite import Admin, Inline, ModelView
from adminsite._http.form_rows import SEARCHABLE_OVER, Choice, FormRow
from adminsite.actions import Selection, action
from adminsite.database import Database
from adminsite.fields import EnumField, JSONField, RelationField
from tests.models import Customer, Order, OrderItem, Product, Setting
from tests.test_accessibility import Controls

# Regions enough to need a search, each sample customer's among them.
REGIONS = [
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("DE", "Germany"),
    ("DK", "Denmark"),
    ("ES", "Spain"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("IE", "Ireland"),
    ("IT", "Italy"),
    ("NL", "Netherlands"),
    ("SE", "Sweden"),
    ("UK", "United Kingdom"),
]
FEW = [("DE", "Germany"), ("IT", "Italy"), ("SE", "Sweden"), ("UK", "United Kingdom")]
NOTES = [
    (note, note)
    for note in (
        "Gift",
        "Fragile",
        "Leave at the door",
        "Call first",
        "No invoice",
        "Express",
        "Weekend",
        "Signature",
        "Pick up",
        "Recycled box",
        "Rush",
        "Back order",
    )
]
CARRIERS = [(f"C{number}", f"Carrier {number}") for number in range(12)]


def customers_with(choices: Sequence[tuple[str, str]]) -> type[ModelView[Customer]]:
    class CustomerView(ModelView[Customer]):
        fields = ["name", EnumField("region", choices=choices)]

    return CustomerView


class NoteView(ModelView[Order]):
    """An order's note, one of more than a select should hold."""

    fields = [EnumField("note", choices=NOTES)]


class OrderItemView(ModelView[OrderItem]):
    fields = ["product", "quantity"]


class OrderView(ModelView[Order]):
    fields = ["note"]
    inlines = (Inline("items", fields=("product", "quantity")),)


class ShippingView(ModelView[Order]):
    fields = ["id", "note"]

    @action(
        "Ship",
        inputs=[
            EnumField("carrier", choices=CARRIERS, required=True),
            RelationField("product", target=Product, required=True),
        ],
    )
    async def ship(
        self, selection: Selection[Order], carrier: str, product: Product
    ) -> str:
        return "Shipped."


class Delivery(BaseModel):
    region: Literal[
        "AT", "BE", "DE", "DK", "ES", "FI", "FR", "IE", "IT", "NL", "SE", "UK"
    ] = "DE"


class SettingView(ModelView[Setting]):
    fields = ["name", JSONField("options", schema=Delivery)]


@pytest.fixture
async def more_products(database: Database) -> Database:
    """Products enough that a select of them is long, too few to search."""
    async with database.session() as session:
        for number in range(20):
            await session.add(
                Product(name=f"Spare part {number}", price=Decimal("1.00"))
            )
        await session.commit()
    return database


def serve(database: Database, view: type[ModelView[Any]]) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", Admin(database, views=[view], secret_key="for-the-session"))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def page_of(database: Database, view: type[ModelView[Any]], url: str) -> str:
    async with serve(database, view) as client:
        page = await client.get(url)
    assert page.status_code == 200
    return page.text


def picker_for(page: str, name: str) -> str:
    """The settings of the picker holding a field, written just before its input."""
    before, found, _ = page.partition(
        f'<input type="hidden" name="{name}" :value="item.value">'
    )
    assert found, f"no picker holds {name}"
    return before.rsplit("x-data=", 1)[1]


def options_in(page: str, name: str) -> int:
    """How many options the picker of a field lists in the page."""
    found = re.search(rf'<ul id="options-{name}".*?</ul>', page, re.DOTALL)
    assert found is not None
    return found.group(0).count('role="option"')


def has_select(page: str, name: str) -> bool:
    return re.search(rf'<select [^>]*name="{re.escape(name)}"', page) is not None


def token_in(page: str) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


async def note_of(database: Database, key: int) -> str | None:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None
        return order.note


ONE_IN_THE_PAGE = "'recordPicker({multiple: false, ordered: false, local: true,"


class TestWhenToPick:
    def test_past_the_options_a_select_holds(self) -> None:
        field = EnumField("region", choices=REGIONS)

        def offering(count: int) -> FormRow:
            choices = [Choice(f"V{one}", f"Value {one}") for one in range(count)]
            return FormRow("region", field, choices=choices)

        assert offering(SEARCHABLE_OVER + 1).many_choices is True
        assert offering(SEARCHABLE_OVER).many_choices is False


class TestAChoice:
    async def test_one_among_many_is_a_picker(self, database: Database) -> None:
        page = await page_of(
            database, customers_with(REGIONS), "/admin/customers/1/edit"
        )

        picker = picker_for(page, "region")
        assert picker.startswith(ONE_IN_THE_PAGE)
        assert 'picked: [{"label": "Germany", "value": "DE"}]' in picker
        assert options_in(page, "region") == len(REGIONS)
        assert not has_select(page, "region")

    async def test_one_among_a_few_is_a_select(self, database: Database) -> None:
        page = await page_of(database, customers_with(FEW), "/admin/customers/1/edit")

        assert has_select(page, "region")
        assert "x-data='recordPicker(" not in page

    async def test_its_box_asks_for_one(self, database: Database) -> None:
        page = await page_of(
            database, customers_with(REGIONS), "/admin/customers/1/edit"
        )

        picker = picker_for(page, "region")
        assert 'hint: "Choose region", search: "Search"' in picker
        assert re.search(
            r'<input type="text" id="field-region" x-ref="search" role="combobox"',
            page,
        )

    async def test_every_control_is_named(self, database: Database) -> None:
        controls = Controls()
        controls.feed(
            await page_of(database, customers_with(REGIONS), "/admin/customers/1/edit")
        )

        assert controls.unnamed() == []

    async def test_one_that_may_be_empty_can_be_cleared(
        self, database: Database
    ) -> None:
        page = await page_of(database, NoteView, "/admin/orders/1/edit")

        assert picker_for(page, "note").startswith(ONE_IN_THE_PAGE)
        assert 'aria-label="Clear Note"' in page

    async def test_what_is_picked_is_saved_and_a_cleared_one_is_emptied(
        self, database: Database
    ) -> None:
        async with serve(database, NoteView) as client:
            form = await client.get("/admin/orders/1/edit")
            token = token_in(form.text)
            picked = await client.post(
                "/admin/orders/1/edit", data={"_csrf": token, "note": "Rush"}
            )
            saved = await note_of(database, 1)
            # A picker cleared sends nothing for its field.
            cleared = await client.post("/admin/orders/1/edit", data={"_csrf": token})
            emptied = await note_of(database, 1)

        assert (picked.status_code, saved) == (303, "Rush")
        assert (cleared.status_code, emptied) == (303, None)


class TestALinkToOne:
    async def test_among_many_it_lists_them_in_the_page(
        self, more_products: Database
    ) -> None:
        page = await page_of(more_products, OrderItemView, "/admin/order_items/1/edit")

        picker = picker_for(page, "product")
        assert picker.startswith(ONE_IN_THE_PAGE)
        assert 'picked: [{"label": "Linen shirt", "value": "1"}]' in picker
        assert options_in(page, "product") == 23
        assert "/lookup/product" not in page
        assert not has_select(page, "product")

    async def test_among_a_few_it_is_a_select(self, database: Database) -> None:
        page = await page_of(database, OrderItemView, "/admin/order_items/1/edit")

        assert has_select(page, "product")
        assert "x-data='recordPicker(" not in page


class TestAnInlineRow:
    async def test_a_line_picks_its_product_and_can_clear_it(
        self, more_products: Database
    ) -> None:
        page = await page_of(more_products, OrderView, "/admin/orders/1/edit")

        line = picker_for(page, "items-0-product")
        blank = picker_for(page, "items-__index__-product")
        assert line.startswith(ONE_IN_THE_PAGE)
        assert 'picked: [{"label": "Linen shirt", "value": "1"}]' in line
        assert blank.startswith(ONE_IN_THE_PAGE)
        assert "picked: []" in blank
        # A line may stay unused, so its select offered Not set: the
        # picker can be cleared instead, though the product is required.
        assert 'aria-label="Clear Product"' in page


class TestAnActionDialog:
    async def test_it_picks_a_choice_and_a_record_among_many(
        self, more_products: Database
    ) -> None:
        page = await page_of(more_products, ShippingView, "/admin/orders")
        dialog = re.search(r'<dialog id="action-ship".*?</dialog>', page, re.DOTALL)

        assert dialog is not None
        assert picker_for(dialog.group(0), "carrier").startswith(ONE_IN_THE_PAGE)
        assert picker_for(dialog.group(0), "product").startswith(ONE_IN_THE_PAGE)
        assert 'id="field-ship-carrier"' in dialog.group(0)


class TestADocument:
    async def test_its_choice_among_many_is_a_picker(self, database: Database) -> None:
        page = await page_of(database, SettingView, "/admin/settings/1/edit")

        assert picker_for(page, "options.region").startswith(ONE_IN_THE_PAGE)
        assert not has_select(page, "options.region")
