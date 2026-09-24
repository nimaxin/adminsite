import httpx
from starlette.applications import Starlette

from adminsite import Admin, Html, ModelView
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from tests.models import Product


class ProductView(ModelView, model=Product):
    list_display = ("id", "name")


def client_for(site: Admin) -> httpx.AsyncClient:
    site.add_view(ProductView)
    app = Starlette()
    app.mount("/admin", site)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestBanner:
    async def test_every_page_shows_it(self, database: Database) -> None:
        client = client_for(Admin(database, banner="Staging: nothing here is real."))

        for path in ("/admin/", "/admin/products", "/admin/products/1"):
            response = await client.get(path)

            assert 'role="note"' in response.text
            assert "Staging: nothing here is real." in response.text

    async def test_the_sign_in_page_shows_it(self, database: Database) -> None:
        site = Admin(
            database,
            banner="Sign in as admin.",
            auth=PasswordAuth({"admin": hash_password("admin")}),
            secret_key="a-secret-for-the-tests",
        )

        response = await client_for(site).get("/admin/login")

        assert "Sign in as admin." in response.text

    async def test_there_is_none_unless_asked_for(self, database: Database) -> None:
        response = await client_for(Admin(database)).get("/admin/products")

        assert 'role="note"' not in response.text

    async def test_its_text_is_escaped(self, database: Database) -> None:
        client = client_for(Admin(database, banner="<b>Staging</b>"))

        response = await client.get("/admin/products")

        assert "&lt;b&gt;Staging&lt;/b&gt;" in response.text

    async def test_html_keeps_a_link(self, database: Database) -> None:
        banner = Html('Read <a href="https://example.com/notes">the notes</a>.')
        client = client_for(Admin(database, banner=banner))

        response = await client.get("/admin/products")

        assert '<a href="https://example.com/notes">the notes</a>' in response.text
