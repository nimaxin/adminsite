from decimal import Decimal

import pytest

from adminsite.backends.sqlalchemy import (
    ChoiceFilter,
    Database,
    DateRangeFilter,
    NumberRangeFilter,
    RelationFilter,
)
from adminsite.exceptions import AdminSiteError
from adminsite.fields import ChoiceField, DecimalField, RelationField
from adminsite.filters import FilterValue
from adminsite.query import CountMode, Sort
from adminsite.views import ModelView, ViewRegistry
from tests.models import Customer, Order, OrderItem, OrderStatus, Product


class OrderView(ModelView, model=Order):
    group = "Sales"
    list_display = ("id", "customer.name", "status", "total", "created_at")
    search_fields = ("id", "customer.name", "customer.email")
    list_filter = ("status", "total", "created_at", "customer")
    ordering = ("-created_at",)
    page_size = 3
    display_template = "Order {id}"
    fields = (
        RelationField("customer", target=Customer, display_template="{name} ({email})"),
    )


class CustomerView(ModelView, model=Customer):
    group = "Sales"
    list_display = ("name", "email", "region")
    form_fields = ("name", "email", "region")
    readonly_fields = ("email",)


class ProductView(ModelView, model=Product):
    exclude = ("description",)


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
        class ItemView(ModelView, model=OrderItem):
            pass

        view = ItemView()

        assert view.name == "order_items"
        assert view.label == "Order item"
        assert view.label_plural == "Order items"

    def test_a_view_without_a_model_says_so(self) -> None:
        class Broken(ModelView):
            pass

        with pytest.raises(AdminSiteError, match="needs a model"):
            Broken()

    def test_a_record_can_be_named_by_a_template(self, orders: OrderView) -> None:
        assert orders.title_of(Order(id=12)) == "Order 12"

    def test_without_a_template_a_record_names_itself(self) -> None:
        assert CustomerView().title_of(Customer(name="Lena")) == "Lena"


class TestColumns:
    def test_the_listed_columns_are_used(self, orders: OrderView) -> None:
        assert orders.get_list_display() == (
            "id",
            "customer.name",
            "status",
            "total",
            "created_at",
        )

    def test_without_a_list_every_column_is_shown(self) -> None:
        view = ProductView()

        assert view.get_list_display() == ("id", "name", "price")

    def test_headings_read_like_words(self, orders: OrderView) -> None:
        assert orders.label_for("created_at") == "Created at"
        assert orders.label_for("customer.name") == "Name"

    def test_each_column_gets_the_field_that_fits(self, orders: OrderView) -> None:
        assert isinstance(orders.field_for("total"), DecimalField)
        assert isinstance(orders.field_for("status"), ChoiceField)

    def test_a_field_can_be_replaced(self, orders: OrderView) -> None:
        field = orders.field_for("customer")

        assert isinstance(field, RelationField)
        assert field.display_template == "{name} ({email})"


class TestReadingValues:
    def test_a_value_is_read_through_a_link(self, orders: OrderView) -> None:
        order = Order(id=1, customer=Customer(name="Lena Fischer"))

        assert orders.value_at(order, "customer.name") == "Lena Fischer"

    def test_a_missing_link_reads_as_nothing(self, orders: OrderView) -> None:
        assert orders.value_at(Order(id=1), "customer.name") is None

    def test_many_records_read_as_a_list(self) -> None:
        class ItemsView(ModelView, model=Order):
            list_display = ("items.quantity",)

        order = Order(items=[OrderItem(quantity=2), OrderItem(quantity=3)])

        assert ItemsView().value_at(order, "items.quantity") == [2, 3]

    def test_cells_are_formatted_by_the_field(self, orders: OrderView) -> None:
        order = Order(id=1, total=Decimal("1234.5"), status=OrderStatus.SHIPPED)

        assert orders.display(order, "total") == "1,234.50"
        assert orders.display(order, "status") == "Shipped"
        assert orders.display(order, "created_at") == ""


class TestFilters:
    def test_a_filter_is_built_for_each_listed_path(self, orders: OrderView) -> None:
        kinds = [type(item) for item in orders.get_filters()]

        assert kinds == [
            ChoiceFilter,
            NumberRangeFilter,
            DateRangeFilter,
            RelationFilter,
        ]

    def test_a_ready_made_filter_is_kept_as_it_is(self) -> None:
        mine = ChoiceFilter("status", choices=(("PAID", "Paid"),))

        class WithFilter(ModelView, model=Order):
            list_filter = (mine,)

        assert WithFilter().get_filters() == (mine,)

    def test_anything_else_in_list_filter_is_refused(self) -> None:
        class Wrong(ModelView, model=Order):
            list_filter = (42,)  # type: ignore[assignment]

        with pytest.raises(AdminSiteError, match="takes paths or"):
            Wrong()


class TestBuildingAQuery:
    def test_the_query_asks_for_what_the_list_shows(self, orders: OrderView) -> None:
        spec = orders.build_spec()

        assert spec.paths == orders.get_list_display()
        assert spec.search_paths == orders.get_search_fields()
        assert spec.limit == 3
        assert spec.count is CountMode.EXACT

    def test_the_view_ordering_is_used_unless_asked_otherwise(
        self, orders: OrderView
    ) -> None:
        assert orders.build_spec().sort == (Sort("created_at", descending=True),)
        assert orders.build_spec(sort=[Sort("total")]).sort == (Sort("total"),)

    def test_pages_move_the_offset(self, orders: OrderView) -> None:
        assert orders.build_spec(page=1).offset == 0
        assert orders.build_spec(page=3).offset == 6

    async def test_the_query_reads_what_it_asked_for(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            spec = orders.build_spec(
                search="lena",
                filters=[FilterValue("status", ("SHIPPED",))],
            )
            page = await orders.repository.list(session, spec)

            assert len(page) == 1
            assert page.rows[0].status is OrderStatus.SHIPPED
            assert orders.display(page.rows[0], "customer.name") == "Lena Fischer"

    async def test_the_page_size_is_the_one_the_view_set(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            page = await orders.repository.list(session, orders.build_spec())

            assert len(page) == 3
            assert page.total == 7


class TestForms:
    def test_the_form_skips_the_key(self) -> None:
        assert "id" not in ProductView().get_form_fields()

    def test_excluded_fields_stay_out_of_both(self) -> None:
        view = ProductView()

        assert "description" not in view.get_list_display()
        assert "description" not in view.get_form_fields()

    def test_the_listed_form_fields_are_used_in_order(self) -> None:
        assert CustomerView().get_form_fields() == ("name", "email", "region")

    def test_readonly_fields_are_reported(self) -> None:
        assert CustomerView().get_readonly_fields() == ("email",)


class TestOverriding:
    def test_columns_can_depend_on_who_is_asking(self) -> None:
        class StaffView(ModelView, model=Customer):
            list_display = ("name", "email", "region")

            def get_list_display(self, request: object = None) -> tuple[str, ...]:
                if request == "staff":
                    return ("name",)
                return super().get_list_display(request)

        view = StaffView()

        assert view.get_list_display("staff") == ("name",)
        assert view.get_list_display() == ("name", "email", "region")


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
        class ShippedOrders(ModelView, model=Order):
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
