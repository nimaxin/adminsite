import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, Inline, ModelView
from adminsite.audit import AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order, Product


class CustomerView(ModelView, model=Customer):
    display_template = "{name} ({email})"


class ProductView(ModelView, model=Product):
    display_template = "{name} at {price}"


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer", "items")
    form_fields = ("customer", "status")
    detail_fields = ("customer", "items", "status")
    inlines = (Inline("items", fields=("product", "quantity")),)


class EmailedOrderView(ModelView, model=Order):
    name = "emailed_orders"
    list_display = ("id", "customer")
    form_fields = ("customer", "status")
    fields = (FieldOptions("customer", display_template="{email}"),)


LENA = "Lena Fischer (lena@fischer.de)"


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, EmailedOrderView, CustomerView],
        audit=log,
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestNamedByTheViewThatShowsIt:
    async def test_a_list_cell(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert LENA in page.text

    async def test_the_record_page_and_the_export(
        self, client: httpx.AsyncClient
    ) -> None:
        record = await client.get("/admin/orders/1")
        export = await client.get("/admin/orders/export")

        assert LENA in record.text
        assert LENA in export.text

    async def test_the_picker_names_it_the_same_way(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        chosen = rf'<option value="1"\s+selected>\s*{re.escape(LENA)}'
        assert re.search(chosen, form.text)

    async def test_the_log_names_it_the_same_way(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        form = await client.get("/admin/orders/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        await client.post(
            "/admin/orders/1/edit",
            data={
                "_csrf": token.group(1),
                "customer": "2",
                "status": "shipped",
                "items-count": "0",
            },
        )

        entry = (await log.find(AuditQuery(), limit=1))[0]
        assert entry.changes["customer"] == (LENA, "Marco Rossi (marco@rossi.it)")


class TestWithoutAViewOrAName:
    async def test_a_link_is_named_by_its_model_and_key(
        self, client: httpx.AsyncClient
    ) -> None:
        # Order items have no view and no __str__ of their own.
        page = await client.get("/admin/orders")
        record = await client.get("/admin/orders/1")

        assert "Order item #1, Order item #2" in page.text
        assert "Order item #1, Order item #2" in record.text
        assert "object at 0x" not in page.text + record.text


class TestALinksOwnTemplate:
    async def test_it_wins_in_the_list_and_in_the_picker(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/emailed_orders")
        form = await client.get("/admin/emailed_orders/1/edit")

        assert "lena@fischer.de" in page.text
        assert LENA not in page.text
        assert re.search(r'<option value="1"\s+selected>\s*lena@fischer.de', form.text)


class TestAnInlinesRows:
    async def test_they_name_their_links_as_the_admins_views_do(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[OrderView, ProductView, CustomerView])
        async with serve(admin) as client:
            page = await client.get("/admin/orders/1")

        assert "Linen shirt at 59.00" in page.text
        assert "Canvas tote at 24.00" in page.text
