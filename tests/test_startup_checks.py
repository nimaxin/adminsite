"""A mistake no type checker sees stops the view when the admin starts.

Each message names the view, the setting, and what the setting could have
named instead.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import CHAR, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, UserDefinedType

from adminsite import AdminSiteError, Descending, Field, Inline, ModelView
from adminsite.backends.sqlalchemy import SQLAlchemyInspector
from adminsite.fields import (
    BooleanField,
    ComputedField,
    DateField,
    DateTimeField,
    EnumField,
    RelationField,
    StringField,
    UUIDField,
)
from tests.models import Customer, Order, OrderItem
from tests.reference import startup_mistakes
from tests.reference.startup_mistakes import EXPECTED
from tests.support import request_from


def line_count(order: Order) -> int:
    return len(order.items)


class CustomTypes(DeclarativeBase):
    pass


class TZDateTime(TypeDecorator[datetime]):
    """A datetime kept in UTC, as SQLAlchemy's own recipe writes it."""

    impl = DateTime
    cache_ok = True


class GUID(TypeDecorator[UUID]):
    """A UUID kept as 32 characters, as SQLAlchemy's own recipe writes it."""

    impl = CHAR(32)
    cache_ok = True


class NamesNothing(TypeDecorator[datetime]):
    """Raises for its python type, as SQLAlchemy 2.0 does for a TypeDecorator."""

    impl = DateTime
    cache_ok = True

    @property
    def python_type(self) -> type[Any]:
        raise NotImplementedError


class Ticket(UserDefinedType[UUID]):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "CHAR(32)"


class Event(CustomTypes):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(TZDateTime)
    token: Mapped[UUID] = mapped_column(GUID)
    ends_at: Mapped[datetime] = mapped_column(NamesNothing)
    ticket: Mapped[UUID] = mapped_column(Ticket)


def refusal(view: type[ModelView[Any]]) -> str:
    """The message a view is refused with, built as the admin builds it."""
    with pytest.raises(AdminSiteError) as raised:
        view(SQLAlchemyInspector())
    return str(raised.value)


@pytest.mark.parametrize(
    "view", [pytest.param(view, id=view.__name__) for view in EXPECTED]
)
def test_each_mistake_of_the_reference_is_refused(view: type[ModelView[Any]]) -> None:
    message = refusal(view)

    assert view.__name__ in message
    for word in EXPECTED[view]:
        assert word in message


def test_the_reference_lists_each_of_its_views() -> None:
    written = {
        value
        for value in vars(startup_mistakes).values()
        if isinstance(value, type)
        and issubclass(value, ModelView)
        and value.__module__ == startup_mistakes.__name__
    }

    assert written == set(EXPECTED)


class TestAName:
    def test_a_misspelt_one_lists_the_columns_there_are(self) -> None:
        class Sorted(ModelView[Order]):
            fields_default_sort = ["-creatd_at"]

        message = refusal(Sorted)

        assert (
            'Sorted.fields_default_sort: Order has no column or relationship "cr'
            in (message)
        )
        assert "Its columns: id, customer_id, status, total, note, created_at." in (
            message
        )

    def test_one_through_a_relationship_names_the_related_model(self) -> None:
        class Searched(ModelView[Order]):
            searchable_fields = ["customer.nmae"]

        assert refusal(Searched) == (
            'Searched.searchable_fields: "customer.nmae": Customer has no column or '
            'relationship "nmae". Its columns: id, name, email, region, is_active. '
            "Its relationships: orders."
        )

    def test_the_view_s_own_fields_are_listed_where_they_count(self) -> None:
        class Excluded(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count)]
            exclude_fields_from_export = ["line"]

        assert "The view's own fields: lines." in refusal(Excluded)

    def test_one_of_the_view_s_own_fields_is_taken_where_they_count(self) -> None:
        class Excluded(ModelView[Order]):
            fields = ["lines", Order.id, ComputedField("lines", line_count)]
            exclude_fields_from_export = ["lines"]

        assert Excluded()._pages.exported(["id", "lines"], request_from()) == ("id",)

    def test_a_filter_is_checked(self) -> None:
        class Filtered(ModelView[Order]):
            list_filters = ["stauts"]

        assert (
            'Filtered.list_filters: Order has no column or relationship "stauts"'
            in (refusal(Filtered))
        )

    def test_what_a_computed_field_needs_is_checked(self) -> None:
        class Needing(ModelView[Order]):
            fields = [ComputedField("lines", line_count, needs=["itms"])]

        assert (
            'Needing.fields[0].needs: Order has no column or relationship "itms"'
            in (refusal(Needing))
        )

    def test_an_inline_is_checked(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [Inline("itms")]

        assert 'WithLines.inlines[0]: Order has no column or relationship "itms"' in (
            refusal(WithLines)
        )


class TestARecordTitle:
    def test_a_misspelt_name_lists_the_columns_there_are(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "Order #{nmae}"

        assert refusal(Titled) == (
            'Titled.record_title: "Order #{nmae}" reads {nmae}, and Order has no '
            'column or relationship "nmae". Its columns: id, customer_id, status, '
            "total, note, created_at. Its relationships: customer, items."
        )

    def test_one_str_format_cannot_read_is_refused(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "Order #{id"

        assert refusal(Titled).startswith(
            'Titled.record_title: "Order #{id" cannot be read: '
        )

    def test_braces_with_no_name_are_refused(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "Order #{}"

        assert "cannot be read: {} names no column." in refusal(Titled)

    def test_a_relationship_is_refused(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "{customer.name}, #{id}"

        assert refusal(Titled) == (
            'Titled.record_title: "{customer.name}, #{id}" reads through the '
            'relationship "customer". A record_title reads Order\'s own columns, '
            "since every page naming a record would otherwise load its links too. "
            "Its columns: id, customer_id, status, total, note, created_at."
        )

    def test_a_relationship_on_its_own_is_refused(self) -> None:
        class Titled(ModelView[Customer]):
            record_title = "{name}: {orders}"

        assert refusal(Titled).startswith(
            'Titled.record_title: "{name}: {orders}" reads through the '
            'relationship "orders".'
        )

    def test_an_inline_s_relationship_is_refused(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [Inline(Order.items, record_title="{product.name}")]

        assert refusal(WithLines) == (
            'WithLines.inlines[0].record_title: "{product.name}" reads through the '
            'relationship "product". A record_title reads OrderItem\'s own '
            "columns, since every page naming a record would otherwise load its "
            "links too. Its columns: id, order_id, product_id, quantity, unit_price."
        )

    def test_a_link_s_relationship_is_refused(self) -> None:
        class Linked(ModelView[Order]):
            fields = [
                Order.id,
                RelationField(Order.customer, record_title="{orders}"),
            ]

        assert refusal(Linked).startswith(
            "Linked.fields: RelationField(Order.customer): its record_title "
            '"{orders}" reads through the relationship "orders". A record_title '
            "reads Customer's own columns"
        )

    def test_a_column_s_own_attribute_is_read(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "#{id} of {created_at.year}"

        order = Order(id=3, created_at=datetime(2026, 5, 1))

        assert Titled().get_record_title(order) == "#3 of 2026"

    def test_an_inline_s_is_checked_against_its_model(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [Inline("items", record_title="{qty}")]

        assert refusal(WithLines).startswith(
            'WithLines.inlines[0].record_title: "{qty}" reads {qty}, and '
            'OrderItem has no column or relationship "qty".'
        )

    def test_a_link_s_is_checked_against_the_model_it_links_to(self) -> None:
        class Linked(ModelView[Order]):
            fields = [
                Order.id,
                RelationField(Order.customer, record_title="{nmae}"),
            ]

        assert refusal(Linked).startswith(
            "Linked.fields: RelationField(Order.customer): its record_title "
            '"{nmae}" reads {nmae}, and Customer has no column or relationship '
            '"nmae".'
        )


class TestWhatASettingTakes:
    def test_a_search_takes_columns_not_a_relationship(self) -> None:
        class Searched(ModelView[Order]):
            searchable_fields = [Order.customer]

        assert refusal(Searched) == (
            'Searched.searchable_fields: "customer" is a relationship, and '
            "searchable_fields takes columns. Name a column of Customer, such as "
            '"customer.name".'
        )

    def test_a_search_takes_no_computed_field(self) -> None:
        class Searched(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count)]
            searchable_fields = ["lines"]

        assert refusal(Searched) == (
            'Searched.searchable_fields: ComputedField("lines") is a field of the '
            "view, not a column of Order, and searchable_fields takes columns."
        )

    def test_no_list_is_sorted_through_a_relationship_holding_many(self) -> None:
        class Sorted(ModelView[Customer]):
            fields_default_sort = [Descending("orders.total")]

        assert "goes through a relationship holding many records" in refusal(Sorted)

    def test_a_sort_takes_a_column_the_model_has(self) -> None:
        class Sortable(ModelView[Order]):
            sortable_fields = ["totl"]

        assert refusal(Sortable) == (
            'Sortable.sortable_fields: Order has no column or relationship "totl". '
            "Its columns: id, customer_id, status, total, note, created_at. "
            "Its relationships: customer, items."
        )

    def test_a_sort_takes_columns_not_a_relationship(self) -> None:
        class Sortable(ModelView[Order]):
            sortable_fields = [Order.customer]

        class Sorted(ModelView[Order]):
            fields_default_sort = [Order.customer]

        assert refusal(Sortable) == (
            'Sortable.sortable_fields: "customer" is a relationship, and '
            "sortable_fields takes columns. Name a column of Customer, such as "
            '"customer.name".'
        )
        assert refusal(Sorted) == (
            'Sorted.fields_default_sort: "customer" is a relationship, and '
            "fields_default_sort takes columns. Name a column of Customer, such as "
            '"customer.name".'
        )

    def test_no_list_is_sortable_through_a_relationship_holding_many(self) -> None:
        class Sortable(ModelView[Customer]):
            sortable_fields = ["orders.total"]

        assert refusal(Sortable) == (
            'Sortable.sortable_fields: "orders.total" goes through a relationship '
            "holding many records, so no list can be sorted by it."
        )

    def test_a_sort_takes_no_computed_field(self) -> None:
        class Sortable(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count)]
            sortable_fields = ["lines"]

        class Sorted(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count)]
            fields_default_sort = ["lines"]

        assert refusal(Sortable) == (
            'Sortable.sortable_fields: ComputedField("lines") is a field of the '
            "view, not a column of Order, and sortable_fields takes columns."
        )
        assert refusal(Sorted) == (
            'Sorted.fields_default_sort: ComputedField("lines") is a field of the '
            "view, not a column of Order, and fields_default_sort takes columns."
        )

    def test_a_deferred_column_is_the_model_s_own(self) -> None:
        class Deferred(ModelView[Order]):
            deferred_fields = [Order.customer]

        assert "takes the model's own columns" in refusal(Deferred)


class TestAnExcludeList:
    def test_a_key_shown_as_its_relationship_names_the_relationship(self) -> None:
        class Listed(ModelView[Order]):
            exclude_fields_from_list = [Order.customer_id]

        assert refusal(Listed) == (
            'Listed.exclude_fields_from_list: "customer_id" is shown as its '
            'relationship "customer". Name "customer".'
        )

    def test_a_field_the_view_does_not_show_is_refused(self) -> None:
        class Exported(ModelView[Order]):
            fields = [Order.id, Order.status]
            exclude_fields_from_export = [Order.note]

        assert refusal(Exported) == (
            'Exported.exclude_fields_from_export: "note" is not among the fields '
            "the view shows, so leaving it off does nothing. The view's fields: "
            "id, status."
        )

    def test_an_inline_leaves_the_link_back_off_only_where_it_shows(self) -> None:
        class Every(ModelView[Order]):
            inlines = [Inline(Order.items)]

        class Chosen(ModelView[Order]):
            inlines = [Inline(Order.items, fields=[OrderItem.product])]

        assert "order" not in Every()._inline_views["items"]._pages.form_fields(
            request_from()
        )
        assert Chosen()._inline_views["items"]._pages.form_fields(request_from()) == (
            "product",
        )


class TestAField:
    def test_hidden_in_the_list_while_the_view_leaves_it_off(self) -> None:
        class Listed(ModelView[Order]):
            fields = [Order.id, Field(Order.note, hidden_in_list=True)]
            exclude_fields_from_list = ["note"]

        assert refusal(Listed).startswith(
            "Listed.fields: Field(Order.note) has hidden_in_list=True, and "
            "exclude_fields_from_list names it."
        )

    def test_a_tone_is_checked_against_the_column_s_enum(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [EnumField(Order.status, tones={"LOST": "rose"})]

        assert refusal(Statuses) == (
            "Statuses.fields: EnumField(Order.status): its tones give a colour to "
            '"LOST", which is not one of its choices: PENDING, PAID, SHIPPED, '
            "REFUNDED."
        )

    def test_a_kind_on_a_relationship_is_refused(self) -> None:
        class Wrong(ModelView[Order]):
            fields = [StringField("customer")]

        assert refusal(Wrong) == (
            'Wrong.fields: StringField("customer") names a relationship. Show it '
            "with RelationField, or name it in fields without a field."
        )

    def test_a_choice_field_takes_its_choices_from_an_enum_column(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [EnumField(Order.status)]

        assert Statuses()._fields.field_for("status").display("PAID") == "Paid"

    def test_one_in_an_inline_names_the_view_and_the_setting(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [Inline(Order.items, fields=[EnumField(OrderItem.quantity)])]

        message = refusal(WithLines)

        assert message.startswith(
            "WithLines.inlines[0].fields: EnumField(OrderItem.quantity) has "
            "nothing to choose from."
        )
        assert "OrderItemInline" not in message

    def test_a_choice_names_a_member_of_the_column_s_enum(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [EnumField(Order.status, choices=[("PIAD", "Paid")])]

        assert refusal(Statuses) == (
            'Statuses.fields: EnumField(Order.status): its choices name "PIAD", '
            "which is not a member of OrderStatus. Name a member by its name or "
            "its value: PENDING, PAID, SHIPPED, REFUNDED."
        )


class TestAKindFitsItsColumn:
    def test_relation_field_on_a_foreign_key_names_the_relationship(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, RelationField(Order.customer_id)]

        assert refusal(Orders) == (
            "Orders.fields: RelationField(Order.customer_id) names a column, and "
            "RelationField shows a relationship. Write RelationField(Order.customer)."
        )

    def test_one_named_by_a_string_is_answered_by_a_string(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, RelationField("customer_id")]

        assert refusal(Orders).endswith('Write RelationField("customer").')

    def test_relation_field_on_another_column_lists_the_relationships(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, RelationField(Order.note)]

        assert refusal(Orders).endswith(
            "Name one of Order's relationships: customer, items."
        )

    def test_a_kind_named_by_a_string_is_checked_against_the_column(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, BooleanField("created_at")]

        assert refusal(Orders) == (
            'Orders.fields: BooleanField("created_at") is for bool values, and '
            "Order.created_at holds datetime. Use DateTimeField, or name the "
            "column without a field."
        )

    def test_a_kind_named_by_its_attribute_is_left_to_the_type_checker(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, BooleanField(Order.created_at)]  # type: ignore[arg-type]

        view = Orders(SQLAlchemyInspector())

        assert isinstance(view._fields.field_for("created_at"), BooleanField)

    def test_a_narrower_type_or_a_kind_for_any_value_fits(self) -> None:
        class Orders(ModelView[Order]):
            fields = [
                Order.id,
                DateField("created_at"),
                EnumField("note", choices=[("gift", "Gift")]),
            ]

        view = Orders()

        assert isinstance(view._fields.field_for("created_at"), DateField)
        assert isinstance(view._fields.field_for("note"), EnumField)


class TestAColumnOfATypeOfItsOwn:
    """Its type names no python type to check a kind against."""

    def kinds(self, view: type[ModelView[Any]]) -> dict[str, str]:
        built = view(SQLAlchemyInspector())
        return {
            path: type(built._fields.field_for(path)).__name__
            for path in ("starts_at", "token", "ends_at", "ticket")
        }

    def test_named_by_its_attribute(self) -> None:
        class Events(ModelView[Event]):
            fields = [
                Event.id,
                DateTimeField(Event.starts_at),
                UUIDField(Event.token),
                DateTimeField(Event.ends_at),
                UUIDField(Event.ticket),
            ]

        assert self.kinds(Events) == {
            "starts_at": "DateTimeField",
            "token": "UUIDField",
            "ends_at": "DateTimeField",
            "ticket": "UUIDField",
        }

    def test_named_by_a_string(self) -> None:
        class Events(ModelView[Event]):
            fields = [
                "id",
                DateTimeField("starts_at"),
                UUIDField("token"),
                DateTimeField("ends_at"),
                UUIDField("ticket"),
            ]

        assert self.kinds(Events) == {
            "starts_at": "DateTimeField",
            "token": "UUIDField",
            "ends_at": "DateTimeField",
            "ticket": "UUIDField",
        }

    def test_relation_field_is_still_refused(self) -> None:
        class Events(ModelView[Event]):
            fields = [Event.id, RelationField("starts_at")]

        assert refusal(Events).startswith(
            'Events.fields: RelationField("starts_at") names a column, and '
            "RelationField shows a relationship."
        )


class TestAnOptionTheKindNeverReads:
    def test_it_is_refused(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, DateTimeField(Order.created_at, max_length=5)]

        assert refusal(Orders) == (
            "Orders.fields: DateTimeField(Order.created_at) has no use for "
            "max_length=5. Leave it out."
        )

    def test_field_on_its_own_names_the_kind_it_became(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, Field(Order.status, max_length=10)]

        assert refusal(Orders) == (
            "Orders.fields: Field(Order.status) becomes EnumField, which has no use "
            "for max_length=10. Leave it out."
        )

    def test_a_computed_field_takes_no_form_options(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count, read_only=True)]

        assert "has no use for read_only=True" in refusal(Orders)

    def test_an_option_written_as_its_default_is_taken(self) -> None:
        class Orders(ModelView[Order]):
            fields = [Order.id, ComputedField("lines", line_count, read_only=False)]

        assert not Orders()._fields.field_for("lines").read_only
