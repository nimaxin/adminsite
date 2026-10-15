import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.database import Database
from tests.models import Customer, Order

STYLESHEET = (
    Path(__file__).parent.parent / "src" / "adminsite" / "static" / "adminsite.css"
)


class OrderView(ModelView[Order]):
    record_title = "Order #{id}"


class CustomerView(ModelView[Customer]):
    """Customers, only ever opened from their orders."""

    record_title = "{name}"
    searchable_fields = ("name",)
    in_sidebar = False


class GroupedOrderView(ModelView[Order]):
    name = "grouped_orders"
    group = "Sales"


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, title="Shop", views=[OrderView, CustomerView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestTheSidebar:
    async def test_it_comes_before_the_page(self, client: httpx.AsyncClient) -> None:
        # A browser draws a page as it arrives. After a long list, the sidebar
        # was missing until the last row came in, and then the page jumped
        # sideways to make room for it.
        page = await client.get("/admin/orders")

        assert page.text.index("<aside") < page.text.index('<main id="content"')


class TestFoldingTheSidebar:
    async def test_a_button_beside_the_name_folds_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")
        before, after = page.text.split('@click="switchSidebar()"', 1)

        # Only from a laptop up, where the sidebar stays on screen.
        assert "max-lg:hidden" in before.rsplit("<button", 1)[1]
        button = after.split("</button>", 1)[0]
        assert 'aria-label="Fold the sidebar"' in button
        assert '"Unfold the sidebar"' in button

    async def test_a_page_reads_the_choice_before_it_draws(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        head = page.text.split("</head>", 1)[0]
        assert 'localStorage.getItem("adminsite-sidebar") === "folded"' in head

    async def test_folded_each_link_keeps_its_name(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")
        nav = page.text.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]

        # Hidden only from sight, and shown when the pointer rests on the icon.
        assert '<span class="truncate folded:lg:sr-only">Orders</span>' in nav
        assert """:title='$store.sidebar.folded ? "Orders" : null'""" in nav

    async def test_folded_a_group_title_still_names_its_links(
        self, database: Database
    ) -> None:
        app = Starlette()
        app.mount("/admin", Admin(database, views=[GroupedOrderView]))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/grouped_orders")

        title = r'<p class="[^"]*folded:lg:sr-only" id="nav-group-1">Sales</p>'
        assert re.search(title, page.text)
        assert 'aria-labelledby="nav-group-1"' in page.text

    def test_folded_the_account_menu_opens_past_its_edge(self) -> None:
        stylesheet = STYLESHEET.read_text(encoding="utf-8")

        assert "[data-sidebar=folded] .drawer-side{overflow:visible}" in stylesheet


class TestAViewLeftOutOfTheSidebar:
    async def test_it_is_not_listed_in_the_sidebar(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")

        assert 'href="/admin/orders"' in page.text
        assert 'href="/admin/customers"' not in page.text

    async def test_nor_among_the_palette_pages_or_the_overview_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        palette = await client.get("/admin/-/search")
        overview = await client.get("/admin/")

        assert "Orders" in palette.text
        assert "Customers" not in palette.text
        assert "New customer" not in palette.text
        assert "Customers" not in overview.text

    async def test_its_records_are_still_found_by_the_palette(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/-/search?q=lena")

        assert "Lena Fischer" in found.text

    async def test_its_pages_still_open(self, client: httpx.AsyncClient) -> None:
        listing = await client.get("/admin/customers")
        record = await client.get("/admin/customers/1")

        assert listing.status_code == 200
        assert record.status_code == 200

    async def test_a_related_record_still_links_to_it(
        self, client: httpx.AsyncClient
    ) -> None:
        order = await client.get("/admin/orders/1")

        assert 'href="/admin/customers/' in order.text
