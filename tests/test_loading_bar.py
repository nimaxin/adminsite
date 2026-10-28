import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import action
from adminsite.database import Database, SessionAdapter
from tests.models import Customer, Order, OrderStatus

STYLESHEET = (
    Path(__file__).parent.parent / "src" / "adminsite" / "static" / "adminsite.css"
)


class OrderView(ModelView[Order]):
    can_import = True

    @action("Confirm", on="record")
    async def confirm(self, record: Order, session: SessionAdapter) -> str:
        record.status = OrderStatus.PAID
        return f"Order {record.id} confirmed."


class CustomerView(ModelView[Customer]):
    pass


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, title="Shop", views=[OrderView, CustomerView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestTheLoadingBar:
    async def test_a_link_or_a_form_leaving_the_page_starts_it(
        self, client: httpx.AsyncClient
    ) -> None:
        # The old page stays on screen until the next one is drawn, so on a
        # slow network a click seemed to do nothing until then.
        page = await client.get("/admin/orders")
        head = page.text.split("</head>", 1)[0]

        assert '<div id="loading-bar"' in page.text
        assert 'document.documentElement.dataset.leaving = "";' in head
        assert 'document.addEventListener("submit", (event) => {' in head

    def test_it_waits_before_it_shows_and_fades_on_its_own(self) -> None:
        # A page that comes at once shows none, and a file an action answers
        # with, which leaves the page where it is, does not leave it running.
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        rule = "[data-leaving] #loading-bar{animation:8s cubic-bezier(.1,.7,.2,1) .15s"

        assert rule in stylesheet
        assert "to{opacity:0;width:90%}" in stylesheet

    async def test_a_link_to_a_file_starts_none(
        self, client: httpx.AsyncClient
    ) -> None:
        # The browser keeps this page while it downloads the file, so the bar
        # would run on until it faded.
        page = await client.get("/admin/orders")
        importing = await client.get("/admin/orders/import")

        export = 'href="/admin/orders/export" data-keeps="" data-file'
        assert page.text.count(export) == 2
        assert 'href="/admin/orders/import/template" data-file' in importing.text

    async def test_an_action_without_a_dialog_starts_it_too(
        self, client: httpx.AsyncClient
    ) -> None:
        # A form sent with submit() starts no submit event, which the bar
        # waits for; requestSubmit() sends it as its button would.
        page = await client.get("/admin/orders/1")

        assert "else form.requestSubmit();" in page.text


class TestTheSidebarWhileThePageLoads:
    async def test_a_clicked_link_shows_as_open_at_once(
        self, client: httpx.AsyncClient
    ) -> None:
        # A link looks open from its aria-current, which the click moves to it
        # before the page it opens arrives.
        page = await client.get("/admin/orders")
        nav = page.text.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
        stylesheet = STYLESHEET.read_text(encoding="utf-8")
        looks_open = (
            r".aria-\[current\=page\]\:bg-base-100[aria-current=page]"
            "{background-color:var(--color-base-100)}"
        )

        links = re.findall(r"<a [^>]*>", nav)
        assert links
        assert all("aria-[current=page]:bg-base-100" in link for link in links)
        assert looks_open in stylesheet
        assert "if (listed) showOpen(listed);" in page.text
