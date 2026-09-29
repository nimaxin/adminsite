from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import (
    Admin,
    AdminSiteError,
    Descending,
    FieldOptions,
    Inline,
    Link,
    ModelView,
    Sort,
)
from adminsite.fields import Computed, RelationField, TextField
from tests.models import Customer, Order, OrderItem, Product, Shelf
from tests.support import Backend


def line_count(order: Order) -> int:
    return len(order.items)


class OrderView(ModelView[Order]):
    fields = [
        Order.id,
        Order.customer,
        Link(Order.customer, Customer.email),
        Order.status,
        Order.total,
        TextField("note", exclude_from_list=True),
        FieldOptions(
            "created_at",
            hidden_in_list=True,
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
        Computed("lines", line_count, label="Lines", needs=("items",)),
    ]
    searchable_fields = [Order.id, Link(Order.customer, Customer.email)]
    sortable_fields = [Order.created_at, Order.total]
    fields_default_sort = [Descending(Order.total)]


class TestFieldsPlaceEachColumnOnEveryPage:
    def test_the_list_shows_them_in_order(self) -> None:
        assert OrderView().get_list_display() == (
            "id",
            "customer",
            "customer.email",
            "status",
            "total",
            "lines",
        )

    def test_a_hidden_column_waits_in_the_columns_menu(self) -> None:
        view = OrderView()

        assert "created_at" not in view.get_list_display()
        assert view.get_column_choices()[-1] == "created_at"

    def test_the_record_page_shows_every_field(self) -> None:
        assert OrderView().get_detail_fields() == (
            "id",
            "customer",
            "customer.email",
            "status",
            "total",
            "note",
            "created_at",
            "lines",
        )

    def test_the_form_edits_only_what_can_be_edited(self) -> None:
        # The key the database numbers, the related customer's email and
        # the worked out line count are shown, never typed in.
        assert OrderView().get_form_fields() == ("customer", "status", "total", "note")

    def test_the_field_given_in_full_is_used(self) -> None:
        assert isinstance(OrderView().field_for("note"), TextField)

    def test_the_create_and_edit_forms_can_differ(self) -> None:
        class SetOnce(ModelView[Order]):
            fields = [
                Order.customer,
                FieldOptions("status", exclude_from_edit=True),
                Order.total,
            ]

        view = SetOnce()

        assert view.get_form_fields() == ("customer", "status", "total")
        assert view.get_form_fields(record=Order(id=1)) == ("customer", "total")

    def test_a_field_named_twice_keeps_its_first_place(self) -> None:
        class Twice(ModelView[Order]):
            fields = [Order.total, Order.status, "total"]

        assert Twice().get_list_display() == ("total", "status")


class TestExcludeLists:
    def test_each_list_leaves_a_field_off_one_page(self) -> None:
        class Trimmed(ModelView[Order]):
            fields = [Order.id, Order.customer, Order.status, Order.note]
            exclude_fields_from_list = [Order.note]
            exclude_fields_from_detail = [Order.status]
            exclude_fields_from_create = [Order.status]
            exclude_fields_from_edit = [Order.customer]
            exclude_fields_from_export = [Order.customer]

        view = Trimmed()

        assert view.get_list_display() == ("id", "customer", "status")
        assert view.get_detail_fields() == ("id", "customer", "note")
        assert view.get_form_fields() == ("customer", "note")
        assert view.get_form_fields(record=Order(id=1)) == ("status", "note")
        assert view.exported(("id", "customer", "status")) == ("id", "status")

    def test_they_trim_every_column_when_fields_is_empty(self) -> None:
        class Trimmed(ModelView[Product]):
            exclude_fields_from_list = [Product.description]

        view = Trimmed()

        assert view.get_list_display() == ("id", "name", "price")
        assert "description" in view.get_form_fields()


class TestKeysInForms:
    def test_a_key_people_type_is_in_the_form(self) -> None:
        class ShelfView(ModelView[Shelf]):
            fields = [Shelf.aisle, Shelf.slot, Shelf.label]

        assert ShelfView().get_form_fields() == ("aisle", "slot", "label")

    def test_so_it_is_with_no_fields_listed(self) -> None:
        class ShelfView(ModelView[Shelf]):
            pass

        assert ShelfView().get_form_fields() == ("aisle", "slot", "label")

    def test_a_numbered_key_is_not(self) -> None:
        class ProductView(ModelView[Product]):
            pass

        assert "id" not in ProductView().get_form_fields()


class TestSearchSortAndOrder:
    def test_searchable_fields_name_the_search_paths(self) -> None:
        assert OrderView().get_search_fields() == ("id", "customer.email")

    def test_only_sortable_fields_sort(self) -> None:
        view = OrderView()

        assert view.sortable("total")
        assert view.sortable("created_at")
        assert not view.sortable("status")

    def test_without_sortable_fields_every_stored_column_sorts(self) -> None:
        class Plain(ModelView[Order]):
            fields = [Order.status, Computed("lines", line_count, needs=("items",))]

        view = Plain()

        assert view.sortable("status")
        assert not view.sortable("lines")

    def test_fields_default_sort_is_where_the_list_starts(self) -> None:
        class Sorted(ModelView[Order]):
            fields_default_sort = [Descending(Order.created_at), "total"]

        assert Sorted().get_ordering() == (
            Sort("created_at", descending=True),
            Sort("total"),
        )


class TestAViewThatStillSetsTheOldSettings:
    def test_fields_only_changes_the_fields_they_name(self) -> None:
        class OldShape(ModelView[Order]):
            list_display = ["id", "customer"]
            fields = [RelationField("customer", target=Customer)]

        view = OldShape()

        assert view.get_list_display() == ("id", "customer")
        assert view.get_form_fields() == (
            "customer",
            "status",
            "total",
            "note",
            "created_at",
        )
        assert isinstance(view.field_for("customer"), RelationField)


class TestInlinesTakeAttributes:
    def test_the_relation_and_the_fields(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [
                Inline(Order.items, fields=[OrderItem.product, OrderItem.quantity])
            ]

        view = WithLines()

        assert view.get_inlines()[0].name == "items"
        assert view.inline_view("items").get_form_fields() == ("product", "quantity")

    def test_a_relation_of_another_model(self) -> None:
        class Wrong(ModelView[Order]):
            inlines = [Inline(Customer.orders)]

        with pytest.raises(AdminSiteError, match=r"Wrong\.inlines\[0\]: Customer"):
            Wrong()

    def test_a_field_of_another_model(self) -> None:
        class Wrong(ModelView[Order]):
            inlines = [Inline(Order.items, fields=[Order.note])]

        with pytest.raises(AdminSiteError) as caught:
            Wrong()

        message = str(caught.value)
        assert "Wrong.inlines[0].fields" in message
        assert "Order.note is a column of Order, not of OrderItem" in message


class TestMistakesStopTheView:
    def test_a_column_that_does_not_exist(self) -> None:
        class Misspelt(ModelView[Order]):
            fields = [Order.id, "totl"]

        with pytest.raises(AdminSiteError, match=r"Misspelt\.fields: .*'totl'"):
            Misspelt()

    def test_a_column_of_another_model(self) -> None:
        class Elsewhere(ModelView[Order]):
            fields = [Order.id, Customer.name]

        with pytest.raises(AdminSiteError, match=r"Customer\.name is a column of"):
            Elsewhere()

    def test_one_string_where_a_list_belongs(self) -> None:
        class OneString(ModelView[Order]):
            searchable_fields = "note"

        with pytest.raises(AdminSiteError, match=r"searchable_fields = \['note'\]"):
            OneString()


@pytest.fixture
async def client(backend: Backend) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(backend.database, views=[OrderView, ModelView[Customer]])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestThePages:
    async def test_the_list_shows_the_fields(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")

        assert page.status_code == 200
        assert "lena@fischer.de" in page.text
        # The largest total first, as fields_default_sort says.
        assert page.text.index("118.00") < page.text.index("72.00")

    async def test_a_column_that_does_not_sort_ignores_the_url(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?sort=status")

        assert page.text.index("118.00") < page.text.index("72.00")

    async def test_the_export_leaves_out_what_it_is_told_to(
        self, backend: Backend
    ) -> None:
        class Exported(OrderView):
            exclude_fields_from_export = [Link(Order.customer, Customer.email)]

        admin = Admin(backend.database, views=[Exported, ModelView[Customer]])
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            answer = await client.get("/admin/orders/export")

        header = answer.text.splitlines()[0]
        assert header == "Id,Customer,Status,Total,Lines"
        assert "lena@fischer.de" not in answer.text

    async def test_the_new_record_form(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders/new")

        assert page.status_code == 200
        assert 'name="note"' in page.text
        assert 'name="created_at"' not in page.text
        assert 'name="id"' not in page.text
