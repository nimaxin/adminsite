import re

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "status")


class CustomerView(ModelView, model=Customer):
    list_display = ("name", "email")


class ProductView(ModelView, model=Product):
    list_display = ("name", "price")


@pytest.fixture
def app(database: Database) -> Starlette:
    staff = Admin(
        database,
        title="Staff",
        views=[OrderView, CustomerView, ProductView],
        auth=PasswordAuth({"manager": hash_password("staff-pass")}),
        secret_key="staff-secret",
        session_cookie="staff_session",
    )
    support = Admin(
        database,
        title="Support",
        views=[OrderView, CustomerView],
        auth=PasswordAuth({"agent": hash_password("support-pass")}),
        secret_key="support-secret",
        session_cookie="support_session",
    )
    application = Starlette()
    application.mount("/staff", staff)
    application.mount("/support", support)
    return application


@pytest.fixture
def client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def sign_in(
    client: httpx.AsyncClient, site: str, username: str, password: str
) -> None:
    page = await client.get(f"/{site}/login")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    await client.post(
        f"/{site}/login",
        data={"username": username, "password": password, "_csrf": token.group(1)},
    )


class TestTwoAdmins:
    async def test_each_site_shows_only_its_own_views(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client, "staff", "manager", "staff-pass")
        await sign_in(client, "support", "agent", "support-pass")

        staff = await client.get("/staff/")
        support = await client.get("/support/")

        assert "/staff/products" in staff.text
        assert "/support/products" not in support.text
        assert "/support/orders" in support.text

    async def test_a_model_left_out_of_a_site_is_not_there(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client, "support", "agent", "support-pass")

        response = await client.get("/support/products")

        assert response.status_code == 404

    async def test_links_and_files_follow_each_mount(
        self, client: httpx.AsyncClient
    ) -> None:
        staff = await client.get("/staff/login")
        support = await client.get("/support/login")

        assert "/staff/static/adminsite.css" in staff.text
        assert "/support/static/adminsite.css" in support.text

    async def test_signing_in_to_one_does_not_open_the_other(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client, "staff", "manager", "staff-pass")

        staff = await client.get("/staff/orders")
        support = await client.get("/support/orders")

        assert staff.status_code == 200
        assert support.status_code == 303
        assert support.headers["location"] == "/support/login"

    async def test_each_site_checks_its_own_users(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client, "support", "manager", "staff-pass")

        response = await client.get("/support/orders")

        assert response.status_code == 303

    async def test_both_sessions_live_side_by_side(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client, "staff", "manager", "staff-pass")
        await sign_in(client, "support", "agent", "support-pass")

        staff = await client.get("/staff/orders")
        support = await client.get("/support/orders")

        assert staff.status_code == 200
        assert support.status_code == 200
        assert "manager" in staff.text
        assert "agent" in support.text

    async def test_the_same_view_class_is_separate_in_each_site(
        self, database: Database
    ) -> None:
        one = Admin(database, views=[OrderView])
        two = Admin(database, views=[OrderView])

        assert one.views.get("orders") is not two.views.get("orders")


class TestDefaultCookies:
    def test_each_admin_gets_its_own_cookie_from_its_title(
        self, database: Database
    ) -> None:
        staff = Admin(database, title="Staff tools")
        support = Admin(database, title="Support")

        assert staff.session_cookie == "adminsite_staff_tools"
        assert support.session_cookie == "adminsite_support"

    async def test_two_admins_keep_apart_without_naming_cookies(
        self, database: Database
    ) -> None:
        application = Starlette()
        application.mount(
            "/staff",
            Admin(
                database,
                title="Staff",
                views=[OrderView],
                auth=PasswordAuth({"manager": hash_password("staff-pass")}),
                secret_key="staff-secret",
            ),
        )
        application.mount(
            "/support",
            Admin(
                database,
                title="Support",
                views=[OrderView],
                auth=PasswordAuth({"agent": hash_password("support-pass")}),
                secret_key="support-secret",
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            await sign_in(client, "staff", "manager", "staff-pass")
            await sign_in(client, "support", "agent", "support-pass")

            assert (await client.get("/staff/orders")).status_code == 200
            assert (await client.get("/support/orders")).status_code == 200
