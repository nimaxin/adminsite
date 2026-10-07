import hashlib

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite._http.urls import STATIC_DIR
from adminsite.database import Database
from tests.models import Customer, Order, Product
from tests.support import counted


class OrderView(ModelView[Order]):
    group = "Sales"
    fields = ["id", "customer.name", "status", "total", "created_at"]
    fields_default_sort = ("-created_at",)
    page_size = 3
    record_title = "Order {id}"


class CustomerView(ModelView[Customer]):
    group = "Sales"
    fields = ["name", "email", "region"]


class ProductView(ModelView[Product]):
    can_create = False


@pytest.fixture
def admin(database: Database) -> Admin:
    site = Admin(database, title="Acme admin")
    site.add_view(OrderView)
    site.add_view(CustomerView)
    site.add_view(ProductView)
    return site


@pytest.fixture
def client(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestMounting:
    async def test_the_front_page_lists_the_models(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/")

        assert response.status_code == 200
        assert "Orders" in response.text
        assert "Customers" in response.text
        assert "Acme admin" in response.text

    async def test_the_front_page_counts_the_records(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/")

        assert "7" in response.text

    async def test_it_mounts_into_fastapi_too(self, admin: Admin) -> None:
        app = FastAPI()
        app.mount("/backoffice", admin)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/backoffice/")

            assert response.status_code == 200
            assert "/backoffice/orders" in response.text

    async def test_links_follow_where_it_is_mounted(self, admin: Admin) -> None:
        app = Starlette()
        app.mount("/tools/admin", admin)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/tools/admin/")

            assert "/tools/admin/static/adminsite.css" in response.text
            assert "/tools/admin/orders" in response.text

    async def test_the_stylesheet_is_served(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/static/adminsite.css")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")
        assert ".btn" in response.text

    async def test_its_address_changes_with_it(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/")
        contents = (STATIC_DIR / "adminsite.css").read_bytes()
        stamp = hashlib.sha256(contents).hexdigest()[:10]

        # A browser that kept the stylesheet from before an upgrade sees a new
        # address, and fetches the new one at once.
        assert f'href="/admin/static/adminsite.css?v={stamp}"' in page.text
        served = await client.get(f"/admin/static/adminsite.css?v={stamp}")
        assert served.content == contents


class TestListPage:
    async def test_it_shows_the_records(self, client: httpx.AsyncClient) -> None:
        names = ["Lena Fischer", "Marco Rossi", "Aisha Khan", "Jonas Berg"]

        response = await client.get("/admin/orders")

        assert response.status_code == 200
        assert any(name in response.text for name in names)
        assert "Created at" in response.text

    async def test_money_and_dates_are_formatted(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")

        assert "Sep " in response.text
        assert "." in response.text

    async def test_it_shows_one_page_at_a_time(self, client: httpx.AsyncClient) -> None:
        assert "1 to 3 of 7" in await counted(client, "/admin/orders")

    async def test_the_next_page_follows_on(self, client: httpx.AsyncClient) -> None:
        assert "4 to 6 of 7" in await counted(client, "/admin/orders?page=2")

    async def test_a_silly_page_number_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?page=nonsense")

        assert response.status_code == 200
        assert "1 to 3 of 7" in await counted(client, "/admin/orders?page=nonsense")

    async def test_a_view_that_cannot_create_hides_the_button(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products")

        assert "New product" not in response.text

    async def test_an_unknown_model_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/ghosts")

        assert response.status_code == 404

    async def test_the_page_escapes_what_it_shows(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            await session.add(
                Customer(name="<script>alert(1)</script>", email="x@example.com")
            )
            await session.commit()

        response = await client.get("/admin/customers")

        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;" in response.text
