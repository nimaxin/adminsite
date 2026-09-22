from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.audit import AuditEntry, AuditEvent, AuditLog
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product


class OrderView(ModelView, model=Order):
    list_display = ("id", "status")


class CustomerView(ModelView, model=Customer):
    """Open to everyone, but its history is private."""

    list_display = ("name",)

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == Permission.HISTORY:
            return False
        return await super().allows(action, request=request, record=record)


class ProductView(ModelView, model=Product):
    """Hidden from this user altogether."""

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        return False


def entry(view: str, key: str = "1") -> AuditEntry:
    return AuditEntry(
        view=view,
        record_key=key,
        record_title=f"{view} {key}",
        event=AuditEvent.UPDATED,
    )


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


async def client_for(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, CustomerView, ProductView],
        audit=log,
    )
    async with await client_for(admin) as client:
        yield client


class TestTheActivityPage:
    async def test_it_shows_only_the_history_you_may_read(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record([entry("orders"), entry("customers"), entry("products")])

        page = await client.get("/admin/-/activity")

        assert "orders 1" in page.text
        assert "customers 1" not in page.text
        assert "products 1" not in page.text

    async def test_entries_from_another_admin_stay_out(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record([entry("invoices")])

        page = await client.get("/admin/-/activity")

        assert "invoices 1" not in page.text

    async def test_asking_for_a_hidden_view_shows_what_you_may_read(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record([entry("orders"), entry("customers")])

        page = await client.get("/admin/-/activity?view=customers")

        assert "orders 1" in page.text
        assert "customers 1" not in page.text

    async def test_the_filter_offers_only_readable_views(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/-/activity")

        assert "/admin/-/activity?view=orders" in page.text
        assert "/admin/-/activity?view=customers" not in page.text

    async def test_the_limit_counts_only_readable_entries(self, log: AuditLog) -> None:
        await log.record(
            [entry("orders", "1")] + [entry("customers", str(n)) for n in range(5)]
        )

        found = await log.recent(views=["orders"], limit=1)

        assert [item.record_key for item in found] == ["1"]

    async def test_with_no_readable_history_the_page_is_refused(
        self, database: Database, log: AuditLog
    ) -> None:
        admin = Admin(database, title="Shop", views=[CustomerView], audit=log)
        async with await client_for(admin) as client:
            home = await client.get("/admin/")
            page = await client.get("/admin/-/activity")

        assert "/admin/-/activity" not in home.text
        assert page.status_code == 403
        assert "Not allowed" in page.text


class TestHistoryOnARecord:
    async def test_the_tab_follows_the_permission(
        self, client: httpx.AsyncClient
    ) -> None:
        order = await client.get("/admin/orders/1")
        customer = await client.get("/admin/customers/1")

        assert "History (" in order.text
        assert "History (" not in customer.text


class TestTheSidebar:
    async def test_views_you_may_not_open_are_left_out(
        self, client: httpx.AsyncClient
    ) -> None:
        home = await client.get("/admin/")

        assert 'href="/admin/orders"' in home.text
        assert 'href="/admin/products"' not in home.text


class TestErrorPages:
    async def test_a_refusal_is_a_403_page(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products")

        assert page.status_code == 403
        assert "Not allowed" in page.text
        assert "You cannot view Products." in page.text
        assert "Back to the overview" in page.text

    async def test_a_missing_record_is_a_404_page(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/999")

        assert page.status_code == 404
        assert "Not found" in page.text

    async def test_an_unknown_view_is_a_404_page(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/nothing-here")

        assert page.status_code == 404
        assert "No page at &#39;nothing-here&#39;." in page.text

    async def test_htmx_gets_plain_text(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/products", headers={"hx-request": "true"})

        assert page.status_code == 403
        assert page.text == "You cannot view Products."
