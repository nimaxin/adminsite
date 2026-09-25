import re
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import BooleanField, ChoiceField
from adminsite.fields.tones import NEUTRAL, TONE_NAMES
from tests.models import Customer, Order, OrderStatus

ROSE = TONE_NAMES.index("rose")
GREEN = TONE_NAMES.index("green")


class OrderView(ModelView, model=Order):
    list_display = ("id", "status")
    ordering = ("id",)
    fields = (
        FieldOptions(
            "status",
            tones={OrderStatus.PENDING: "green", OrderStatus.REFUNDED: "rose"},
        ),
    )


class PlainOrderView(ModelView, model=Order):
    name = "plain_orders"
    list_display = ("id", "status")
    fields = (FieldOptions("status", tones="grey"),)


class CustomerView(ModelView, model=Customer):
    list_display = ("name", "is_active")
    fields = (FieldOptions("is_active", tones={True: "rose", False: None}),)


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    async with database.session() as session:
        marco = await session.get(Customer, 2)
        assert marco is not None
        marco.is_active = False
        await session.commit()
    admin = Admin(database, views=[OrderView, PlainOrderView, CustomerView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def pill(tone: int, text: str) -> str:
    return f'<span class="pill tone-{tone}">{text}</span>'


def row_of(page: httpx.Response, text: str) -> str:
    found = re.search(
        rf"<tr\b(?:(?!</tr>).)*{text}(?:(?!</tr>).)*</tr>", page.text, re.S
    )
    assert found is not None, text
    return found.group(0)


class TestAStatusSaysItsColours:
    async def test_the_list_draws_each_value_in_its_tone(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        # Pending comes first among the choices, refunded last: their places
        # would make them amber and grey.
        assert pill(GREEN, "Pending") in page.text
        assert pill(ROSE, "Refunded") in page.text
        assert pill(NEUTRAL, "Paid") in page.text

    async def test_the_record_page_draws_it_the_same(
        self, client: httpx.AsyncClient
    ) -> None:
        refunded = await client.get("/admin/orders/5")

        assert pill(ROSE, "Refunded") in refunded.text

    async def test_every_value_can_be_neutral(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/plain_orders")

        assert pill(NEUTRAL, "Pending") in page.text
        assert pill(NEUTRAL, "Refunded") in page.text
        assert "tone-0" not in page.text


class TestAFlagWhoseYesIsTheBadCase:
    async def test_it_is_rose_when_set_and_not_drawn_when_not(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers")

        assert pill(ROSE, "Yes") in row_of(page, "Lena Fischer")
        assert "pill" not in row_of(page, "Marco Rossi")

    async def test_the_record_page_leaves_it_out_too(
        self, client: httpx.AsyncClient
    ) -> None:
        marco = await client.get("/admin/customers/2")
        lena = await client.get("/admin/customers/1")

        assert "pill tone" not in marco.text
        assert pill(ROSE, "Yes") in lena.text


class TestTheField:
    def test_a_value_is_found_by_its_member_or_its_stored_value(self) -> None:
        field = ChoiceField(
            "status",
            enum_class=OrderStatus,
            tones={OrderStatus.PAID: "blue", "Refunded": "rose"},
        )

        assert field.tone_of(OrderStatus.PAID) == TONE_NAMES.index("blue")
        assert field.tone_of(OrderStatus.REFUNDED) == ROSE
        assert field.tone_of(OrderStatus.PENDING) == NEUTRAL

    def test_none_draws_no_badge(self) -> None:
        field = ChoiceField(
            "size", choices=(("s", "Small"), ("m", "Medium")), tones={"s": None}
        )

        assert field.tone_of("s") is None
        assert field.tone_of("m") == NEUTRAL

    def test_a_yes_or_no_is_green_and_grey_unless_it_says(self) -> None:
        plain = BooleanField("paid")
        flag = BooleanField("high_risk", tones={True: "rose", False: None})

        assert (plain.tone_of(True), plain.tone_of(False)) == (GREEN, NEUTRAL)
        assert (flag.tone_of(True), flag.tone_of(False)) == (ROSE, None)


class TestAMistake:
    def test_a_tone_that_does_not_exist_names_the_tones(self) -> None:
        # A type checker already refuses "red"; this is the check for code
        # that has none, such as FieldOptions.
        with pytest.raises(AdminSiteError, match="amber, blue, green, grey"):
            ChoiceField("size", choices=(("s", "Small"),), tones={"s": "red"})  # type: ignore[dict-item]

    def test_a_value_that_is_not_a_choice_names_the_choices(self) -> None:
        with pytest.raises(AdminSiteError, match="not one of its choices: s, m"):
            ChoiceField(
                "size", choices=(("s", "Small"), ("m", "Medium")), tones={"xl": "rose"}
            )

    def test_a_yes_or_no_takes_true_and_false(self) -> None:
        with pytest.raises(AdminSiteError, match="takes True and False"):
            BooleanField("paid", tones={"yes": "green"})

    def test_it_stops_the_view_from_being_created(self) -> None:
        class Wrong(ModelView, model=Order):
            name = "wrong_orders"
            fields = (FieldOptions("status", tones={"lost": "rose"}),)

        with pytest.raises(AdminSiteError) as raised:
            Wrong()

        assert "FieldOptions('status') in Wrong.fields" in str(raised.value)
        assert "'lost'" in str(raised.value)
