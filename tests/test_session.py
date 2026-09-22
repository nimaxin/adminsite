from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from adminsite.backends.sqlalchemy import (
    AsyncSessionAdapter,
    Database,
    SyncSessionAdapter,
)
from adminsite.exceptions import AdminSiteError
from tests.models import Customer, Order, Product
from tests.support import spare_product


class TestDatabase:
    def test_an_async_engine_gives_an_async_session(
        self, async_engine: AsyncEngine
    ) -> None:
        database = Database(async_engine)

        assert database.is_async is True
        assert isinstance(database.open(), AsyncSessionAdapter)

    def test_a_sync_engine_gives_a_threaded_session(self, sync_engine: Engine) -> None:
        database = Database(sync_engine)

        assert database.is_async is False
        assert isinstance(database.open(), SyncSessionAdapter)

    def test_session_factories_are_accepted_too(
        self,
        sync_session_factory: sessionmaker[Session],
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        assert Database(sync_session_factory).is_async is False
        assert Database(async_session_factory).is_async is True

    def test_anything_else_is_refused(self) -> None:
        with pytest.raises(AdminSiteError, match="engine or a session factory"):
            Database("sqlite://")  # type: ignore[arg-type]


class TestReading:
    async def test_rows_come_back_the_same_either_way(self, database: Database) -> None:
        async with database.session() as session:
            customers = (await session.scalars(select(Customer))).all()

            assert len(customers) == 4

    async def test_a_single_value_comes_back(self, database: Database) -> None:
        async with database.session() as session:
            count = await session.scalar(select(func.count()).select_from(Order))

            assert count == 7

    async def test_a_record_is_loaded_by_key(self, database: Database) -> None:
        async with database.session() as session:
            first = (await session.scalars(select(Customer))).first()
            assert first is not None

            found = await session.get(Customer, first.id)

            assert found is not None
            assert found.email == first.email

    async def test_a_missing_key_gives_nothing(self, database: Database) -> None:
        async with database.session() as session:
            assert await session.get(Customer, 9999) is None

    async def test_rows_can_be_read_as_tuples(self, database: Database) -> None:
        async with database.session() as session:
            result = await session.execute(
                select(Customer.name, Customer.email).order_by(Customer.id)
            )
            rows = result.all()

            assert rows[0].name == "Lena Fischer"


class TestWriting:
    async def test_a_new_record_is_saved(self, database: Database) -> None:
        async with database.session() as session:
            await session.add(Product(name="Felt hat", price=Decimal("42.00")))
            await session.commit()

            saved = await session.scalar(
                select(Product).where(Product.name == "Felt hat")
            )

            assert saved is not None
            assert saved.price == Decimal("42.00")

    async def test_a_record_is_deleted(self, database: Database) -> None:
        key = await spare_product(database)
        async with database.session() as session:
            product = await session.get(Product, key)
            assert product is not None

            await session.delete(product)
            await session.commit()

            assert await session.get(Product, key) is None

    async def test_a_change_is_undone_by_rolling_back(self, database: Database) -> None:
        async with database.session() as session:
            await session.add(Product(name="Gone", price=Decimal("1.00")))
            await session.flush()
            await session.rollback()

            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Product)
                    .where(Product.name == "Gone")
                )
                == 0
            )

    async def test_a_record_is_reloaded_from_the_database(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            product = await session.scalar(select(Product))
            assert product is not None
            product.name = "Changed in memory"

            await session.refresh(product)

            assert product.name != "Changed in memory"


class TestTransaction:
    async def test_work_is_committed_when_the_block_ends(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            async with session.transaction():
                await session.add(Product(name="Kept", price=Decimal("5.00")))

            count = await session.scalar(
                select(func.count()).select_from(Product).where(Product.name == "Kept")
            )

            assert count == 1

    async def test_an_error_rolls_the_whole_block_back(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            with pytest.raises(RuntimeError):
                async with session.transaction():
                    await session.add(Product(name="Dropped", price=Decimal("5.00")))
                    await session.flush()
                    raise RuntimeError("the hook said no")

            count = await session.scalar(
                select(func.count())
                .select_from(Product)
                .where(Product.name == "Dropped")
            )

            assert count == 0


class TestEscapeHatch:
    async def test_a_function_can_use_the_plain_session(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            names = await session.run(
                lambda plain: list(plain.scalars(select(Customer.name)))
            )

            assert "Lena Fischer" in names

    async def test_lazy_loading_works_inside_run(self, database: Database) -> None:
        async with database.session() as session:

            def count_orders(plain: Session) -> int:
                customer = plain.scalars(
                    select(Customer).where(Customer.email == "lena@fischer.de")
                ).one()
                return len(customer.orders)

            assert await session.run(count_orders) == 2
