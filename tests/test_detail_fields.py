import re
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, BaseField, ModelView, RequestAction
from adminsite.database import Database
from tests.models import Customer, Order


class OrderView(ModelView[Order]):
    """The page shows the customer and when it was made; the form does not."""

    fields = ["customer", "status", "total", "note", "created_at"]
    exclude_fields_from_detail = ["note"]
    exclude_fields_from_create = ["customer", "total", "created_at"]
    exclude_fields_from_edit = ["customer", "total", "created_at"]


class CustomerView(ModelView[Customer]):
    fields = ["name", "email"]


class PerUserView(ModelView[Order]):
    name = "audited"
    fields = ["customer", "status", "total", "note", "created_at"]

    def can_access_field(
        self, request: Request, field: BaseField, action: RequestAction
    ) -> bool:
        if action is not RequestAction.DETAIL or request.query_params.get("full"):
            return True
        return field.name == "status"


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
    """The fields listed among the record's details, in order."""
    body = page.text.split("<dl", 1)[1].split("</dl>", 1)[0]
    return re.findall(r"<dt[^>]*>(.*?)</dt>", body)


def linked(page: httpx.Response) -> list[str]:
    """The fields whose value links to the record it names."""
    body = page.text.split("<dl", 1)[1].split("</dl>", 1)[0]
    return re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>\s*<a ", body)


class TestTheDetailPage:
    async def test_it_shows_what_the_view_names(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders/1")

        # The status stands beside the record's name, so it leaves the rows.
        assert labels(page) == ["Customer", "Total", "Created at"]
        assert linked(page) == ["Customer"]
        # With no audit log there is no history to say who changed it.
        assert "Last changed" not in page.text

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

    async def test_with_nothing_excluded_it_shows_every_field(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers/1")

        assert labels(page) == ["Name", "Email"]

    async def test_it_can_answer_per_request(self, client: httpx.AsyncClient) -> None:
        short = await client.get("/admin/audited/1")
        full = await client.get("/admin/audited/1?full=1")

        assert labels(short) == ["Status"]
        assert labels(full) == ["Customer", "Total", "Note", "Created at"]

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
