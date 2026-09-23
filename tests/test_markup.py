from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Computed, Html, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Product


def shop_link(product: Product) -> Html:
    """A cell that links somewhere else, with the name escaped into it."""
    return Html('<a class="link" href="/shop/{}">{}</a>').format(
        product.id, product.name
    )


class ProductView(ModelView, model=Product):
    list_display = ("id", "name", "shop", "warning")
    detail_fields = ("name", "shop")
    fields = (
        Computed("shop", shop_link, needs=("id", "name")),
        # Plain text, so the page shows the tags rather than obeying them.
        Computed("warning", lambda product: "<b>handle with care</b>"),
    )


class CustomerView(ModelView, model=Customer):
    list_display = ("id", "name", "mail")
    fields = (
        Computed(
            "mail",
            lambda customer: Html('<a href="mailto:{}">Write</a>').format(
                customer.email
            ),
            needs=("email",),
        ),
    )


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


class TestInACell:
    async def test_markup_goes_into_the_page(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products")

        assert '<a class="link" href="/shop/1">Linen shirt</a>' in page.text

    async def test_plain_text_is_still_escaped(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products")

        assert "&lt;b&gt;handle with care&lt;/b&gt;" in page.text
        assert "<b>handle with care</b>" not in page.text

    async def test_a_value_written_in_is_escaped(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            shirt = await session.get(Product, 1)
            assert shirt is not None
            shirt.name = "<script>alert(1)</script>"
            await session.commit()

        page = await client.get("/admin/products")

        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text

    async def test_the_record_page_shows_it_too(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/products/1")

        assert '<a class="link" href="/shop/1">Linen shirt</a>' in page.text


class TestWhereMarkupDoesNotBelong:
    async def test_the_export_writes_the_text(self, client: httpx.AsyncClient) -> None:
        answer = await client.get("/admin/products/export")

        assert "Linen shirt,Linen shirt,<b>handle with care</b>" in answer.text
        assert "<a " not in answer.text

    async def test_the_api_sends_the_text(self, client: httpx.AsyncClient) -> None:
        answer = await client.get("/admin/-/api/customers/1")

        assert answer.json()["mail"] == "Write"
