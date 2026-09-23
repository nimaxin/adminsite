import re
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RefusedError
from adminsite.views.writing import SaveContext
from tests.models import Customer, Product


class ProductView(ModelView, model=Product):
    """Names are slugged on the way in, and a price has to make sense."""

    form_fields = ("name", "price", "description")

    async def before_save(self, context: SaveContext) -> None:
        name = context.values.get("name")
        if name:
            context.set("description", name.strip().lower().replace(" ", "-"))
        price = context.values.get("price")
        if price is not None and price < 0:
            raise RefusedError("A price cannot be below zero.", field="price")


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "region", "is_active")

    async def before_save(self, context: SaveContext) -> None:
        if context.values.get("region") == "XX":
            raise RefusedError("That region is closed.")


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[ProductView, CustomerView],
        secret_key="for-the-session",
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


class TestChangingAValue:
    async def test_a_hook_writes_what_is_stored(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/products/new")
        answer = await client.post(
            "/admin/products/new",
            data={
                "_csrf": token_in(form),
                "name": "Woolly Hat",
                "price": "12.00",
                "description": "anything",
            },
        )

        assert answer.status_code == 303
        async with database.session() as session:
            hat = await session.scalar(
                select(Product).where(Product.name == "Woolly Hat")
            )
            assert hat is not None
            assert hat.description == "woolly-hat"

    async def test_it_works_on_a_change_too(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        form = await client.get("/admin/products/1/edit")
        await client.post(
            "/admin/products/1/edit",
            data={
                "_csrf": token_in(form),
                "name": "Linen Shirt",
                "price": "59.00",
                "description": "",
            },
        )

        async with database.session() as session:
            shirt = await session.get(Product, 1)
            assert shirt is not None
            assert shirt.description == "linen-shirt"

    async def test_the_caller_s_values_are_left_alone(self, database: Database) -> None:
        view = ProductView()
        values = {"name": "Cap", "price": 5, "description": "keep me"}
        async with database.session() as session:
            await view.save(session, values)

        assert values["description"] == "keep me"


class TestRefusingOneField:
    async def test_the_message_sits_by_the_field(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/products/new")
        answer = await client.post(
            "/admin/products/new",
            data={
                "_csrf": token_in(form),
                "name": "Cheap",
                "price": "-1.00",
                "description": "",
            },
        )

        assert answer.status_code == 422
        assert 'id="field-price-note"' in answer.text
        assert "A price cannot be below zero." in answer.text
        # Beside the field, not above the form as well.
        assert answer.text.count("A price cannot be below zero.") == 1
        assert 'aria-describedby="field-price-note"' in answer.text

    async def test_what_was_typed_is_still_there(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/products/new")
        answer = await client.post(
            "/admin/products/new",
            data={
                "_csrf": token_in(form),
                "name": "Cheap",
                "price": "-1.00",
                "description": "",
            },
        )

        assert 'value="Cheap"' in answer.text

    async def test_a_refusal_about_no_field_stays_above_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/new")
        answer = await client.post(
            "/admin/customers/new",
            data={
                "_csrf": token_in(form),
                "name": "Nobody",
                "email": "nobody@x.io",
                "region": "XX",
            },
        )

        assert answer.status_code == 422
        assert 'role="alert"' in answer.text
        assert "That region is closed." in answer.text

    async def test_the_api_answers_with_the_field(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/products")
        answer = await client.post(
            "/admin/-/api/products",
            json={"name": "Cheap", "price": "-1.00"},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 422
        assert answer.json()["errors"] == {"price": "A price cannot be below zero."}

    async def test_the_api_keeps_409_for_the_rest(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers")
        answer = await client.post(
            "/admin/-/api/customers",
            json={"name": "Nobody", "email": "nobody@x.io", "region": "XX"},
            headers={"X-CSRF-Token": token_in(page)},
        )

        assert answer.status_code == 409
        assert answer.json() == {"error": "That region is closed."}
