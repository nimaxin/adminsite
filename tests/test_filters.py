import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import ColumnElement, Select, func, select
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ColumnReference, CountMode, ModelView
from adminsite.database import Database
from adminsite.filters import (
    BooleanFilter,
    ChoiceFilter,
    DateRangeFilter,
    FilterOption,
    FilterValue,
    NumberRangeFilter,
    RelationFilter,
    SQLAlchemyRepository,
    SQLFilter,
    TextFilter,
)
from adminsite.filters.base import parse_filters
from adminsite.filters.sql import SQLFilterContext, filter_for
from adminsite.query import QuerySpec
from tests.models import Customer, Order, OrderStatus
from tests.support import Backend, count_queries, counted

NOW = datetime(2026, 9, 19, 9, 0)


def picked(name: str, *values: str) -> FilterValue:
    return FilterValue(name, values)


def orders_with(*filters: SQLFilter[Any]) -> SQLAlchemyRepository[Order]:
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

    async def test_counts_and_values_stay_inside_the_scope(
        self, database: Database
    ) -> None:
        def scope(statement: Select[Any]) -> Select[Any]:
            # A scope may sort too, which a grouped count must not trip on.
            return statement.where(Order.status == OrderStatus.PAID).order_by(
                Order.created_at
            )

        async with database.session() as session:
            context = SQLFilterContext(session, orders_with(), QuerySpec(), scope)

            assert await context.count_by("status") == {"PAID": 2}
            assert await context.distinct("status") == [OrderStatus.PAID]

    async def test_nothing_is_counted_for_a_filter_whose_counts_are_hidden(
        self, backend: Backend
    ) -> None:
        status = filter_for(orders_with(), "status")
        async with backend.database.session() as session:
            context = SQLFilterContext(
                session, orders_with(status), QuerySpec(), counts=False
            )
            with count_queries(backend) as queries:
                options = await status.options(context)

        assert [option.count for option in options] == [None] * len(options)
        assert queries.count == 0


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

    async def test_a_path_it_cannot_count_gives_no_counts(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            context = SQLFilterContext(session, orders_with(), QuerySpec())

            assert await context.count_by("customer.region") == {}

    async def test_a_filter_through_a_relationship_still_offers_its_options(
        self, database: Database
    ) -> None:
        region = filter_for(orders_with(), "customer.region")
        async with database.session() as session:
            context = SQLFilterContext(session, orders_with(), QuerySpec())
            options = await region.options(context)

        assert [option.count for option in options] == [None] * len(options)


class TestCustomFilters:
    async def test_a_custom_filter_writes_its_own_condition(
        self, database: Database
    ) -> None:
        class BigOrderFilter(SQLFilter[Order]):
            """Orders worth more than the chosen amount."""

            def condition(
                self, value: FilterValue, repository: SQLAlchemyRepository[Order]
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
        class OnlyTheFirstTwo(SQLFilter[Order]):
            """Keeps the two oldest orders, whatever else is asked for."""

            def apply(
                self,
                statement: Select[Any],
                value: FilterValue,
                repository: SQLAlchemyRepository[Order],
            ) -> Select[Any]:
                # MySQL refuses a LIMIT straight inside IN (...), but takes
                # one inside a derived table.
                oldest = select(Order.id).order_by(Order.id).limit(2).subquery()
                return statement.where(Order.id.in_(select(oldest.c.id)))

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
                self, value: FilterValue, repository: SQLAlchemyRepository[Any]
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


class BigOrderFilter(SQLFilter[Order]):
    """Orders worth more than the chosen amount."""

    def condition(
        self, value: FilterValue, repository: SQLAlchemyRepository[Order]
    ) -> ColumnElement[bool] | None:
        return Order.total > Decimal(value.first)


class OrdersWithAFilterPerRequest(ModelView[Order]):
    """Adds a filter for this request only; list_filters does not name it."""

    name = "orders"
    fields = ["id", "total"]

    def get_list_filters(
        self, request: Request
    ) -> Sequence[ColumnReference | SQLFilter[Order]]:
        return [*super().get_list_filters(request), BigOrderFilter("big")]


class TestFiltersAddedPerRequest:
    async def test_one_only_get_list_filters_returns_still_narrows_the_list(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            expected = await session.scalar(
                select(func.count()).where(Order.total > Decimal("100"))
            )
        app = Starlette()
        app.mount("/admin", Admin(database, views=[OrdersWithAFilterPerRequest]))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            listing = await counted(client, "/admin/orders?big=100")
            export = await client.get("/admin/orders/export?big=100")

        total = re.search(r"(\d+) orders", listing)
        assert total is not None
        assert int(total.group(1)) == expected
        assert len(export.text.strip().splitlines()) - 1 == expected
        assert expected < 7


STATUSES = [("PAID", "Paid"), ("SHIPPED", "Shipped")]


class CountedOrders(ModelView[Order]):
    """Counts the options of its filters, as a view that counts exactly does."""

    name = "orders"
    fields = ["id", "status", "total"]
    searchable_fields = ["customer.name"]
    list_filters = [Order.status, Order.total]
    page_size = 2


class EstimatedOrders(CountedOrders):
    """Says its table is too big to count exactly."""

    count_mode = CountMode.ESTIMATED


class UncountedOrders(CountedOrders):
    """Says its table is too big to count at all."""

    count_mode = CountMode.NONE


class EstimatedOrdersCountingStatus(EstimatedOrders):
    """Counts the options of one filter all the same."""

    list_filters = [ChoiceFilter("status", choices=STATUSES, show_counts=True)]


class OrdersNotCountingStatus(CountedOrders):
    """Counts its records, but not the options of its filter."""

    list_filters = [ChoiceFilter("status", choices=STATUSES, show_counts=False)]


class CustomersNotCountingActive(ModelView[Customer]):
    """Counts its records, but not how many of them are active."""

    name = "customers"
    fields = ["name", "is_active"]
    list_filters = [BooleanFilter("is_active", show_counts=False)]


# What each list request sends: the whole page, the table HTMX redraws alone,
# and the filters drawer asking for its counts as it opens.
PAGE: dict[str, str] = {}
TABLE = {"HX-Request": "true"}
DRAWER = {"HX-Request": "true", "HX-Trigger": "filters"}


async def counts_run(
    backend: Backend,
    view: type[ModelView[Any]],
    address: str,
    headers: dict[str, str],
) -> tuple[str, list[str]]:
    """What one list request answers, and the statements it ran to count options."""
    app = Starlette()
    app.mount("/admin", Admin(backend.database, views=[view]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        with count_queries(backend) as queries:
            response = await client.get(address, headers=headers)

    assert response.status_code == 200
    return response.text, [
        item for item in queries.statements if "group by" in item.lower()
    ]


def shown_counts(answer: str) -> list[str]:
    """The counts beside the options in the filters drawer, or in its answer."""
    return re.findall(r'<span id="filter-[^"]*-count-\d+"[^>]*>(\d+)</span>', answer)


class TestWhereOptionsAreCounted:
    @pytest.mark.parametrize("headers", [PAGE, TABLE], ids=["page", "table"])
    async def test_the_list_itself_counts_none(
        self, backend: Backend, headers: dict[str, str]
    ) -> None:
        page, counting = await counts_run(
            backend, CountedOrders, "/admin/orders", headers
        )

        assert counting == []
        assert shown_counts(page) == []

    async def test_the_drawer_asks_for_them_as_it_opens(self, backend: Backend) -> None:
        page, _counting = await counts_run(
            backend, CountedOrders, "/admin/orders", PAGE
        )

        drawer = re.search(r'<div id="filters"[^>]*>', page)
        assert drawer is not None
        assert 'hx-trigger="toggle[filterCountsWanted(event)]"' in drawer.group(0)

    async def test_a_view_that_counts_exactly_counts_them_for_the_drawer(
        self, backend: Backend
    ) -> None:
        answer, counting = await counts_run(
            backend, CountedOrders, "/admin/orders", DRAWER
        )

        assert len(counting) == 1
        assert shown_counts(answer) == ["2", "2", "2", "1"]

    async def test_they_follow_the_search(self, backend: Backend) -> None:
        answer, _counting = await counts_run(
            backend, CountedOrders, "/admin/orders?q=lena", DRAWER
        )

        assert shown_counts(answer) == ["1", "1"]
        assert 'data-search="lena"' in answer

    @pytest.mark.parametrize(
        ("view", "address"),
        [
            (OrdersNotCountingStatus, "/admin/orders"),
            (CustomersNotCountingActive, "/admin/customers"),
        ],
    )
    async def test_a_filter_told_not_to_count_runs_no_count(
        self, backend: Backend, view: type[ModelView[Any]], address: str
    ) -> None:
        page, _counting = await counts_run(backend, view, address, PAGE)
        answer, counting = await counts_run(backend, view, address, DRAWER)

        assert 'hx-trigger="toggle[' not in page
        assert counting == []
        assert shown_counts(answer) == []

    @pytest.mark.parametrize("view", [EstimatedOrders, UncountedOrders])
    async def test_a_view_too_big_to_count_counts_no_options(
        self, backend: Backend, view: type[ModelView[Order]]
    ) -> None:
        page, _counting = await counts_run(backend, view, "/admin/orders", PAGE)
        answer, counting = await counts_run(backend, view, "/admin/orders", DRAWER)

        assert 'hx-trigger="toggle[' not in page
        assert counting == []
        assert shown_counts(answer) == []

    async def test_a_filter_can_ask_to_be_counted_all_the_same(
        self, backend: Backend
    ) -> None:
        answer, counting = await counts_run(
            backend, EstimatedOrdersCountingStatus, "/admin/orders", DRAWER
        )

        assert len(counting) == 1
        assert shown_counts(answer) == ["2", "2"]

    @pytest.mark.parametrize("view", [CountedOrders, EstimatedOrdersCountingStatus])
    @pytest.mark.parametrize(
        "address",
        [
            "/admin/orders?page=2",
            "/admin/orders?sort=-total",
            "/admin/orders?status=PAID",
        ],
    )
    async def test_the_table_redrawn_alone_counts_no_options(
        self, backend: Backend, view: type[ModelView[Order]], address: str
    ) -> None:
        _page, counting = await counts_run(backend, view, address, TABLE)

        assert counting == []

    async def test_the_chips_it_sends_still_read_the_labels(
        self, backend: Backend
    ) -> None:
        page, _counting = await counts_run(
            backend, CountedOrders, "/admin/orders?status=SHIPPED", TABLE
        )

        assert "Status: Shipped" in page


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


# More than an Integer column holds, which asyncpg refuses to send.
TOO_BIG = "99999999999"


class OrdersByCustomer(ModelView[Order]):
    name = "orders"
    fields = ["id", "status", "customer"]
    list_filters = ("status", "customer")


class CustomerView(ModelView[Customer]):
    name = "customers"


class TestValuesEditedByHand:
    """A filter's values come from the address, where anyone can change them."""

    async def test_a_choice_that_is_not_offered_matches_nothing(
        self, database: Database
    ) -> None:
        orders = orders_with(ChoiceFilter("status", choices=STATUSES))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("status", "REFUNDED"),))
            )

            assert len(page) == 0

    async def test_the_choices_that_are_offered_still_match(
        self, database: Database
    ) -> None:
        orders = orders_with(ChoiceFilter("status", choices=STATUSES))
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(filters=(picked("status", "PAID", "NOPE"),))
            )

            assert {row.status for row in page} == {OrderStatus.PAID}

    async def test_an_enum_column_leaves_out_a_name_it_does_not_have(
        self, database: Database
    ) -> None:
        orders = orders_with(ChoiceFilter("status"))
        async with database.session() as session:
            nothing = await orders.list(
                session, QuerySpec(filters=(picked("status", "NOPE"),))
            )
            paid = await orders.list(
                session, QuerySpec(filters=(picked("status", "NOPE", "PAID"),))
            )

            assert len(nothing) == 0
            assert {row.status for row in paid} == {OrderStatus.PAID}

    async def test_a_key_past_what_the_column_holds_names_no_record(
        self, database: Database
    ) -> None:
        orders = orders_with(RelationFilter("customer"))
        async with database.session() as session:
            lena = await session.scalar(
                select(Customer).where(Customer.email == "lena@fischer.de")
            )
            assert lena is not None
            nothing = await orders.list(
                session, QuerySpec(filters=(picked("customer", TOO_BIG),))
            )
            hers = await orders.list(
                session,
                QuerySpec(filters=(picked("customer", TOO_BIG, str(lena.id)),)),
            )

            assert len(nothing) == 0
            assert len(hers) == 2

    def test_the_condition_asks_only_for_what_the_column_holds(self) -> None:
        orders = SQLAlchemyRepository(Order)
        customer = RelationFilter("customer")
        status = ChoiceFilter("status")

        keys = customer.condition(picked("customer", TOO_BIG, "3"), orders)
        names = status.condition(picked("status", "NOPE"), orders)

        assert keys is not None
        assert names is not None
        assert list(keys.compile().params.values()) == [[3]]
        assert names.compile().params == {}

    @pytest.mark.parametrize(
        "address",
        [
            "/admin/orders?status=NOPE",
            f"/admin/orders?customer={TOO_BIG}",
            f"/admin/orders?customer=-{TOO_BIG}",
            "/admin/orders?customer=abc",
        ],
    )
    async def test_the_list_shows_no_rows_rather_than_failing(
        self, database: Database, address: str
    ) -> None:
        async with listing(database) as client:
            page = await client.get(address)

        assert page.status_code == 200
        assert "No orders to show." in page.text

    async def test_the_offered_values_still_narrow_the_list(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            paid = await session.scalar(
                select(func.count()).where(Order.status == OrderStatus.PAID)
            )
        async with listing(database) as client:
            page = await counted(client, "/admin/orders?status=PAID&status=NOPE")

        assert f"{paid} orders" in page

    async def test_a_record_past_what_the_key_holds_is_not_found(
        self, database: Database
    ) -> None:
        async with listing(database) as client:
            page = await client.get(f"/admin/orders/{TOO_BIG}")

        assert page.status_code == 404

    async def test_a_search_for_a_number_past_what_the_key_holds_finds_nothing(
        self, database: Database
    ) -> None:
        class SearchedOrders(OrdersByCustomer):
            searchable_fields = ("id",)

        async with listing(database, SearchedOrders) as client:
            page = await client.get(f"/admin/orders?q={TOO_BIG}")

        assert page.status_code == 200
        assert "No orders to show." in page.text


def listing(
    database: Database, view: type[ModelView[Order]] = OrdersByCustomer
) -> httpx.AsyncClient:
    """A client for an admin of orders and their customers."""
    app = Starlette()
    app.mount("/admin", Admin(database, views=[view, CustomerView]))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
