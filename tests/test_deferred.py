from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from tests.models import Setting
from tests.support import Backend, count_queries


class SettingView(ModelView, model=Setting):
    """The payload is heavy and the list never shows it."""

    display_template = "{name}"
    list_display = ("id", "name")
    deferred_fields = ("options", "notes")


class ShownView(ModelView, model=Setting):
    """A column on show is loaded, whatever the view asks for."""

    name = "shown_settings"
    list_display = ("id", "name", "options")
    deferred_fields = ("options",)


class TitledView(ModelView, model=Setting):
    """A column the record's name is built from is loaded too."""

    name = "titled_settings"
    display_template = "{name} ({options})"
    list_display = ("id",)
    deferred_fields = ("name", "options")


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[SettingView, ShownView, TitledView],
        secret_key="for-the-session",
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestWhatTheQueryAsksFor:
    def test_the_named_columns_are_left_out(self) -> None:
        spec = SettingView().build_spec()

        assert spec.defer == ("options", "notes")

    def test_a_column_on_show_is_kept(self) -> None:
        assert ShownView().build_spec().defer == ()

    def test_a_column_the_name_needs_is_kept(self) -> None:
        assert TitledView().build_spec().defer == ()

    def test_a_column_that_does_not_exist_says_so(self) -> None:
        class Wrong(ModelView, model=Setting):
            name = "wrong_settings"
            deferred_fields = ("payload",)

        with pytest.raises(AdminSiteError) as raised:
            Wrong().build_spec()

        assert "deferred_fields names 'payload'" in str(raised.value)


class TestOnThePage:
    async def test_the_list_does_not_select_them(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        with count_queries(backend) as counter:
            page = await client.get("/admin/settings")

        assert page.status_code == 200
        reads = [one for one in counter.statements if "FROM settings" in one]
        assert reads, counter.statements
        assert all("options" not in one for one in reads)

    async def test_it_still_lists_the_records(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/settings")

        assert "delivery" in page.text
        assert "shop" in page.text

    async def test_the_record_page_loads_them(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/settings/1")

        assert "carriers" in page.text

    async def test_the_form_loads_them(self, client: httpx.AsyncClient) -> None:
        form = await client.get("/admin/settings/1/edit")

        assert "carriers" in form.text

    async def test_a_page_of_records_costs_no_more_queries(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        with count_queries(backend) as counter:
            await client.get("/admin/settings")
        deferred = counter.count

        with count_queries(backend) as plain:
            await client.get("/admin/shown_settings")

        assert deferred <= plain.count
