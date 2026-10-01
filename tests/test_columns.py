from collections.abc import AsyncIterator, Sequence
from typing import Any, Generic, TypeVar

import httpx
import pytest
from sqlalchemy.orm import aliased
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import (
    Admin,
    AdminSiteError,
    ColumnReference,
    Descending,
    Field,
    Link,
    ModelView,
    Sort,
    ViewRegistry,
)
from tests.models import Customer, Order, OrderItem, Product
from tests.support import Backend

M = TypeVar("M")
K = TypeVar("K")


class TestTheModelComesFromTheTypeArgument:
    def test_a_view_reads_its_model(self) -> None:
        class OrderView(ModelView[Order]):
            pass

        view = OrderView()

        assert view.model is Order
        assert view.name == "orders"

    def test_a_subclass_keeps_the_model(self) -> None:
        class OrderView(ModelView[Order]):
            pass

        class ShippedOrders(OrderView):
            name = "shipped"

        assert ShippedOrders().model is Order

    def test_a_view_that_is_generic_itself_passes_the_model_on(self) -> None:
        class ShopView(ModelView[M]):
            page_size = 10

        class CustomerView(ShopView[Customer]):
            pass

        view = CustomerView()

        assert view.model is Customer
        assert view.page_size == 10

    def test_the_model_can_be_any_parameter_of_a_generic_view(self) -> None:
        class KeyedView(ModelView[M], Generic[K, M]):
            pass

        class ProductView(KeyedView[int, Product]):
            pass

        assert ProductView().model is Product

    def test_the_old_keyword_names_the_new_form(self) -> None:
        with pytest.raises(AdminSiteError, match=r"ModelView\[Order\]"):

            class OrderView(ModelView, model=Order):  # type: ignore[type-arg]
                pass

    def test_any_is_no_model(self) -> None:
        class Broken(ModelView[Any]):
            pass

        with pytest.raises(AdminSiteError, match="needs a model"):
            Broken()


class TestAViewWithNoClassOfItsOwn:
    def test_the_admin_registers_it(self, backend: Backend) -> None:
        admin = Admin(backend.database, views=[ModelView[Customer]])

        view = admin.views.get("customers")

        assert view.model is Customer
        assert type(view).__name__ == "CustomerView"

    def test_the_registry_builds_it_too(self) -> None:
        registry = ViewRegistry()

        view = registry.add(ModelView[Product])

        assert view.model is Product

    def test_a_generic_view_of_ones_own_works_the_same(self, backend: Backend) -> None:
        class ShopView(ModelView[M]):
            page_size = 10

        admin = Admin(backend.database, views=[ShopView[Order]])

        view = admin.views.get("orders")
        assert view.model is Order
        assert view.page_size == 10


class TestSettingsNameColumnsByAttribute:
    def test_attributes_links_and_strings_become_paths(self) -> None:
        class OrderView(ModelView[Order]):
            fields = [
                Order.id,
                Link(Order.customer, Customer.name),
                Order.customer,
                "status",
                "customer.email",
                Field(Order.note, hidden_in_list=True),
            ]
            exclude_fields_from_list = [Order.customer]
            searchable_fields = [Order.id, Link(Order.customer, Customer.email)]
            deferred_fields = [Order.note]

            def get_readonly_fields(
                self, request: Request, record: Order | None
            ) -> Sequence[ColumnReference]:
                return [Order.status]

        view = OrderView()

        assert view._list_fields() == (
            "id",
            "customer.name",
            "status",
            "customer.email",
        )
        assert view._column_choices()[-1] == "note"
        assert view._search_paths(None) == ("id", "customer.email")
        assert view._form_fields() == ("customer", "status", "note")
        assert view._readonly_paths()[0] == "status"
        assert view._settings.deferred_fields == ("note",)

    def test_a_link_can_go_through_several_relations(self) -> None:
        class ItemView(ModelView[OrderItem]):
            fields = [Link(OrderItem.order, Link(Order.customer, Customer.name))]

        assert ItemView()._list_fields() == ("order.customer.name",)

    def test_excluded_attributes_leave_the_default_columns(self) -> None:
        class OrderView(ModelView[Order]):
            exclude_fields_from_list = [Order.note, Order.created_at]

        shown = OrderView()._list_fields()

        assert "note" not in shown
        assert "created_at" not in shown
        assert "total" in shown

    def test_a_filter_can_be_named_by_attribute_or_link(self) -> None:
        class OrderView(ModelView[Order]):
            list_filters = [Order.status, Link(Order.customer, Customer.region)]

        paths = [item.path for item in OrderView()._list_filters(None)]

        assert paths == ["status", "customer.region"]


class TestSorts:
    def test_descending_and_a_minus_sort_down(self) -> None:
        class OrderView(ModelView[Order]):
            fields_default_sort = [
                Descending(Order.created_at),
                "-total",
                Order.id,
                Descending(Link(Order.customer, Customer.name)),
            ]

        assert OrderView()._default_sort(None) == (
            Sort("created_at", descending=True),
            Sort("total", descending=True),
            Sort("id"),
            Sort("customer.name", descending=True),
        )

    def test_they_read_as_written(self) -> None:
        assert repr(Descending(Order.total)) == "Descending(Order.total)"
        assert (
            repr(Link(Order.customer, Customer.email))
            == "Link(Order.customer, Customer.email)"
        )


class TestMistakesStopTheView:
    def test_a_column_of_another_model(self) -> None:
        class OrderView(ModelView[Order]):
            fields = [Order.id, Customer.name]

        with pytest.raises(AdminSiteError) as caught:
            OrderView()

        message = str(caught.value)
        assert "OrderView.fields" in message
        assert "Customer.name is a column of Customer, not of Order" in message
        assert "Link(Order.<relation>, Customer.name)" in message

    def test_a_link_that_leads_elsewhere(self) -> None:
        class OrderView(ModelView[Order]):
            searchable_fields = [Link(Order.customer, Product.name)]

        with pytest.raises(AdminSiteError) as caught:
            OrderView()

        message = str(caught.value)
        assert "OrderView.searchable_fields" in message
        assert "Order.customer leads to Customer" in message
        assert "Product.name is a column of Product" in message

    def test_a_link_that_starts_from_a_column(self) -> None:
        class OrderView(ModelView[Order]):
            fields = [Link(Order.note, Customer.name)]

        with pytest.raises(AdminSiteError, match="A link starts from a relationship"):
            OrderView()

    def test_a_link_from_another_model(self) -> None:
        class OrderView(ModelView[Order]):
            fields = [Link(OrderItem.product, Product.name)]

        with pytest.raises(AdminSiteError, match=r"OrderItem\.product is a column of"):
            OrderView()

    def test_one_string_where_a_list_belongs(self) -> None:
        class OrderView(ModelView[Order]):
            searchable_fields = "note"

        with pytest.raises(AdminSiteError) as caught:
            OrderView()

        message = str(caught.value)
        assert 'OrderView.searchable_fields is the string "note"' in message
        assert 'searchable_fields = ["note"]' in message

    def test_something_that_is_not_a_column(self) -> None:
        class OrderView(ModelView[Order]):
            searchable_fields = [42]  # type: ignore[list-item]

        with pytest.raises(AdminSiteError, match="42 is not a column"):
            OrderView()

    def test_a_column_of_an_alias(self) -> None:
        other = aliased(Order)

        class OrderView(ModelView[Order]):
            fields = [other.total]

        with pytest.raises(AdminSiteError, match="belongs to an alias"):
            OrderView()

    def test_a_sort_by_a_column_of_another_model(self) -> None:
        class OrderView(ModelView[Order]):
            fields_default_sort = [Descending(Customer.name)]

        with pytest.raises(AdminSiteError, match=r"OrderView\.fields_default_sort"):
            OrderView()


class LinkedOrders(ModelView[Order]):
    fields = [Order.id, Link(Order.customer, Customer.name), Order.total]
    searchable_fields = [Link(Order.customer, Customer.email)]
    fields_default_sort = [Descending(Order.total)]


@pytest.fixture
async def client(backend: Backend) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(backend.database, views=[LinkedOrders, ModelView[Customer]])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestTheListReadsThem:
    async def test_it_searches_and_sorts_through_links(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders?q=rossi.it")

        assert page.status_code == 200
        assert "Marco Rossi" in page.text
        assert "Lena Fischer" not in page.text
        # Marco's orders, the larger total first: 118.00, then 72.00.
        assert page.text.index("118.00") < page.text.index("72.00")

    async def test_a_view_with_no_class_has_its_pages(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers")

        assert page.status_code == 200
        assert "Aisha Khan" in page.text
