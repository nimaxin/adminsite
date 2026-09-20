from datetime import datetime
from decimal import Decimal

import pytest

from adminsite import (
    InvalidPathError,
    NotAModelError,
    RelationDirection,
    UnknownFieldError,
)
from adminsite.backends.sqlalchemy import SQLAlchemyInspector
from adminsite.text import humanize, humanize_class, pluralize, snake_case
from tests.models import Customer, Order, OrderItem, OrderStatus, Product


@pytest.fixture
def inspector() -> SQLAlchemyInspector:
    return SQLAlchemyInspector()


class TestModelSchema:
    def test_names_the_model_for_people_and_for_urls(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        schema = inspector.inspect(OrderItem)

        assert schema.name == "order_item"
        assert schema.label == "Order item"
        assert schema.label_plural == "Order items"

    def test_lists_every_column(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Order)

        assert set(schema.fields) == {
            "id",
            "customer_id",
            "status",
            "total",
            "note",
            "created_at",
        }

    def test_finds_the_primary_key(self, inspector: SQLAlchemyInspector) -> None:
        assert inspector.inspect(Order).primary_key == ("id",)

    def test_reads_python_types(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Order)

        assert schema.field_named("id").python_type is int
        assert schema.field_named("total").python_type is Decimal
        assert schema.field_named("created_at").python_type is datetime

    def test_reads_an_enum_with_its_values(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        status = inspector.inspect(Order).field_named("status")

        assert status.enum_values == tuple(member.name for member in OrderStatus)
        assert status.python_type is OrderStatus

    def test_reads_string_length(self, inspector: SQLAlchemyInspector) -> None:
        assert inspector.inspect(Customer).field_named("email").max_length == 255

    def test_marks_nullable_columns(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Product)

        assert schema.field_named("description").nullable is True
        assert schema.field_named("name").nullable is False

    def test_marks_keys(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Order)

        assert schema.field_named("id").primary_key is True
        assert schema.field_named("customer_id").foreign_key is True
        assert schema.field_named("note").foreign_key is False

    def test_labels_drop_the_id_suffix(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Order)

        assert schema.field_named("customer_id").label == "Customer"
        assert schema.field_named("created_at").label == "Created at"

    def test_only_required_fields_are_required(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        schema = inspector.inspect(Order)

        assert schema.field_named("created_at").required is True
        assert schema.field_named("note").required is False
        assert schema.field_named("status").required is False
        assert schema.field_named("id").required is False

    def test_unknown_names_say_which_model_and_name(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        with pytest.raises(UnknownFieldError, match="Order has no field"):
            inspector.inspect(Order).field_named("nope")

    def test_a_plain_class_is_not_a_model(self, inspector: SQLAlchemyInspector) -> None:
        class Plain:
            pass

        with pytest.raises(NotAModelError, match="Plain is not a mapped model"):
            inspector.inspect(Plain)

    def test_the_same_model_is_read_once(self, inspector: SQLAlchemyInspector) -> None:
        assert inspector.inspect(Order) is inspector.inspect(Order)


class TestRelations:
    def test_reads_both_sides_of_a_relationship(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        order = inspector.inspect(Order).relation_named("customer")
        customer = inspector.inspect(Customer).relation_named("orders")

        assert order.direction is RelationDirection.MANY_TO_ONE
        assert order.target is Customer
        assert order.collection is False
        assert customer.direction is RelationDirection.ONE_TO_MANY
        assert customer.target is Order
        assert customer.collection is True

    def test_knows_which_column_holds_the_link(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        relation = inspector.inspect(Order).relation_named("customer")

        assert relation.local_columns == ("customer_id",)

    def test_labels_relationships(self, inspector: SQLAlchemyInspector) -> None:
        assert inspector.inspect(Order).relation_named("items").label == "Items"


class TestPaths:
    def test_resolves_a_plain_field(self, inspector: SQLAlchemyInspector) -> None:
        path = inspector.resolve(Order, "total")

        assert path.dotted == "total"
        assert path.relations == ()
        assert path.label == "Total"
        assert path.crosses_collection is False

    def test_resolves_through_a_relationship(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        path = inspector.resolve(Order, "customer.email")

        assert path.parts == ("customer", "email")
        assert path.relations[0].target is Customer
        assert path.label == "Email"
        assert path.crosses_collection is False

    def test_resolves_through_several_relationships(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        path = inspector.resolve(OrderItem, "order.customer.name")

        assert [relation.name for relation in path.relations] == [
            "order",
            "customer",
        ]
        assert path.field is not None
        assert path.field.name == "name"

    def test_knows_when_a_path_goes_through_many_rows(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        assert inspector.resolve(Customer, "orders.total").crosses_collection

    def test_a_path_can_end_on_a_relationship(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        path = inspector.resolve(Order, "customer")

        assert path.points_at_relation is True
        assert path.label == "Customer"

    def test_unknown_step_names_the_whole_path(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        with pytest.raises(UnknownFieldError, match=r"'customer\.nope'"):
            inspector.resolve(Order, "customer.nope")

    def test_a_field_cannot_have_more_path_after_it(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        with pytest.raises(InvalidPathError, match="is a field"):
            inspector.resolve(Order, "total.nope")

    def test_an_empty_path_is_rejected(self, inspector: SQLAlchemyInspector) -> None:
        with pytest.raises(InvalidPathError):
            inspector.resolve(Order, "")


class TestText:
    def test_humanize_reads_like_a_sentence(self) -> None:
        assert humanize("created_at") == "Created at"
        assert humanize("is_active") == "Is active"
        assert humanize("name") == "Name"

    def test_class_names_become_labels(self) -> None:
        assert humanize_class("OrderItem") == "Order item"
        assert humanize_class("HTTPLog") == "Http log"

    def test_plurals_cover_the_common_endings(self) -> None:
        assert pluralize("Order") == "Orders"
        assert pluralize("Address") == "Addresses"
        assert pluralize("Category") == "Categories"
        assert pluralize("Day") == "Days"

    def test_snake_case_matches_table_style(self) -> None:
        assert snake_case("OrderItem") == "order_item"
        assert snake_case("Order") == "order"
