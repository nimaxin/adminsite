from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

import adminsite
from tests.models import Customer, Order, OrderItem, OrderStatus


class TestSampleData:
    def test_sync_session_reads_the_sample_rows(self, sync_session: Session) -> None:
        customers = sync_session.scalars(select(Customer)).all()
        orders = sync_session.scalars(select(Order)).all()

        assert len(customers) == 4
        assert len(orders) == 7

    async def test_async_session_reads_the_same_rows(
        self, async_session: AsyncSession
    ) -> None:
        customers = (await async_session.scalars(select(Customer))).all()
        orders = (await async_session.scalars(select(Order))).all()

        assert len(customers) == 4
        assert len(orders) == 7

    def test_order_totals_match_their_items(self, sync_session: Session) -> None:
        orders = sync_session.scalars(
            select(Order).options(selectinload(Order.items))
        ).all()

        for order in orders:
            expected = sum(
                (item.unit_price * item.quantity for item in order.items),
                Decimal(0),
            )
            assert order.total == expected

    def test_statuses_cover_every_case(self, sync_session: Session) -> None:
        statuses = set(sync_session.scalars(select(Order.status)).all())

        assert statuses == set(OrderStatus)

    def test_each_order_belongs_to_a_customer(self, sync_session: Session) -> None:
        orphans = sync_session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.customer_id.notin_(select(Customer.id)))
        )

        assert orphans == 0

    async def test_relationships_load_in_both_directions(
        self, async_session: AsyncSession
    ) -> None:
        customer = (
            await async_session.scalars(
                select(Customer)
                .options(selectinload(Customer.orders).selectinload(Order.items))
                .where(Customer.email == "lena@fischer.de")
            )
        ).one()

        assert len(customer.orders) == 2
        assert all(isinstance(item, OrderItem) for item in customer.orders[0].items)


class TestPackage:
    def test_version_is_readable(self) -> None:
        assert adminsite.__version__ == "0.1.0"
