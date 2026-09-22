import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission, RecentRecords
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product

ICON = '<svg viewBox="0 0 16 16"><path d="M2 2h12v12H2z"/></svg>'


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    search_fields = ("customer.name",)
    icon = ICON


class QuietView(ModelView, model=Customer):
    """A list that says everything, so a record page would only repeat it."""

    name = "customers"
    list_display = ("name", "email")
    search_fields = ("name",)
    can_detail = False
    can_export = False
    icon = "icons/customers.svg"


class ManagerOnlyExport(ModelView, model=Product):
    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == Permission.EXPORT:
            return False
        return await super().allows(action, request=request, record=record)


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, QuietView, ManagerOnlyExport],
        secret_key="for-the-session",
        dashboard=[RecentRecords("Latest customers", "customers")],
    )
    async with serve(admin) as client:
        yield client


class TestExport:
    async def test_a_view_can_export_nothing(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers")
        exported = await client.get("/admin/customers/export")

        assert "/admin/customers/export" not in page.text
        assert exported.status_code == 403

    async def test_the_button_follows_the_permission(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/products")
        exported = await client.get("/admin/products/export")

        assert "Export CSV" not in page.text
        assert exported.status_code == 403

    async def test_a_view_that_exports_keeps_its_button(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert "/admin/orders/export" in page.text


class TestTheDetailPage:
    async def test_a_view_can_have_none(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers/1")

        assert page.status_code == 403

    async def test_rows_open_the_form_instead(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers")

        assert 'href="/admin/customers/1/edit"' in page.text
        assert 'href="/admin/customers/1"' not in page.text

    async def test_saving_lands_on_the_list(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/customers/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        answer = await client.post(
            "/admin/customers/1/edit",
            data={
                "_csrf": token.group(1),
                "name": "Lena F.",
                "email": "lena@fischer.de",
                "region": "DE",
                "is_active": "true",
            },
        )

        assert answer.headers["location"] == "/admin/customers"

    async def test_the_palette_opens_the_form_instead(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search?q=lena")

        assert 'href="/admin/customers/1/edit"' in found.text

    async def test_a_dashboard_card_links_where_it_can(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/")

        assert 'href="/admin/customers/1/edit"' in page.text

    async def test_a_view_with_a_detail_page_keeps_it(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")
        page = await client.get("/admin/orders/1")

        assert 'href="/admin/orders/1"' in listed.text
        assert page.status_code == 200


class TestIcons:
    async def test_inline_svg_is_drawn(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert ICON in page.text

    async def test_a_picture_is_linked(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert '<img src="/admin/icons/customers.svg"' in page.text

    async def test_a_view_without_an_icon_draws_none(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert page.text.count("<img src=") == 1


class TestFacetCounts:
    async def test_a_filter_through_a_relationship_shows_its_options(
        self, database: Database
    ) -> None:
        class Orders(ModelView, model=Order):
            name = "by_region"
            list_filter = ("customer.region",)

        admin = Admin(database, views=[Orders])
        async with serve(admin) as client:
            page = await client.get("/admin/by_region")

        assert page.status_code == 200
        assert 'name="customer__region"' in page.text


class TestTheSessionCookie:
    def test_it_is_open_over_plain_http_by_default(self, database: Database) -> None:
        admin = Admin(database, secret_key="for-the-session")

        middleware = admin.middleware()[0]

        assert middleware.kwargs["https_only"] is False
        assert middleware.kwargs["max_age"] == 14 * 24 * 3600

    async def test_it_can_be_locked_down(self, database: Database) -> None:
        admin = Admin(
            database,
            views=[OrderView],
            secret_key="for-the-session",
            session_https_only=True,
            session_max_age=3600,
        )
        async with serve(admin) as client:
            answer = await client.get("https://testserver/admin/orders")

        cookie = answer.headers.get("set-cookie", "").lower()
        assert "secure" in cookie
        assert "max-age=3600" in cookie
