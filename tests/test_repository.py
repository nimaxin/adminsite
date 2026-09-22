from decimal import Decimal

import pytest

from adminsite.backends.sqlalchemy import Database, SQLAlchemyInspector
from adminsite.backends.sqlalchemy.loader import build_load_options
from adminsite.backends.sqlalchemy.repository import SQLAlchemyRepository
from adminsite.exceptions import InvalidPathError
from adminsite.query import CountMode, QuerySpec, Sort
from tests.models import Customer, Order, OrderItem
from tests.support import Backend, count_queries


@pytest.fixture
def orders() -> SQLAlchemyRepository:
    return SQLAlchemyRepository(Order)


@pytest.fixture
def customers() -> SQLAlchemyRepository:
    return SQLAlchemyRepository(Customer)


class TestReadingPages:
    async def test_a_page_holds_the_rows_and_the_total(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, QuerySpec(limit=3))

            assert len(page) == 3
            assert page.total == 7
            assert page.has_next is True
            assert page.has_previous is False

    async def test_the_second_page_follows_the_first(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            spec = QuerySpec(limit=3, sort=(Sort("id"),))
            first = await orders.list(session, spec)
            second = await orders.list(session, spec.page(2))

            assert second.has_previous is True
            assert second.first_position == 4
            assert [row.id for row in first] != [row.id for row in second]

    async def test_the_last_page_says_there_is_no_next(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, QuerySpec(limit=3).page(3))

            assert len(page) == 1
            assert page.has_next is False

    async def test_counting_can_be_switched_off(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, QuerySpec(limit=3, count=CountMode.NONE))

            assert page.total is None
            assert page.has_next is True
            assert len(page) == 3

    async def test_without_counting_one_query_reads_the_page(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            with count_queries(backend) as queries:
                await orders.list(session, QuerySpec(limit=3, count=CountMode.NONE))

            assert queries.count == 1


class TestSorting:
    async def test_rows_come_back_in_the_order_asked_for(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            ascending = await orders.list(session, QuerySpec(sort=(Sort("total"),)))
            descending = await orders.list(
                session, QuerySpec(sort=(Sort("total", descending=True),))
            )

            totals = [row.total for row in ascending]
            assert totals == sorted(totals)
            assert descending.rows[0].total == max(totals)

    async def test_sorting_can_follow_a_relationship(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(paths=("customer.name",), sort=(Sort("customer.name"),)),
            )

            names = [row.customer.name for row in page]
            assert names == sorted(names)

    async def test_two_sorts_are_applied_in_order(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(
                    paths=("customer.name",),
                    sort=(Sort("customer.name"), Sort("total", descending=True)),
                ),
            )

            pairs = [(row.customer.name, -row.total) for row in page]
            assert pairs == sorted(pairs)

    async def test_sorting_through_many_rows_is_refused(
        self, database: Database, customers: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            with pytest.raises(InvalidPathError, match="many records"):
                await customers.list(session, QuerySpec(sort=(Sort("orders.total"),)))

    async def test_sorting_needs_a_field(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            with pytest.raises(InvalidPathError, match="needs a field"):
                await orders.list(session, QuerySpec(sort=(Sort("customer"),)))


class TestSearching:
    async def test_text_is_matched_anywhere_in_the_value(
        self, database: Database, customers: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await customers.list(
                session,
                QuerySpec(search="fisch", search_paths=("name", "email")),
            )

            assert [row.name for row in page] == ["Lena Fischer"]

    async def test_search_looks_in_every_listed_path(
        self, database: Database, customers: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await customers.list(
                session,
                QuerySpec(search="berg.se", search_paths=("name", "email")),
            )

            assert [row.email for row in page] == ["jonas@berg.se"]

    async def test_search_can_follow_a_relationship(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(search="lena", search_paths=("customer.name",)),
            )

            assert len(page) == 2

    async def test_search_can_reach_through_many_rows(
        self, database: Database, customers: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await customers.list(
                session,
                QuerySpec(search="refunded", search_paths=("orders.status",)),
            )

            assert [row.name for row in page] == ["Aisha Khan"]

    async def test_numbers_are_matched_exactly(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            first = (await orders.list(session, QuerySpec(limit=1))).rows[0]
            page = await orders.list(
                session, QuerySpec(search=str(first.id), search_paths=("id",))
            )

            assert [row.id for row in page] == [first.id]

    async def test_a_term_that_fits_nothing_matches_nothing(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session, QuerySpec(search="not a number", search_paths=("id",))
            )

            assert len(page) == 0
            assert page.total == 0

    async def test_the_total_counts_what_the_search_matched(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(limit=1, search="lena", search_paths=("customer.name",)),
            )

            assert page.total == 2


class TestEagerLoading:
    async def test_a_to_one_path_is_loaded_with_the_page(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            page = await orders.list(
                session,
                QuerySpec(paths=("id", "customer.name"), count=CountMode.NONE),
            )

            with count_queries(backend) as queries:
                names = [row.customer.name for row in page]

            assert len(names) == 7
            assert queries.count == 0

    async def test_a_collection_path_costs_one_extra_query(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            with count_queries(backend) as queries:
                page = await orders.list(
                    session,
                    QuerySpec(paths=("items.quantity",), count=CountMode.NONE),
                )
                totals = [len(row.items) for row in page]

            assert sum(totals) == 9
            assert queries.count == 2

    async def test_a_deep_path_is_loaded_too(self, backend: Backend) -> None:
        items = SQLAlchemyRepository(OrderItem)
        async with backend.database.session() as session:
            page = await items.list(
                session,
                QuerySpec(
                    paths=("order.customer.name", "product.name"),
                    count=CountMode.NONE,
                    limit=None,
                ),
            )

            with count_queries(backend) as queries:
                names = {row.order.customer.name for row in page}
                products = {row.product.name for row in page}

            assert len(names) == 4
            assert len(products) == 3
            assert queries.count == 0

    def test_the_loader_only_walks_relationships(self) -> None:
        inspector = SQLAlchemyInspector()

        assert build_load_options(inspector, Order, ("id", "total")) == []
        assert len(build_load_options(inspector, Order, ("customer.name",))) == 1

    def test_paths_sharing_a_relationship_load_once(self) -> None:
        inspector = SQLAlchemyInspector()

        options = build_load_options(
            inspector, Order, ("customer.name", "customer.email")
        )

        assert len(options) == 1


class TestSingleRecords:
    async def test_a_record_is_loaded_by_key(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, QuerySpec(limit=1))
            wanted = page.rows[0]

            found = await orders.get(session, wanted.id)

            assert found is not None
            assert found.id == wanted.id

    async def test_a_missing_key_gives_nothing(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            assert await orders.get(session, 9999) is None

    async def test_a_record_can_be_loaded_with_its_links(
        self, backend: Backend, orders: SQLAlchemyRepository
    ) -> None:
        async with backend.database.session() as session:
            page = await orders.list(session, QuerySpec(limit=1))
            wanted = page.rows[0]

            found = await orders.get(
                session, wanted.id, paths=("customer.name", "items.quantity")
            )

            assert found is not None
            with count_queries(backend) as queries:
                assert found.customer.name
                assert len(found.items) >= 1
            assert queries.count == 0

    async def test_the_wrong_number_of_key_values_is_refused(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            with pytest.raises(InvalidPathError, match="needs 1 key values"):
                await orders.get(session, (1, 2))

    async def test_a_record_can_name_its_own_key(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            record = (await orders.list(session, QuerySpec(limit=1))).rows[0]

            assert orders.identity_of(record) == str(record.id)


class TestCounting:
    async def test_counting_ignores_the_page(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            assert await orders.count(session, QuerySpec(limit=2)) == 7

    async def test_counting_respects_the_search(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            spec = QuerySpec(search="lena", search_paths=("customer.name",))

            assert await orders.count(session, spec) == 2

    async def test_totals_add_up_across_pages(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            spec = QuerySpec(limit=4, sort=(Sort("id"),))
            first = await orders.list(session, spec)
            second = await orders.list(session, spec.page(2))

            assert len(first) + len(second) == first.total

    async def test_money_survives_the_trip(
        self, database: Database, orders: SQLAlchemyRepository
    ) -> None:
        async with database.session() as session:
            page = await orders.list(session, QuerySpec(limit=1))

            assert isinstance(page.rows[0].total, Decimal)
