import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from adminsite import Admin, AdminPage, ModelView, Plugin
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from tests.models import Customer, Order

REPORT = """{% extends "adminsite/base.html" %}
{% block header %}<h1>{{ page.label }}</h1>{% endblock %}
{% block content %}<p>Orders so far: {{ orders }}</p>{% endblock %}
"""


class OrderView(ModelView, model=Order):
    pass


class SalesReportPage(AdminPage):
    group = "Reports"
    template = "reports/sales.html"

    async def get_context(self, request: Request) -> dict[str, Any]:
        async with self.admin.database.session() as session:
            orders = len((await session.scalars(Order.__table__.select())).all())
        return {"orders": orders}


class SettingsPage(AdminPage):
    label = "Shop settings"
    received: dict[str, Any] | None = None

    async def get(self, request: Request) -> Response:
        return PlainTextResponse("settings")

    async def post(self, request: Request, form: dict[str, Any]) -> Response:
        SettingsPage.received = form
        return RedirectResponse(self.admin_url(request), status_code=303)

    def admin_url(self, request: Request) -> str:
        return str(request.url)


class HiddenPage(AdminPage):
    template = "reports/sales.html"

    async def allows(self, request: Request) -> bool:
        return False


@pytest.fixture
def templates(tmp_path: Path) -> Path:
    folder = tmp_path / "templates" / "reports"
    folder.mkdir(parents=True)
    (folder / "sales.html").write_text(REPORT, encoding="utf-8")
    return tmp_path / "templates"


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(
    database: Database, templates: Path
) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView],
        template_dirs=[templates],
        pages=[SalesReportPage, SettingsPage, HiddenPage],
    )
    async with serve(admin) as client:
        yield client


class TestNaming:
    def test_the_name_comes_from_the_class(self) -> None:
        page = SalesReportPage()

        assert page.name == "sales_report"
        assert page.label == "Sales report"

    def test_a_name_can_be_taken_once(self, database: Database) -> None:
        admin = Admin(database, pages=[SalesReportPage])

        with pytest.raises(AdminSiteError, match="already called"):
            admin.add_page(SalesReportPage)

    def test_the_admins_own_paths_are_kept(self, database: Database) -> None:
        class ActivityPage(AdminPage):
            pass

        with pytest.raises(AdminSiteError, match="already called 'activity'"):
            Admin(database, pages=[ActivityPage])

    async def test_a_page_needs_a_template_or_its_own_get(
        self, database: Database
    ) -> None:
        class Empty(AdminPage):
            pass

        admin = Admin(database, pages=[Empty])

        with pytest.raises(AdminSiteError, match="needs a template"):
            await admin.pages["empty"].get(Request({"type": "http"}))


class TestShowingAPage:
    async def test_the_template_gets_the_context_and_the_layout(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/-/sales_report")

        assert page.status_code == 200
        assert "Orders so far: 7" in page.text
        assert 'id="palette"' in page.text

    async def test_the_sidebar_lists_it_and_marks_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/-/sales_report")

        assert "Reports" in page.text
        assert re.search(
            r'href="/admin/-/sales_report"\s+class="bg-base-300', page.text
        )

    async def test_a_page_can_answer_by_itself(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/-/settings")

        assert page.text == "settings"

    async def test_a_page_takes_a_form(self, client: httpx.AsyncClient) -> None:
        answer = await client.post("/admin/-/settings", data={"currency": "EUR"})

        assert answer.status_code == 303
        assert SettingsPage.received == {"currency": "EUR"}

    async def test_a_page_without_post_refuses_forms(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.post("/admin/-/sales_report", data={})

        assert answer.status_code == 405

    async def test_a_refused_page_is_hidden_and_closed(
        self, client: httpx.AsyncClient
    ) -> None:
        home = await client.get("/admin/")
        page = await client.get("/admin/-/hidden")

        assert "/admin/-/hidden" not in home.text
        assert page.status_code == 403

    async def test_an_unknown_page_is_missing(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/-/nothing")

        assert page.status_code == 404

    async def test_the_palette_offers_pages(self, client: httpx.AsyncClient) -> None:
        found = await client.get("/admin/-/search?q=sales")

        assert 'href="/admin/-/sales_report"' in found.text


async def export_orders(admin: Admin, request: Request) -> Response:
    return PlainTextResponse("id,total")


class Reports(Plugin):
    name = "reports"

    def __init__(self, templates: Path, static: Path) -> None:
        self.templates = templates
        self.static = static

    def setup(self, admin: Admin) -> None:
        admin.add_view(OrderView)
        admin.add_template_dir(self.templates)
        admin.add_page(SalesReportPage)
        admin.add_route("/-/reports/export", export_orders)
        admin.add_static("reports", self.static)
        admin.add_stylesheet("-/static/reports/reports.css")
        admin.add_script("https://example.com/chart.js")


@pytest.fixture
def static(tmp_path: Path) -> Path:
    folder = tmp_path / "static"
    folder.mkdir()
    (folder / "reports.css").write_text("h1 { color: red; }", encoding="utf-8")
    return folder


class TestPlugins:
    async def test_a_plugin_adds_everything_it_brings(
        self, database: Database, templates: Path, static: Path
    ) -> None:
        plugin = Reports(templates, static)
        admin = Admin(database, title="Shop", plugins=[plugin])

        async with serve(admin) as client:
            report = await client.get("/admin/-/sales_report")
            exported = await client.get("/admin/-/reports/export")
            css = await client.get("/admin/-/static/reports/reports.css")
            orders = await client.get("/admin/orders")

        assert admin.plugins == [plugin]
        assert "Orders so far: 7" in report.text
        assert exported.text == "id,total"
        assert css.text == "h1 { color: red; }"
        assert orders.status_code == 200
        assert '<link rel="stylesheet" href="/admin/-/static/reports/reports.css">' in (
            report.text
        )
        assert '<script src="https://example.com/chart.js" defer>' in report.text

    def test_extra_routes_live_under_the_dash(self, database: Database) -> None:
        admin = Admin(database)

        with pytest.raises(AdminSiteError, match="under /-/"):
            admin.add_route("/reports", export_orders)

    async def test_routes_are_added_before_the_first_request(
        self, database: Database
    ) -> None:
        admin = Admin(database)
        async with serve(admin) as client:
            await client.get("/admin/")

        with pytest.raises(AdminSiteError, match="before the admin answers"):
            admin.add_route("/-/late", export_orders)

    async def test_extra_routes_sit_behind_the_sign_in(
        self, database: Database
    ) -> None:
        admin = Admin(
            database,
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key="for-the-session",
        )
        admin.add_route("/-/reports/export", export_orders)
        admin.add_route("/-/health", export_orders, guarded=False)

        async with serve(admin) as client:
            guarded = await client.get("/admin/-/reports/export")
            open_ = await client.get("/admin/-/health")

        assert guarded.status_code == 303
        assert open_.text == "id,total"


class TestTheSidebarOrder:
    async def test_views_and_pages_share_their_groups(
        self, database: Database, templates: Path
    ) -> None:
        class CustomerView(ModelView, model=Customer):
            group = "Reports"

        admin = Admin(
            database,
            views=[CustomerView],
            pages=[SalesReportPage],
            template_dirs=[templates],
        )
        async with serve(admin) as client:
            home = await client.get("/admin/")

        assert home.text.count(">Reports</li>") == 1
        assert home.text.index("/admin/customers") < home.text.index(
            "/admin/-/sales_report"
        )
