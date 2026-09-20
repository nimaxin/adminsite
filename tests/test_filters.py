from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import ColumnElement, Select, select

from adminsite.backends.sqlalchemy import (
    BooleanFilter,
    ChoiceFilter,
    Database,
    DateRangeFilter,
    NumberRangeFilter,
    RelationFilter,
    SQLAlchemyRepository,
    SQLFilter,
    SQLFilterContext,
    TextFilter,
    filter_for,
)
from adminsite.exceptions import InvalidPathError
from adminsite.filters import FilterOption, FilterValue, parse_filters
from adminsite.query import QuerySpec
from tests.models import Customer, Order, OrderStatus

NOW = datetime(2026, 9, 19, 9, 0)


def picked(name: str, *values: str) -> FilterValue:
    return FilterValue(name, values)


def orders_with(*filters: SQLFilter) -> SQLAlchemyRepository:
    return SQLAlchemyRepository(Order, filters=filters)


class TestReadingFiltersFromARequest:
    def test_only_known_filters_are_read(self) -> None:
        status = ChoiceFilter("status", choices=(("PAID", "Paid"),))

        values = parse_filters([status], {"status": ["PAID"], "colour": ["green"]})

        assert values == (picked("status", "PAID"),)

    def test_empty_values_are_ignored(self) -> None:
        status = ChoiceFilter("status")

        assert parse_filters([status], {"status": ["", "  "]}) == ()

    def test_a_single_choice_filter_keeps_the_first_value(self) -> None:
        active = BooleanFilter("is_active")

        values = parse_filters([active], {"is_active": ["true", "false"]})

        assert values == (picked("is_active", "true"),)

    def test_a_multiple_choice_filter_keeps_them_all(self) -> None:
        status = ChoiceFilter("status")

        values = parse_filters([status], {"status": ["PAID", "SHIPPED"]})

        assert values == (picked("status", "PAID", "SHIPPED"),)


class TestChoiceFilter:
    async def test_it_narrows_the_list(self, database: Database) -> None:
        orders = orders_with(ChoiceFilter("status"))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("status", "SHIPPED"),))
            )

            assert {row.status for row in page} == {OrderStatus.SHIPPED}

    async def test_several_values_match_any_of_them(self, database: Database) -> None:
        orders = orders_with(ChoiceFilter("status"))
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(filters=(picked("status", "PAID", "REFUNDED"),)),
            )

            assert {row.status for row in page} == {
                OrderStatus.PAID,
                OrderStatus.REFUNDED,
            }

    async def test_the_total_counts_only_what_matched(self, database: Database) -> None:
        orders = orders_with(ChoiceFilter("status"))
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(limit=1, filters=(picked("status", "SHIPPED"),)),
            )

            assert page.total == 2

    async def test_options_carry_how_many_records_match(
        self, database: Database
    ) -> None:
        status = filter_for(orders_with(), "status")
        orders = orders_with(status)
        async with database.session() as session:
            context = SQLFilterContext(session, orders, QuerySpec())
            options = await status.options(context)

            counts = {option.value: option.count for option in options}
            assert counts == {
                "PENDING": 2,
                "PAID": 2,
                "SHIPPED": 2,
                "REFUNDED": 1,
            }

    async def test_option_labels_read_like_words(self, database: Database) -> None:
        status = filter_for(orders_with(), "status")
        orders = orders_with(status)
        async with database.session() as session:
            context = SQLFilterContext(session, orders, QuerySpec())
            options = await status.options(context)

            assert [option.label for option in options][:2] == ["Pending", "Paid"]

    async def test_counts_follow_the_search(self, database: Database) -> None:
        status = filter_for(orders_with(), "status")
        orders = orders_with(status)
        spec = QuerySpec(search="lena", search_paths=("customer.name",))
        async with database.session() as session:
            context = SQLFilterContext(session, orders, spec)
            options = await status.options(context)

            assert sum(option.count or 0 for option in options) == 2

    async def test_counts_can_be_switched_off(self, database: Database) -> None:
        status = ChoiceFilter("status", choices=(("PAID", "Paid"),), show_counts=False)
        async with database.session() as session:
            context = SQLFilterContext(session, orders_with(status), QuerySpec())
            options = await status.options(context)

            assert options[0].count is None


class TestOtherBuiltInFilters:
    async def test_a_boolean_filter_matches_either_side(
        self, database: Database
    ) -> None:
        customers = SQLAlchemyRepository(Customer, filters=[BooleanFilter("is_active")])
        async with database.session() as session:
            page = await customers.list(
                session, QuerySpec(filters=(picked("is_active", "true"),))
            )

            assert len(page) == 4

    async def test_a_number_range_matches_between_the_bounds(
        self, database: Database
    ) -> None:
        orders = orders_with(NumberRangeFilter("total"))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("total", "60,120"),))
            )

            assert all(Decimal("60") <= row.total <= Decimal("120") for row in page)
            assert len(page) >= 1

    async def test_a_number_range_can_be_open_ended(self, database: Database) -> None:
        orders = orders_with(NumberRangeFilter("total"))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("total", "100,"),))
            )

            assert all(row.total >= Decimal("100") for row in page)

    async def test_a_date_range_reads_a_shortcut(self, database: Database) -> None:
        created = DateRangeFilter("created_at", now=NOW)
        orders = orders_with(created)
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("created_at", "week"),))
            )

            assert all(row.created_at >= datetime(2026, 9, 12, 9, 0) for row in page)

    async def test_a_date_range_reads_two_dates(self, database: Database) -> None:
        orders = orders_with(DateRangeFilter("created_at", now=NOW))
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(filters=(picked("created_at", "2026-09-01,2026-09-06"),)),
            )

            assert {row.created_at.day for row in page} == {1, 4, 6}

    async def test_a_relation_filter_matches_linked_records(
        self, database: Database
    ) -> None:
        orders = orders_with(RelationFilter("customer"))
        async with database.session() as session:
            lena = await session.scalar(
                select(Customer).where(Customer.email == "lena@fischer.de")
            )
            assert lena is not None

            page = await orders.list(
                session, QuerySpec(filters=(picked("customer", str(lena.id)),))
            )

            assert len(page) == 2

    async def test_a_text_filter_matches_part_of_a_value(
        self, database: Database
    ) -> None:
        customers = SQLAlchemyRepository(Customer, filters=[TextFilter("email")])
        async with database.session() as session:
            page = await customers.list(
                session, QuerySpec(filters=(picked("email", "fischer"),))
            )

            assert [row.email for row in page] == ["lena@fischer.de"]


class TestChoosingAFilterForAColumn:
    def test_each_column_gets_the_filter_that_fits(self) -> None:
        orders = orders_with()

        assert isinstance(filter_for(orders, "status"), ChoiceFilter)
        assert isinstance(filter_for(orders, "total"), NumberRangeFilter)
        assert isinstance(filter_for(orders, "created_at"), DateRangeFilter)
        assert isinstance(filter_for(orders, "note"), TextFilter)
        assert isinstance(filter_for(orders, "customer"), RelationFilter)
        assert isinstance(
            filter_for(SQLAlchemyRepository(Customer), "is_active"), BooleanFilter
        )

    def test_the_label_comes_from_the_column(self) -> None:
        assert filter_for(orders_with(), "created_at").label == "Created at"

    def test_a_dotted_path_gets_a_name_that_fits_a_url(self) -> None:
        built = filter_for(orders_with(), "customer.region")

        assert built.name == "customer__region"
        assert built.path == "customer.region"

    async def test_a_filter_can_reach_through_a_relationship(
        self, database: Database
    ) -> None:
        region = filter_for(orders_with(), "customer.region")
        orders = orders_with(region)
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("customer__region", "DE"),))
            )

            assert len(page) == 2

    async def test_counting_options_needs_the_model_s_own_column(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            context = SQLFilterContext(session, orders_with(), QuerySpec())

            with pytest.raises(InvalidPathError, match="own columns"):
                await context.count_by("customer.region")


class TestCustomFilters:
    async def test_a_custom_filter_writes_its_own_condition(
        self, database: Database
    ) -> None:
        class BigOrderFilter(SQLFilter):
            """Orders worth more than the chosen amount."""

            def condition(
                self, value: FilterValue, repository: SQLAlchemyRepository
            ) -> ColumnElement[bool] | None:
                floor = Decimal(value.first)
                return Order.total > floor

        orders = orders_with(BigOrderFilter("big", label="Large orders"))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("big", "100"),))
            )

            assert all(row.total > Decimal("100") for row in page)
            assert len(page) < 7

    async def test_a_custom_filter_can_change_the_statement(
        self, database: Database
    ) -> None:
        class OnlyTheFirstTwo(SQLFilter):
            """Keeps the two oldest orders, whatever else is asked for."""

            def apply(
                self,
                statement: Select[Any],
                value: FilterValue,
                repository: SQLAlchemyRepository,
            ) -> Select[Any]:
                return statement.where(
                    Order.id.in_(select(Order.id).order_by(Order.id).limit(2))
                )

        orders = orders_with(OnlyTheFirstTwo("oldest"))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("oldest", "yes"),))
            )

            assert len(page) == 2

    async def test_custom_options_show_up_with_counts(self, database: Database) -> None:
        class DeliveryFilter(ChoiceFilter):
            """Groups orders by whether they have shipped."""

            def condition(
                self, value: FilterValue, repository: SQLAlchemyRepository
            ) -> ColumnElement[bool] | None:
                if value.first == "shipped":
                    return Order.status == OrderStatus.SHIPPED
                return Order.status != OrderStatus.SHIPPED

        delivery = DeliveryFilter(
            "delivery",
            choices=(("shipped", "On its way"), ("waiting", "Not yet sent")),
            show_counts=False,
        )
        orders = orders_with(delivery)
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("delivery", "waiting"),))
            )

            assert OrderStatus.SHIPPED not in {row.status for row in page}
            assert len(page) == 5


class TestChips:
    def test_a_chip_falls_back_to_the_raw_value(self) -> None:
        status = ChoiceFilter("status")

        chip = status.describe(picked("status", "PAID", "SHIPPED"))

        assert chip == "Status: PAID, SHIPPED"

    def test_a_chip_uses_the_labels_when_the_options_are_known(self) -> None:
        status = ChoiceFilter("status")
        options = [FilterOption("PAID", "Paid"), FilterOption("SHIPPED", "Shipped")]

        chip = status.describe(picked("status", "PAID"), options)

        assert chip == "Status: Paid"
