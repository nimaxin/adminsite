"""A mistake no type checker sees stops the view when the admin starts.

Each message names the view, the setting, and what the setting could have
named instead.
"""

from typing import Any

import pytest

from adminsite import AdminSiteError, Descending, Field, Inline, ModelView
from adminsite.backends.sqlalchemy import SQLAlchemyInspector
from adminsite.fields import ComputedField, EnumField, RelationField
from tests.models import Customer, Order
from tests.reference import startup_mistakes
from tests.reference.startup_mistakes import EXPECTED


def line_count(order: Order) -> int:
    return len(order.items)


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

        assert Excluded()._exported(["id", "lines"]) == ("id",)

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

    def test_a_related_record_is_read_through_its_link(self) -> None:
        class Titled(ModelView[Order]):
            record_title = "{customer.name}, #{id}"

        order = Order(id=3, customer=Customer(name="Lena"))

        assert Titled().get_record_title(order) == "Lena, #3"

    def test_an_inline_s_is_checked_against_its_model(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [Inline("items", display_template="{qty}")]

        assert refusal(WithLines).startswith(
            'WithLines.inlines[0].display_template: "{qty}" reads {qty}, and '
            'OrderItem has no column or relationship "qty".'
        )

    def test_a_link_s_is_checked_against_the_model_it_links_to(self) -> None:
        class Linked(ModelView[Order]):
            fields = [
                Order.id,
                RelationField(Order.customer, display_template="{nmae}"),
            ]

        assert refusal(Linked).startswith(
            "Linked.fields: RelationField(Order.customer): its display_template "
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

    def test_a_deferred_column_is_the_model_s_own(self) -> None:
        class Deferred(ModelView[Order]):
            deferred_fields = [Order.customer]

        assert "takes the model's own columns" in refusal(Deferred)


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

    def test_a_choice_field_takes_its_choices_from_an_enum_column(self) -> None:
        class Statuses(ModelView[Order]):
            fields = [EnumField(Order.status)]

        assert Statuses()._field_for("status").display("PAID") == "Paid"
