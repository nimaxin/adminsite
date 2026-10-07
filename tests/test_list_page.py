import html
import re

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Link, ModelView
from adminsite.database import Database
from adminsite.fields import EmailField
from tests.models import Customer, Order, OrderItem


class OrderView(ModelView[Order]):
    fields = ["id", "customer.name", "status", "total", "created_at"]
    searchable_fields = ("id", "customer.name", "customer.email")
    list_filters = ("status", "total", "created_at")
    fields_default_sort = ("-created_at",)
    page_size = 5


class CustomerView(ModelView[Customer]):
    fields = ["name", "email", "region", "is_active"]
    searchable_fields = ("name", "email")
    list_filters = ("region", "is_active")


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    admin = Admin(database, title="Shop")
    admin.add_view(OrderView)
    admin.add_view(CustomerView)
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestSearch:
    async def test_the_search_box_narrows_the_list(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert "1 to 2 of 2" in response.text

    async def test_the_search_reaches_through_a_relationship(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=fischer.de")

        assert "1 to 2 of 2" in response.text

    async def test_the_term_stays_in_the_box(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert 'value="lena"' in response.text

    async def test_a_search_matching_nothing_says_so(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=nobody")

        assert "No orders to show." in response.text


class TestFilters:
    async def test_a_filter_narrows_the_list(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=SHIPPED")

        assert "1 to 2 of 2" in response.text

    async def test_two_values_match_either(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=SHIPPED&status=PAID")

        assert "1 to 4 of 4" in response.text

    async def test_the_filter_panel_shows_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")

        assert "Shipped" in response.text
        assert "Refunded" in response.text

    async def test_an_active_filter_shows_a_chip(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED")

        assert "Status: Shipped" in response.text

    async def test_the_chip_links_to_the_list_without_it(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED&q=a")

        removal = re.search(
            r'href="([^"]+)"\s+aria-label="Remove this filter"', response.text
        )
        assert removal is not None
        link = html.unescape(removal.group(1))
        assert "q=a" in link
        assert "status=" not in link

    async def test_clear_all_keeps_the_search_and_the_sort(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/admin/orders?status=SHIPPED&total=100,&q=a&sort=total&page=2"
        )

        clear = re.search(r'href="([^"]+)">Clear all</a>', response.text)
        assert clear is not None
        link = html.unescape(clear.group(1))
        assert "q=a" in link
        assert "sort=total" in link
        assert "status=" not in link
        assert "total=" not in link.replace("sort=total", "")
        assert "page=" not in link

    async def test_a_chip_opens_the_filters_at_its_filter(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED")

        assert "openFilters('filter-status')" in response.text

    async def test_no_chips_without_a_filter(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders")

        assert "Clear all" not in response.text

    async def test_what_comes_back_with_the_table(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/admin/orders?status=SHIPPED", headers={"HX-Request": "true"}
        )

        for part in ("filter-chips", "filters-count", "filters-footer"):
            found = re.search(rf'<[a-z]+ id="{part}"[^>]*>', response.text)
            assert found is not None, part
            assert 'hx-swap-oob="true"' in found.group(0)
        assert "Status: Shipped" in response.text
        assert "Show 2 orders" in response.text

    async def test_the_filters_button_counts_the_filters_in_use(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=SHIPPED&total=100,")

        assert 'popovertarget="filters"' in response.text
        count = re.search(r'<span id="filters-count"[^>]*>(\d*)</span>', response.text)
        assert count is not None
        assert count.group(1) == "2"

    async def test_one_drawer_holds_every_filter(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")

        drawer = re.search(
            r'<div id="filters" popover.*?</form>', response.text, re.DOTALL
        )
        assert drawer is not None
        for name in ("status", "total", "created_at"):
            assert f'<fieldset id="filter-{name}">' in drawer.group(0)

    async def test_the_drawer_applies_as_it_is_picked(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=a")

        form = re.search(
            r'<form[^>]*data-keeps="status total created_at".*?</form>',
            response.text,
            re.DOTALL,
        )
        assert form is not None
        opening = html.unescape(form.group(0).split(">", 1)[0])
        assert "change[target.hasAttribute('data-applies')]" in opening
        assert 'hx-target="#records"' in opening
        assert 'hx-push-url="true"' in opening
        # A filter not in use stays out of the address.
        assert "data-drops-empty" in opening
        assert 'name="q" value="a"' in form.group(0)
        assert 'type="submit"' not in form.group(0)
        shipped = re.search(
            r'<input type="checkbox" name="status"[^>]*>', form.group(0)
        )
        assert shipped is not None
        assert "data-applies" in shipped.group(0)

    async def test_a_range_waits_for_both_ends(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders")

        dates = re.search(
            r'<fieldset id="filter-created_at">.*?</fieldset>', response.text, re.DOTALL
        )
        assert dates is not None
        week = re.search(r'<input type="radio"[^>]*value="week"[^>]*>', dates.group(0))
        between = re.search(
            r'<input type="radio"[^>]*value="custom"[^>]*>', dates.group(0)
        )
        assert week is not None
        assert between is not None
        assert "data-applies" in week.group(0)
        assert "data-applies" not in between.group(0)

    @pytest.mark.parametrize(
        ("query", "chip"),
        [
            ("total=100,", "Total: 100 or more"),
            ("total=,200", "Total: 200 or less"),
            ("total=100,200", "Total: 100 to 200"),
            ("created_at=2026-09-01,", "Created at: 2026-09-01 or later"),
            ("created_at=,2026-09-30", "Created at: 2026-09-30 or earlier"),
            ("created_at=week", "Created at: Last 7 days"),
        ],
    )
    async def test_a_range_chip_says_the_range_in_words(
        self, client: httpx.AsyncClient, query: str, chip: str
    ) -> None:
        response = await client.get(f"/admin/orders?{query}")

        assert chip in response.text

    async def test_search_and_filter_work_together(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena&status=SHIPPED")

        assert "1 to 1 of 1" in response.text

    async def test_an_unknown_parameter_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?colour=green")

        assert "1 to 5 of 7" in response.text


class TestSorting:
    async def test_a_column_can_be_sorted(self, client: httpx.AsyncClient) -> None:
        ascending = await client.get("/admin/orders?sort=total")
        descending = await client.get("/admin/orders?sort=-total")

        assert ascending.text != descending.text

    async def test_the_heading_shows_which_way_it_is_sorted(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?sort=total")

        assert "&uarr;" in response.text

    async def test_clicking_the_same_column_turns_it_around(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?sort=total")

        assert "sort=-total" in response.text

    async def test_sorting_keeps_the_search(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?q=lena&sort=total")

        assert "q=lena" in response.text
        assert "1 to 2 of 2" in response.text


class LinkedOrderView(ModelView[Order]):
    name = "linked_orders"
    fields = [Order.id, Order.customer, Order.items, Order.status]


class CustomerOrderView(ModelView[Customer]):
    name = "customer_orders"
    fields = [Customer.id, Customer.name, Link(Customer.orders, Order.status)]


class UnsortedOrderView(ModelView[Order]):
    name = "unsorted_orders"
    fields = [Order.id, Order.status, Order.total]
    sortable_fields = []


@pytest.fixture
def sorting(database: Database) -> httpx.AsyncClient:
    admin = Admin(
        database, views=[LinkedOrderView, CustomerOrderView, UnsortedOrderView]
    )
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def sort_links(page: str) -> list[str]:
    """The addresses the list's column headings sort by."""
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return [html.unescape(link) for link in re.findall(r'href="([^"]+)"', head)]


def row_keys(page: str) -> list[str]:
    """The keys of the rows, in the order the list shows them."""
    return re.findall(r'name="keys" value="([^"]+)"', page)


class TestWhatCanBeSorted:
    async def test_a_relationship_has_no_sort_link(
        self, sorting: httpx.AsyncClient
    ) -> None:
        orders = await sorting.get("/admin/linked_orders")
        customers = await sorting.get("/admin/customer_orders")

        assert sort_links(orders.text) == [
            "/admin/linked_orders?sort=id",
            "/admin/linked_orders?sort=status",
        ]
        assert sort_links(customers.text) == [
            "/admin/customer_orders?sort=id",
            "/admin/customer_orders?sort=name",
        ]

    async def test_every_sort_link_answers(self, sorting: httpx.AsyncClient) -> None:
        for view in ("linked_orders", "customer_orders"):
            page = await sorting.get(f"/admin/{view}")
            for link in sort_links(page.text):
                assert (await sorting.get(link)).status_code == 200

    @pytest.mark.parametrize("sort", ["customer", "-customer", "items"])
    async def test_a_sort_by_a_relationship_is_ignored(
        self, sorting: httpx.AsyncClient, sort: str
    ) -> None:
        plain = await sorting.get("/admin/linked_orders")
        asked = await sorting.get(f"/admin/linked_orders?sort={sort}")

        assert asked.status_code == 200
        assert row_keys(asked.text) == row_keys(plain.text)

    async def test_an_empty_sortable_fields_sorts_by_none(
        self, sorting: httpx.AsyncClient
    ) -> None:
        plain = await sorting.get("/admin/unsorted_orders")
        asked = await sorting.get("/admin/unsorted_orders?sort=-id")
        sortable = await sorting.get("/admin/linked_orders?sort=-id")

        assert sort_links(plain.text) == []
        assert row_keys(asked.text) == row_keys(plain.text)
        assert row_keys(sortable.text) == row_keys(plain.text)[::-1]


class TestPaging:
    async def test_paging_keeps_the_filters(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?status=PAID&page=1")

        assert "status=PAID" in response.text

    async def test_the_second_page_carries_on(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/orders?page=2")

        assert "6 to 7 of 7" in response.text


class TestPartialUpdates:
    async def test_htmx_gets_only_the_table_back(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/admin/orders?q=lena", headers={"HX-Request": "true"}
        )

        assert response.status_code == 200
        assert "<!doctype html>" not in response.text.lower()
        assert 'id="records"' in response.text

    async def test_a_normal_request_gets_the_whole_page(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?q=lena")

        assert "<!doctype html>" in response.text.lower()


class TestExportLink:
    async def test_the_export_link_keeps_the_filters(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders?status=PAID&q=lena")

        assert "/admin/orders/export?" in response.text
        assert "status=PAID" in response.text


class TestColumns:
    async def test_a_row_opens_with_its_menu_after_the_checkbox(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/customers")
        row = response.text.split("<tbody>", 1)[1].split("</tr>", 1)[0]

        # In reach at the start of the row, however many columns follow.
        checkbox = row.index('name="keys"')
        menu = row.index("Actions for")
        record = row.index("font-medium text-link")
        assert checkbox < menu < record

    async def test_the_columns_share_the_width(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/customers")
        head = response.text.split("<thead>", 1)[1].split("</thead>", 1)[0]

        # No empty cell takes the width: the columns fill it, as in any table,
        # and the last one keeps its distance from the edge.
        assert 'aria-hidden="true"></td>' not in head
        last = head.rsplit("<th ", 1)[1]
        assert re.search(r'class="[^"]*\bpe-4\b', last)

    async def test_a_key_reads_like_text_and_an_amount_lines_up(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/orders")
        head = response.text.split("<thead>", 1)[1].split("</thead>", 1)[0]
        cells = re.findall(r'<th class="([^"]*)"[^>]*>\s*<a[^>]*>\s*(\w+)', head)
        at_the_end = {label: "text-end" in classes for classes, label in cells}

        # The key names its record, so it starts where text starts; the total
        # is an amount, so it lines up on the right.
        assert at_the_end["Id"] is False
        assert at_the_end["Total"] is True


class CustomerTotalsView(ModelView[Customer]):
    name = "customer_totals"
    fields = [
        Customer.id,
        Customer.name,
        Link(Customer.orders, Order.total),
        Link(Customer.orders, Link(Order.customer, Customer.email)),
        Link(Customer.orders, Link(Order.items, OrderItem.quantity)),
    ]


class TestAColumnReadThroughLinksToMany:
    async def test_it_shows_each_record_s_value(self, database: Database) -> None:
        app = Starlette()
        app.mount("/admin", Admin(database, views=[CustomerTotalsView], api=True))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/customer_totals")
            detail = await client.get("/admin/customer_totals/1")
            export = await client.get("/admin/customer_totals/export?format=csv")
            listed = await client.get("/admin/-/api/customer_totals?sort=id")
            one = await client.get("/admin/-/api/customer_totals/1")

        assert page.status_code == detail.status_code == 200
        for shown in ("107.00, 38.50", "lena@fischer.de, lena@fischer.de", "1, 2, 1"):
            assert shown in page.text
            assert shown in detail.text
        lena = '1,Lena Fischer,"107.00, 38.50","lena@fischer.de, lena@fischer.de"'
        assert f'{lena},"1, 2, 1"' in export.text
        assert listed.json()["items"][0] == one.json()
        assert one.json() == {
            "key": "1",
            "id": 1,
            "name": "Lena Fischer",
            "orders.total": ["107.00", "38.50"],
            "orders.customer.email": ["lena@fischer.de", "lena@fischer.de"],
            "orders.items.quantity": [1, 2, 1],
        }


class LongValuesView(ModelView[Customer]):
    name = "long_values"
    fields = [
        Customer.name,
        EmailField(Customer.email),
        Customer.orders,
        Link(Customer.orders, Order.total),
    ]
    fields_default_sort = [Customer.name]


class TestLongValues:
    async def test_every_kind_is_cut_short_where_it_is_long(
        self, database: Database
    ) -> None:
        app = Starlette()
        app.mount("/admin", Admin(database, views=[LongValuesView, OrderView]))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/long_values")
        row = page.text.split("<tbody>", 1)[1].split("</tr>", 1)[0]

        # Text was cut short before; a record's name, an email, the names of
        # many linked records and their values were not, so one long value
        # could stretch its column across the screen.
        link = r'<a class="block w-fit max-w-md truncate [^"]*" href="[^"]+">'
        assert re.search(link + "Aisha Khan</a>", row)
        assert row.count("max-w-md truncate") == 4
