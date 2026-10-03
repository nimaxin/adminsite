"""Fields written with typed options, completed from their columns."""

import enum
from decimal import Decimal

import pytest

from adminsite import AdminSiteError, Field, Link, ModelView, ViewRegistry
from adminsite.fields import (
    ComputedField,
    DecimalField,
    EnumField,
    RelationField,
    StringField,
    TextAreaField,
)
from tests.models import Customer, Order, OrderStatus
from tests.support import request_from


def line_count(order: Order) -> int:
    return len(order.items)


def doubled(order: Order) -> Decimal:
    return order.total * 2


class TestAKindStartsFromItsColumn:
    def test_the_column_gives_what_the_options_leave_out(self) -> None:
        class Notes(ModelView[Order]):
            fields = [TextAreaField(Order.note, help_text="Seen by staff only.")]

        item = Notes()._fields.field_for("note")

        assert isinstance(item, TextAreaField)
        assert item.label == "Note"
        assert item.max_length == 500
        assert item.required is False
        assert item.help_text == "Seen by staff only."

    def test_an_option_given_wins_over_the_column(self) -> None:
        class Names(ModelView[Customer]):
            fields = [
                StringField(Customer.name, label="Full name", max_length=40),
                StringField(Customer.email, required=False),
            ]

        view = Names()

        assert view._fields.field_for("name").label == "Full name"
        assert view._fields.field_for("name").max_length == 40
        assert view._fields.field_for("name").required is True
        assert view._fields.field_for("email").required is False

    def test_the_field_in_the_view_stays_as_written(self) -> None:
        written = TextAreaField(Order.note)

        class Notes(ModelView[Order]):
            fields = [written]

        Notes()

        assert written.max_length is None
        assert written.required is None

    def test_a_column_named_by_its_name(self) -> None:
        class Notes(ModelView[Order]):
            fields = [TextAreaField("note")]

        assert Notes()._fields.field_for("note").max_length == 500


class TestFieldOnItsOwn:
    def test_it_is_the_kind_the_column_calls_for(self) -> None:
        class Chosen(ModelView[Order]):
            fields = [
                Field(Order.total, label="Amount", read_only=True),
                Field(Order.status, hidden_in_list=True),
                Field(Order.customer, label="Buyer"),
            ]

        view = Chosen()
        total = view._fields.field_for("total")
        status = view._fields.field_for("status")
        customer = view._fields.field_for("customer")

        assert type(total) is DecimalField
        assert (total.label, total.read_only) == ("Amount", True)
        assert type(status) is EnumField
        assert status.hidden_in_list
        assert status.enum is OrderStatus
        assert isinstance(customer, RelationField)
        assert customer.related_model is Customer
        assert customer.label == "Buyer"

    def test_read_only_keeps_the_value_from_being_changed(self) -> None:
        class Fixed(ModelView[Order]):
            fields = [Order.customer, Field(Order.total, read_only=True)]

        assert Fixed()._pages.readonly_paths(request_from()) == ("total",)

    def test_a_column_of_a_related_model(self) -> None:
        class Contact(ModelView[Order]):
            fields = [
                Order.id,
                Field(Link(Order.customer, Customer.email), format="<{}>"),
                Field(Link(Order.customer, Customer.name), label="Buyer"),
            ]

        view = Contact()

        assert view._pages.list_fields(request_from()) == (
            "id",
            "customer.email",
            "customer.name",
        )
        assert view._fields.field_for("customer.email").format == "<{}>"
        # Named by its path, as can_access_field reads it.
        assert view._fields.field_for("customer.email").name == "customer.email"
        # Without a label of its own, the column names the relation too.
        assert view._fields.label_for("customer.email") == "Customer email"
        assert view._fields.label_for("customer.name") == "Buyer"


class TestEnumField:
    def test_its_choices_come_from_the_column(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [EnumField(Order.status)]

        item = Statuses()._fields.field_for("status")

        assert isinstance(item, EnumField)
        assert item.parse("PAID") is OrderStatus.PAID

    def test_choices_given_for_a_string_column(self) -> None:
        class Regions(ModelView[Customer]):
            fields = [EnumField(Customer.region, choices=[("EU", "Europe")])]

        assert Regions()._fields.field_for("region").display("EU") == "Europe"

    def test_choices_on_an_enum_column_keep_its_enum(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [
                EnumField(
                    Order.status, choices=[("PENDING", "Waiting"), ("PAID", "Paid up")]
                )
            ]

        item = Statuses()._fields.field_for("status")

        assert isinstance(item, EnumField)
        assert item.enum is OrderStatus
        assert item.parse("PAID") is OrderStatus.PAID
        assert item.display(OrderStatus.PAID) == "Paid up"

    def test_a_choice_may_name_a_member_by_its_value(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [
                EnumField(
                    Order.status, choices=[("pending", "Waiting"), ("paid", "Paid up")]
                )
            ]

        item = Statuses()._fields.field_for("status")

        assert isinstance(item, EnumField)
        assert item.display(OrderStatus.PAID) == "Paid up"
        assert item.values_of(OrderStatus.PAID) == ("paid",)
        assert item.parse("paid") is OrderStatus.PAID

    def test_an_enum_on_a_string_column_is_stored_as_its_value(self) -> None:
        class Region(enum.Enum):
            GERMANY = "DE"
            ITALY = "IT"

        class Regions(ModelView[Customer]):
            fields = [EnumField(Customer.region, enum=Region)]

        item = Regions()._fields.field_for("region")

        assert isinstance(item, EnumField)
        assert item.choices == (("DE", "Germany"), ("IT", "Italy"))
        assert item.parse("DE") == "DE"
        assert type(item.parse("GERMANY")) is str
        assert item.display("IT") == "Italy"
        assert item.values_of("IT") == ("IT",)

    def test_a_tone_is_checked_once_the_choices_are_known(self) -> None:
        class Wrong(ModelView[Order]):
            fields = [EnumField(Order.status, tones={"lost": "rose"})]

        with pytest.raises(AdminSiteError, match="not one of its choices"):
            Wrong()


class TestComputedField:
    def test_needs_takes_attributes(self) -> None:
        class Lines(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count, needs=[Order.items])]

        assert Lines()._pages.loadable(["id", "lines"]) == ["id", "items"]

    def test_a_need_of_another_model(self) -> None:
        class Wrong(ModelView[Order]):
            fields = [ComputedField("lines", line_count, needs=[Customer.orders])]

        with pytest.raises(
            AdminSiteError, match=r"Wrong\.fields\[0\]\.needs: Customer\.orders is a"
        ):
            Wrong()

    def test_its_format_is_used(self) -> None:
        item = ComputedField("doubled", doubled, format="€{:,.2f}")

        assert item.text_for(Order(total=Decimal("600.25"))) == "€1,200.50"

    def test_it_takes_a_function_or_a_loader(self) -> None:
        with pytest.raises(AdminSiteError, match="one of the two"):
            ComputedField("lines")


class TestRelationField:
    def test_the_relationship_says_what_it_links_to(self) -> None:
        class Orders(ModelView[Order]):
            fields = [RelationField(Order.customer, record_title="{name}")]

        item = Orders()._fields.field_for("customer")

        assert isinstance(item, RelationField)
        assert item.related_model is Customer
        assert item.collection is False
        assert item.required is True
        assert item.record_title == "{name}"

    def test_a_link_to_many(self) -> None:
        class Customers(ModelView[Customer]):
            fields = [RelationField(Customer.orders)]

        item = Customers()._fields.field_for("orders")

        assert isinstance(item, RelationField)
        assert item.collection is True

    def test_the_view_it_opens_is_named_by_its_class(self) -> None:
        class CustomerView(ModelView[Customer]):
            pass

        class OrderView(ModelView[Order]):
            fields = [RelationField(Order.customer, view=CustomerView)]

        views = ViewRegistry()
        customers = views.add(CustomerView)
        orders = views.add(OrderView)
        item = orders._fields.field_for("customer")

        assert isinstance(item, RelationField)
        assert views.for_relation(item) is customers

    def test_a_view_that_is_not_registered(self) -> None:
        class CustomerView(ModelView[Customer]):
            pass

        class OrderView(ModelView[Order]):
            fields = [RelationField(Order.customer, view=CustomerView)]

        views = ViewRegistry()
        item = views.add(OrderView)._fields.field_for("customer")

        assert isinstance(item, RelationField)
        with pytest.raises(AdminSiteError, match="CustomerView, which is not regist"):
            views.for_relation(item)

    def test_another_kind_on_a_relationship(self) -> None:
        class Wrong(ModelView[Order]):
            fields = [StringField("customer")]

        with pytest.raises(
            AdminSiteError,
            match=r'Wrong\.fields: StringField\("customer"\) names a rel',
        ):
            Wrong()


class TestOptionsAreKeywords:
    def test_an_option_by_position_is_refused(self) -> None:
        with pytest.raises(TypeError):
            Field(Order.note, "Note")  # type: ignore[call-arg]

    def test_a_misspelt_option_is_refused(self) -> None:
        # Only Python 3.13 and later add "Did you mean 'label'?".
        with pytest.raises(TypeError, match="unexpected keyword argument 'lable'"):
            Field(Order.note, lable="Note")  # type: ignore[call-arg]
