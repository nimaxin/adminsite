from decimal import Decimal
from typing import Any

import pytest
from starlette.requests import Request

from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import BaseField, DecimalField, EnumField, Field, RelationField
from adminsite.filters import (
    ChoiceFilter,
    DateRangeFilter,
    FilterValue,
    NumberRangeFilter,
    RelationFilter,
)
from adminsite.query import CountMode, Sort
from adminsite.security import RequestAction
from adminsite.views import ModelView, ViewRegistry
from tests.models import Customer, Order, OrderItem, OrderStatus, Product
from tests.support import request_from


class OrderView(ModelView[Order]):
    group = "Sales"
    fields = [
        "id",
        "customer.name",
        RelationField("customer", target=Customer, record_title="{name} ({email})"),
        "status",
        "total",
        "note",
        "created_at",
    ]
    exclude_fields_from_list = ["customer", "note"]
    searchable_fields = ["id", "customer.name", "customer.email"]
    list_filters = ["status", "total", "created_at", "customer"]
    fields_default_sort = ["-created_at"]
    page_size = 3
    record_title = "Order {id}"


class CustomerView(ModelView[Customer]):
    group = "Sales"
    fields = ["name", Field("email", read_only=True), "region"]


class ProductView(ModelView[Product]):
    pass


@pytest.fixture
def orders() -> OrderView:
    return OrderView()


class TestNaming:
    def test_a_view_names_itself_from_its_model(self) -> None:
        view = ProductView()

        assert view.name == "products"
        assert view.label == "Product"
        assert view.label_plural == "Products"

    def test_a_two_word_model_reads_properly(self) -> None:
        class ItemView(ModelView[OrderItem]):
            pass

        view = ItemView()

        assert view.name == "order_items"
        assert view.label == "Order item"
        assert view.label_plural == "Order items"

    def test_a_view_without_a_model_says_so(self) -> None:
        class Broken(ModelView[Any]):
            pass

        with pytest.raises(AdminSiteError, match="needs a model"):
            Broken()

    def test_a_record_can_be_named_by_a_template(self, orders: OrderView) -> None:
        assert orders.get_record_title(Order(id=12)) == "Order 12"

    def test_without_a_template_a_record_names_itself(self) -> None:
        assert CustomerView().get_record_title(Customer(name="Lena")) == "Lena"


class TestColumns:
    def test_the_listed_columns_are_used(self, orders: OrderView) -> None:
        assert orders._pages.list_fields(request_from()) == (
            "id",
            "customer.name",
            "status",
            "total",
            "created_at",
        )

    def test_without_a_list_every_column_is_shown(self) -> None:
        view = ProductView()

        assert view._pages.list_fields(request_from()) == (
            "id",
            "name",
            "price",
            "description",
        )

    def test_headings_read_like_words(self, orders: OrderView) -> None:
        assert orders._fields.label_for("created_at") == "Created at"
        assert orders._fields.label_for("customer.name") == "Customer name"

    def test_each_column_gets_the_field_that_fits(self, orders: OrderView) -> None:
        assert isinstance(orders._fields.field_for("total"), DecimalField)
        assert isinstance(orders._fields.field_for("status"), EnumField)

    def test_a_field_can_be_replaced(self, orders: OrderView) -> None:
        field = orders._fields.field_for("customer")

        assert isinstance(field, RelationField)
        assert field.record_title == "{name} ({email})"


class TestReadingValues:
    def test_a_value_is_read_through_a_link(self, orders: OrderView) -> None:
        order = Order(id=1, customer=Customer(name="Lena Fischer"))

        assert orders._fields.value_at(order, "customer.name") == "Lena Fischer"

    def test_a_missing_link_reads_as_nothing(self, orders: OrderView) -> None:
        assert orders._fields.value_at(Order(id=1), "customer.name") is None

    def test_many_records_read_as_a_list(self) -> None:
        class ItemsView(ModelView[Order]):
            fields = ["items.quantity"]

        order = Order(items=[OrderItem(quantity=2), OrderItem(quantity=3)])

        assert ItemsView()._fields.value_at(order, "items.quantity") == [2, 3]

    def test_cells_are_formatted_by_the_field(self, orders: OrderView) -> None:
        order = Order(id=1, total=Decimal("1234.5"), status=OrderStatus.SHIPPED)

        assert orders._fields.display(order, "total") == "1,234.50"
        assert orders._fields.display(order, "status") == "Shipped"
        assert orders._fields.display(order, "created_at") == ""


class TestFilters:
    def test_a_filter_is_built_for_each_listed_path(self, orders: OrderView) -> None:
        kinds = [type(item) for item in orders._pages.list_filters(request_from())]

        assert kinds == [
            ChoiceFilter,
            NumberRangeFilter,
            DateRangeFilter,
            RelationFilter,
        ]

    def test_a_ready_made_filter_is_kept_as_it_is(self) -> None:
        mine = ChoiceFilter("status", choices=(("PAID", "Paid"),))

        class WithFilter(ModelView[Order]):
            list_filters = (mine,)

        assert WithFilter()._pages.list_filters(request_from()) == (mine,)

    def test_anything_else_in_list_filters_is_refused(self) -> None:
        class Wrong(ModelView[Order]):
            list_filters = (42,)  # type: ignore[assignment]

        with pytest.raises(AdminSiteError, match="takes columns or"):
            Wrong()


class TestBuildingAQuery:
    def test_the_query_asks_for_what_the_list_shows(self, orders: OrderView) -> None:
        spec = orders._reader.build_spec(request=request_from())

        assert spec.paths == orders._pages.list_fields(request_from())
        assert spec.search_paths == orders._pages.search_paths(request_from())
        assert spec.limit == 3
        assert spec.count is CountMode.EXACT

    def test_the_view_ordering_is_used_unless_asked_otherwise(
        self, orders: OrderView
    ) -> None:
        assert orders._reader.build_spec(request=request_from()).sort == (
            Sort("created_at", descending=True),
        )
        assert orders._reader.build_spec(
            sort=[Sort("total")], request=request_from()
        ).sort == (Sort("total"),)

    def test_pages_move_the_offset(self, orders: OrderView) -> None:
        assert orders._reader.build_spec(page=1, request=request_from()).offset == 0
        assert orders._reader.build_spec(page=3, request=request_from()).offset == 6

    async def test_the_query_reads_what_it_asked_for(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            spec = orders._reader.build_spec(
                search="lena",
                filters=[FilterValue("status", ("SHIPPED",))],
                request=request_from(),
            )
            page = await orders._repository.list(session, spec)

            assert len(page) == 1
            assert page.rows[0].status is OrderStatus.SHIPPED
            assert (
                orders._fields.display(page.rows[0], "customer.name") == "Lena Fischer"
            )

    async def test_the_page_size_is_the_one_the_view_set(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            page = await orders._repository.list(
                session, orders._reader.build_spec(request=request_from())
            )

            assert len(page) == 3
            assert page.total == 7


class TestForms:
    def test_the_form_skips_the_key(self) -> None:
        assert "id" not in ProductView()._pages.form_fields(request_from())

    def test_a_foreign_key_becomes_its_link_in_the_form(self) -> None:
        class PlainOrders(ModelView[Order]):
            pass

        fields = PlainOrders()._pages.form_fields(request_from())

        assert "customer" in fields
        assert "customer_id" not in fields

    def test_a_foreign_key_becomes_its_link_in_the_list(self) -> None:
        class PlainOrders(ModelView[Order]):
            pass

        columns = PlainOrders()._pages.list_fields(request_from())

        assert columns.index("customer") == 1
        assert "customer_id" not in columns

    def test_the_link_gets_a_picker(self) -> None:
        class PlainOrders(ModelView[Order]):
            pass

        assert isinstance(PlainOrders()._fields.field_for("customer"), RelationField)

    def test_a_column_left_out_of_fields_is_on_no_page(self) -> None:
        class ShortProducts(ModelView[Product]):
            fields = ["id", "name", "price"]

        view = ShortProducts()

        assert "description" not in view._pages.list_fields(request_from())
        assert "description" not in view._pages.form_fields(request_from())
        assert "description" not in view._pages.detail_fields(request_from())

    def test_the_listed_form_fields_are_used_in_order(self) -> None:
        assert CustomerView()._pages.form_fields(request_from()) == (
            "name",
            "email",
            "region",
        )

    def test_read_only_fields_are_reported(self) -> None:
        assert CustomerView()._pages.readonly_paths(request_from()) == ("email",)


class TestOverriding:
    def test_columns_can_depend_on_who_is_asking(self) -> None:
        class StaffView(ModelView[Customer]):
            fields = ["name", "email", "region"]

            def can_access_field(
                self, request: Request, field: BaseField, action: RequestAction
            ) -> bool:
                return (
                    request.scope.get("user_record") != "staff" or field.name == "name"
                )

        view = StaffView()

        assert view._pages.list_fields(request_from("staff")) == ("name",)
        assert view._pages.list_fields(request_from()) == ("name", "email", "region")


class TestRegistry:
    def test_views_are_found_by_name(self) -> None:
        registry = ViewRegistry()
        orders = registry.add(OrderView)

        assert registry.get("orders") is orders
        assert registry.find("nope") is None
        assert "orders" in registry

    def test_a_class_or_an_instance_can_be_added(self) -> None:
        registry = ViewRegistry()
        registry.add(OrderView)
        registry.add(CustomerView())

        assert len(registry) == 2

    def test_two_views_cannot_share_a_name(self) -> None:
        registry = ViewRegistry()
        registry.add(OrderView)

        with pytest.raises(AdminSiteError, match="Two views are called"):
            registry.add(OrderView)

    def test_a_second_view_of_a_model_just_needs_a_name(self) -> None:
        class ShippedOrders(ModelView[Order]):
            name = "shipped_orders"
            label_plural = "Shipped orders"

        registry = ViewRegistry()
        registry.add(OrderView)
        registry.add(ShippedOrders)

        assert len(registry) == 2
        assert registry.get("shipped_orders").label_plural == "Shipped orders"

    def test_views_are_grouped_for_the_sidebar(self) -> None:
        registry = ViewRegistry()
        registry.add(OrderView)
        registry.add(CustomerView)
        registry.add(ProductView)

        groups = dict(registry.grouped())

        assert [view.name for view in groups["Sales"]] == ["orders", "customers"]
        assert [view.name for view in groups[""]] == ["products"]

    def test_a_model_finds_its_view(self) -> None:
        registry = ViewRegistry()
        registry.add(OrderView)

        assert registry.for_model(Order) is not None
        assert registry.for_model(Product) is None

    def test_an_unknown_name_says_so(self) -> None:
        with pytest.raises(AdminSiteError, match="No view is called"):
            ViewRegistry().get("ghosts")
