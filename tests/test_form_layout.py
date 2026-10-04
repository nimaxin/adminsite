"""form_layout arranges a view's forms, and its record page, in panels and tabs."""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import (
    Admin,
    AdminSiteError,
    FieldsetWidget,
    ModelView,
    PanelWidget,
    RowWidget,
    TabsWidget,
)
from adminsite.database import Database
from adminsite.views.layout import Placed, arrange, read_layout
from tests.models import Customer, Order


class CustomerView(ModelView[Customer]):
    fields = [Customer.name, Customer.email, Customer.region, Customer.is_active]
    # Region is left out, so it comes last.
    form_layout = [
        PanelWidget(
            "Who", [(Customer.name, Customer.email)], description="How to reach them."
        ),
        PanelWidget("Account", [Customer.is_active]),
    ]


class TabbedOrderView(ModelView[Order]):
    name = "tabbed_orders"
    fields = [Order.customer, Order.status, Order.note, Order.created_at]
    form_layout = [
        TabsWidget(
            [
                ("Order", [Order.customer, Order.status]),
                ("Notes", [Order.note, Order.created_at]),
            ]
        )
    ]


class FoldedOrderView(ModelView[Order]):
    name = "folded_orders"
    fields = [Order.customer, Order.status, Order.note, Order.created_at]
    form_layout = [
        Order.customer,
        Order.status,
        PanelWidget(
            "More", [Order.note, Order.created_at], collapsible=True, collapsed=True
        ),
    ]


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[CustomerView, TabbedOrderView, FoldedOrderView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def cards(page: httpx.Response) -> list[tuple[str, list[str]]]:
    """Each card of a record page: its heading, and the labels of its rows."""
    found = []
    for section in page.text.split("<section")[1:]:
        if "<dl" not in section:
            continue
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", section.split("<dl", 1)[0])
        labels = re.findall(r"<dt[^>]*>(.*?)</dt>", section.split("</dl>", 1)[0])
        found.append((heading.group(1) if heading else "", labels))
    return found


EDIT_ORDER = {
    "customer": "1",
    "status": "PAID",
    "note": "",
    "created_at": "2026-09-01T10:30",
}


class TestTheForm:
    async def test_panels_carry_their_titles_and_rows_sit_side_by_side(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        assert re.search(r'<h2 id="panel-1"[^>]*>Who</h2>', form.text)
        assert "How to reach them." in form.text
        row = form.text.split('class="grid gap-5 md:grid-cols-2"', 1)[1]
        row = row.split('id="panel-2"', 1)[0]
        assert 'name="name"' in row
        assert 'name="email"' in row

    async def test_a_panel_s_one_field_leaves_its_label_to_the_title(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        account = form.text.split('id="panel-2"', 1)[1]
        assert re.search(
            r'<legend class="[^"]*\bsr-only\b[^"]*" id="field-is_active-label"', account
        )

    async def test_a_field_the_layout_leaves_out_comes_last(
        self, client: httpx.AsyncClient
    ) -> None:
        form = await client.get("/admin/customers/1/edit")

        assert form.text.index('name="region"') > form.text.index('name="is_active"')

    async def test_tabs_open_on_the_first_with_a_mistake(
        self, client: httpx.AsyncClient
    ) -> None:
        clean = await client.get("/admin/tabbed_orders/1/edit")
        wrong = await client.post(
            "/admin/tabbed_orders/1/edit",
            data={**EDIT_ORDER, "created_at": "not a date"},
        )

        assert re.search(
            r'role="tab"[^>]*id="tabs-customer-0"[^>]*aria-selected="true"', clean.text
        )
        assert wrong.status_code == 422
        assert re.search(
            r'role="tab"[^>]*id="tabs-customer-1"[^>]*aria-selected="true"', wrong.text
        )
        notes = wrong.text.split('id="tabs-customer-1"', 1)[1].split("</button>", 1)[0]
        assert "Has mistakes" in notes

    async def test_a_folded_panel_opens_where_it_holds_a_mistake(
        self, client: httpx.AsyncClient
    ) -> None:
        clean = await client.get("/admin/folded_orders/1/edit")
        wrong = await client.post(
            "/admin/folded_orders/1/edit",
            data={**EDIT_ORDER, "created_at": "not a date"},
        )

        assert re.search(r"<details class=\"group[^\"]*\"\s*>", clean.text)
        assert re.search(r"<details class=\"group[^\"]*\"\s*open>", wrong.text)


class TestTheRecordPage:
    async def test_it_follows_the_panels(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/customers/1")

        assert cards(page) == [
            ("Who", ["Name", "Email"]),
            ("Account", ["Is active"]),
            ("", ["Region"]),
        ]

    async def test_each_tab_is_a_card_of_its_own(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/tabbed_orders/1")

        # The status stands beside the record's name, so it leaves its tab.
        assert cards(page) == [
            ("Order", ["Customer"]),
            ("Notes", ["Note", "Created at"]),
        ]


class TestTheSetting:
    def test_a_field_placed_twice_is_refused(self) -> None:
        class Twice(ModelView[Customer]):
            fields = [Customer.name, Customer.email]
            form_layout = [Customer.name, PanelWidget("Again", [Customer.name])]

        with pytest.raises(AdminSiteError, match="places 'name' a second time"):
            Twice()

    def test_a_field_the_view_does_not_show_is_refused(self) -> None:
        class Missing(ModelView[Customer]):
            fields = [Customer.name]
            form_layout = [Customer.email]

        with pytest.raises(
            AdminSiteError,
            match=r"Missing\.form_layout\[0\] places 'email', which is not one of",
        ):
            Missing()

    def test_a_misspelt_name_is_refused(self) -> None:
        class Misspelt(ModelView[Customer]):
            fields = [Customer.name]
            form_layout = [RowWidget(["nmae"])]

        with pytest.raises(AdminSiteError, match=r"form_layout\[0\]\.children\[0\]"):
            Misspelt()

    @pytest.mark.parametrize(
        ("layout", "said"),
        [
            ([42], r"Wrong\.form_layout\[0\] is int"),
            ([PanelWidget("Who", "name")], r"form_layout\[0\]\.children is str"),
            ([TabsWidget([("Who",)])], r"form_layout\[0\]\.tabs\[0\] is \('Who',\)"),  # type: ignore[list-item]
            ([FieldsetWidget(3)], r"form_layout\[0\]\.legend is int"),  # type: ignore[arg-type]
        ],
    )
    def test_an_entry_it_cannot_read_is_refused(self, layout: Any, said: str) -> None:
        class Wrong(ModelView[Customer]):
            fields = [Customer.name]
            form_layout = layout

        with pytest.raises(AdminSiteError, match=said):
            Wrong()


def read(entries: list[Any]) -> tuple[Placed, ...]:
    return read_layout(entries, lambda entry, where: str(entry), owner="View")


class TestArranging:
    def test_with_no_layout_every_field_makes_one_group(self) -> None:
        arranged = arrange((), ["a", "b"], form=True)

        assert [card.kind for card in arranged] == ["group"]
        assert arranged[0].paths() == ["a", "b"]

    def test_a_field_a_page_does_not_show_leaves_and_empty_parts_go(self) -> None:
        layout = read([PanelWidget("One", ["a"]), PanelWidget("Two", ["b", "c"])])

        arranged = arrange(layout, ["c"], form=False)

        assert [(card.title, card.paths()) for card in arranged] == [("Two", ["c"])]

    def test_loose_fields_gather_between_panels(self) -> None:
        layout = read(["a", PanelWidget("Panel", ["b"]), "c"])

        arranged = arrange(layout, ["a", "b", "c", "d"], form=False)

        assert [(card.kind, card.paths()) for card in arranged] == [
            ("group", ["a"]),
            ("panel", ["b"]),
            ("group", ["c", "d"]),
        ]

    def test_only_a_form_hides_a_lone_field_s_label(self) -> None:
        layout = read([PanelWidget("Panel", ["a"])])

        form = arrange(layout, ["a"], form=True)
        page = arrange(layout, ["a"], form=False)

        assert form[0].children[0].show_label is False
        assert page[0].children[0].show_label is True

    def test_a_tuple_is_a_row_and_a_list_a_column(self) -> None:
        layout = read([("a", ["b", "c"])])

        assert layout[0].kind == "row"
        assert [child.kind for child in layout[0].children] == ["field", "column"]
