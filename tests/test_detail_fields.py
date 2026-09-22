from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from tests.models import Customer, Order


class OrderView(ModelView, model=Order):
    """The page shows the customer and when it was made; the form does not."""

    form_fields = ("status", "note")
    detail_fields = ("customer", "status", "total", "created_at")


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email")


class PerUserView(ModelView, model=Order):
    name = "audited"
    form_fields = ("status",)

    def get_detail_fields(
        self, request: Any = None, record: Any = None
    ) -> tuple[str, ...]:
        if request is not None and request.query_params.get("full"):
            return ("customer", "status", "total", "note", "created_at")
        return ("status",)


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[OrderView, CustomerView, PerUserView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def labels(page: httpx.Response) -> list[str]:
    body = page.text.split('<dl class="divide-y', 1)[1].split("</dl>", 1)[0]
    return [
        part.split("</dt>", 1)[0]
        for part in body.split('<dt class="text-base-content/60">')[1:]
    ]


class TestTheDetailPage:
    async def test_it_shows_what_the_view_names(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1")

        assert labels(page) == ["Customer", "Status", "Total", "Created at"]

    async def test_the_form_keeps_its_own_fields(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/orders/1/edit")

        assert 'name="status"' in form.text
        assert 'name="note"' in form.text
        assert 'name="customer"' not in form.text
        assert 'name="total"' not in form.text

    async def test_a_linked_record_is_loaded_with_the_page(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1")

        assert "Lena Fischer" in page.text

    async def test_without_a_list_it_follows_the_form(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers/1")

        assert labels(page) == ["Name", "Email"]

    async def test_it_can_answer_per_request(self, client: httpx.AsyncClient) -> None:
        short = await client.get("/admin/audited/1")
        full = await client.get("/admin/audited/1?full=1")

        assert labels(short) == ["Status"]
        assert len(labels(full)) == 5

    async def test_the_api_reads_the_detail_fields_too(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[OrderView], api=True)
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            body = (await client.get("/admin/-/api/orders/1")).json()

        assert body["total"] == "107.00"
        assert body["note"] is None
