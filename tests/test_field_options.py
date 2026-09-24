from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import RelationField, StringField
from tests.models import Customer, Order, Product


class ProductView(ModelView, model=Product):
    """Only the options change; the fields stay the ones that were worked out."""

    list_display = ("id", "name", "price")
    form_fields = ("name", "price", "description")
    fields = (
        FieldOptions("name", label="Product name", max_length=10),
        FieldOptions("description", help_text="Shown on the shop page."),
        FieldOptions("price", readonly=True),
    )


class OrderView(ModelView, model=Order):
    """A path through a link keeps the name it was given."""

    list_display = ("id", "customer.name")
    form_fields = ("customer", "status")
    fields = (
        FieldOptions("customer.name", label="Bought by"),
        FieldOptions("customer", display_template="{name} <{email}>"),
    )


class StatedView(ModelView, model=Product):
    """A field given in full wins over options for the same name."""

    name = "stated_products"
    form_fields = ("name",)
    fields = (
        StringField("name", label="Given in full"),
        FieldOptions("name", label="Passed over"),
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[ProductView, OrderView, StatedView],
        secret_key="for-the-session",
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    return page.text.split('name="_csrf" value="', 1)[1].split('"', 1)[0]


class TestWhatTheyChange:
    def test_a_label_without_restating_the_field(self) -> None:
        view = ProductView()

        assert view.label_for("name") == "Product name"
        assert isinstance(view.field_for("name"), StringField)

    def test_a_line_of_help(self) -> None:
        assert ProductView().field_for("description").help_text == (
            "Shown on the shop page."
        )

    def test_a_length_the_column_does_not_set(self) -> None:
        assert ProductView().field_for("name").max_length == 10

    def test_what_a_link_takes(self) -> None:
        item = OrderView().field_for("customer")

        assert isinstance(item, RelationField)
        assert item.target is Customer
        assert item.label_for(Customer(name="Lena", email="lena@fischer.de")) == (
            "Lena <lena@fischer.de>"
        )

    def test_a_named_path_through_a_link_is_left_alone(self) -> None:
        assert OrderView().label_for("customer.name") == "Bought by"

    def test_a_path_nobody_named_still_names_the_link(self) -> None:
        class Plain(ModelView, model=Order):
            name = "plain_orders"
            list_display = ("id", "customer.name")

        assert Plain().label_for("customer.name") == "Customer name"

    def test_a_field_given_in_full_wins(self) -> None:
        assert StatedView().label_for("name") == "Given in full"


class TestOnThePage:
    async def test_the_form_shows_them(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/products/1/edit")

        assert "Product name" in form.text
        assert "Shown on the shop page." in form.text

    async def test_the_list_heading_follows(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products")

        assert "Product name" in page.text

    async def test_a_readonly_option_is_not_read_back(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/products/1/edit")

        answer = await client.post(
            "/admin/products/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "Linen",
                "price": "1.00",
                "description": "Still here.",
            },
        )

        assert answer.status_code == 303
        async with database.session() as session:
            shirt = await session.get(Product, 1)
            assert shirt is not None
            assert str(shirt.price) == "59.00"

    async def test_a_length_is_enforced(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/products/1/edit")

        answer = await client.post(
            "/admin/products/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "A name that is far too long",
                "description": "Still here.",
            },
        )

        assert answer.status_code == 422
        assert "10 characters or fewer" in answer.text


class TestAnOptionThatDoesNotExist:
    def test_it_says_which_view_and_which_path(self) -> None:
        class Wrong(ModelView, model=Product):
            name = "wrong_products"
            fields = (FieldOptions("name", colour="red"),)

        with pytest.raises(AdminSiteError) as raised:
            Wrong().field_for("name")

        assert "FieldOptions('name') in Wrong.fields" in str(raised.value)


class TestAFormat:
    def test_it_writes_the_value_wherever_it_is_shown(self) -> None:
        class Priced(ModelView, model=Product):
            name = "priced_products"
            fields = (FieldOptions("price", format="€{:,.2f}"),)

        item = Priced().field_for("price")

        assert item.text_for(None, Decimal("1234.5")) == "€1,234.50"
        assert item.text_for(None, None) == ""
        # The input keeps the plain number, which is what it reads back.
        assert item.serialize(Decimal("1234.50")) == "1234.50"

    async def test_the_list_and_the_form_follow_it(self, database: Database) -> None:
        class Priced(ModelView, model=Product):
            name = "priced_products"
            list_display = ("name", "price")
            form_fields = ("name", "price")
            fields = (FieldOptions("price", format="€{:,.2f}"),)

        admin = Admin(database, views=[Priced])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            listed = await client.get("/admin/priced_products")
            form = await client.get("/admin/priced_products/1/edit")

        assert "€59.00" in listed.text
        assert 'value="59.00"' in form.text
