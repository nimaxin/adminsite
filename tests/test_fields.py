from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from adminsite.backends.sqlalchemy import SQLAlchemyInspector
from adminsite.exceptions import FieldValidationError
from adminsite.fields import (
    BooleanField,
    ChoiceField,
    DateField,
    DateTimeField,
    DecimalField,
    Field,
    IntegerField,
    RelationField,
    StringField,
    TextField,
    TimeField,
    default_registry,
)
from tests.models import Customer, Order, OrderStatus, Product


@pytest.fixture
def inspector() -> SQLAlchemyInspector:
    return SQLAlchemyInspector()


class TestDisplay:
    def test_missing_values_show_as_nothing(self) -> None:
        assert StringField("note").display(None) == ""
        assert DecimalField("total").display(None) == ""
        assert DateField("day").display(None) == ""

    def test_amounts_keep_two_decimals_and_group_thousands(self) -> None:
        assert DecimalField("total").display(Decimal("1234.5")) == "1,234.50"

    def test_yes_and_no_instead_of_true_and_false(self) -> None:
        field = BooleanField("is_active")

        assert field.display(True) == "Yes"
        assert field.display(False) == "No"

    def test_dates_read_like_a_person_wrote_them(self) -> None:
        assert DateField("day").display(date(2026, 9, 8)) == "Sep 8, 2026"
        assert (
            DateTimeField("created_at").display(datetime(2026, 9, 8, 14, 5))
            == "Sep 8, 2026 14:05"
        )


class TestParsing:
    def test_text_comes_back_trimmed(self) -> None:
        assert StringField("name").parse("  Lena  ") == "Lena"

    def test_empty_input_becomes_nothing_when_allowed(self) -> None:
        assert StringField("note").parse("") is None
        assert StringField("note").parse("   ") is None

    def test_required_fields_say_so(self) -> None:
        with pytest.raises(FieldValidationError, match="required"):
            StringField("name", required=True).parse("")

    def test_numbers_are_converted(self) -> None:
        assert IntegerField("quantity").parse("12") == 12
        assert DecimalField("total").parse("12.50") == Decimal("12.50")

    def test_bad_numbers_explain_what_to_enter(self) -> None:
        with pytest.raises(FieldValidationError, match="whole number"):
            IntegerField("quantity").parse("twelve")

    def test_dates_and_times_are_converted(self) -> None:
        assert DateField("day").parse("2026-09-08") == date(2026, 9, 8)
        assert DateTimeField("created_at").parse("2026-09-08T14:05") == datetime(
            2026, 9, 8, 14, 5
        )

    def test_a_checkbox_that_is_not_sent_means_no(self) -> None:
        field = BooleanField("is_active")

        assert field.parse(None) is False
        assert field.parse("on") is True
        assert field.parse("true") is True

    def test_text_longer_than_the_column_is_refused(self) -> None:
        field = StringField("name", max_length=5)

        with pytest.raises(FieldValidationError, match="5 characters"):
            field.parse("far too long")


class TestRoundTrip:
    def test_values_survive_being_edited(self) -> None:
        cases: list[tuple[Field, Any]] = [
            (StringField("name"), "Lena"),
            (IntegerField("quantity"), 3),
            (DecimalField("total"), Decimal("59.00")),
            (DateField("day"), date(2026, 9, 8)),
            (DateTimeField("created_at"), datetime(2026, 9, 8, 14, 5)),
            (TimeField("at"), time(14, 5)),
        ]
        for field, value in cases:
            assert field.parse(field.serialize(value)) == value


class TestChoices:
    def test_options_come_from_the_enum(self, inspector: SQLAlchemyInspector) -> None:
        schema = inspector.inspect(Order).field_named("status")
        field = ChoiceField.from_schema(schema)

        assert field.choices == (
            ("PENDING", "Pending"),
            ("PAID", "Paid"),
            ("SHIPPED", "Shipped"),
            ("REFUNDED", "Refunded"),
        )

    def test_the_label_is_shown_not_the_stored_value(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        field = ChoiceField.from_schema(inspector.inspect(Order).field_named("status"))

        assert field.display(OrderStatus.SHIPPED) == "Shipped"

    def test_parsing_gives_back_the_enum_member(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        field = ChoiceField.from_schema(inspector.inspect(Order).field_named("status"))

        assert field.parse("PAID") is OrderStatus.PAID
        assert field.parse("paid") is OrderStatus.PAID

    def test_a_value_outside_the_list_is_refused(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        field = ChoiceField.from_schema(inspector.inspect(Order).field_named("status"))

        with pytest.raises(FieldValidationError, match="listed options"):
            field.parse("lost")


class TestRelations:
    def test_a_record_is_named_by_its_string_form(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        relation = inspector.inspect(Order).relation_named("customer")
        field = RelationField.from_relation(relation)

        assert field.display(Customer(name="Lena Fischer")) == "Lena Fischer"

    def test_a_display_template_decides_what_is_shown(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        relation = inspector.inspect(Order).relation_named("customer")
        field = RelationField.from_relation(
            relation, display_template="{name} ({email})"
        )
        customer = Customer(name="Lena Fischer", email="lena@fischer.de")

        assert field.display(customer) == "Lena Fischer (lena@fischer.de)"

    def test_many_records_are_listed(self, inspector: SQLAlchemyInspector) -> None:
        relation = inspector.inspect(Customer).relation_named("orders")
        field = RelationField.from_relation(relation)

        assert field.collection is True
        assert field.display([Order(id=1), Order(id=2)]) == "Order #1, Order #2"

    def test_chosen_keys_come_back_from_the_form(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        relation = inspector.inspect(Customer).relation_named("orders")
        field = RelationField.from_relation(relation)

        assert field.parse_many(["1", " 2 ", ""]) == ["1", "2"]
        assert field.parse_many(None) == []


class TestRegistry:
    def test_columns_get_the_field_that_fits(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        order = inspector.inspect(Order)
        customer = inspector.inspect(Customer)
        product = inspector.inspect(Product)

        assert isinstance(default_registry.build(order.field_named("id")), IntegerField)
        assert isinstance(
            default_registry.build(order.field_named("total")), DecimalField
        )
        assert isinstance(
            default_registry.build(order.field_named("created_at")), DateTimeField
        )
        assert isinstance(
            default_registry.build(order.field_named("status")), ChoiceField
        )
        assert isinstance(
            default_registry.build(customer.field_named("is_active")), BooleanField
        )
        assert isinstance(
            default_registry.build(customer.field_named("name")), StringField
        )
        assert isinstance(
            default_registry.build(product.field_named("description")), TextField
        )

    def test_a_long_column_is_edited_in_a_box(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        field = default_registry.build(
            inspector.inspect(Product).field_named("description")
        )

        assert field.widget == "textarea"

    def test_the_column_decides_label_and_requirement(
        self, inspector: SQLAlchemyInspector
    ) -> None:
        schema = inspector.inspect(Order)
        created_at = default_registry.build(schema.field_named("created_at"))
        note = default_registry.build(schema.field_named("note"))
        identifier = default_registry.build(schema.field_named("id"))

        assert created_at.label == "Created at"
        assert created_at.required is True
        assert note.required is False
        assert identifier.readonly is True
