from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RecordNotFoundError
from adminsite.fields import RelationField
from adminsite.query import QuerySpec
from adminsite.views import ModelView
from adminsite.views.writing import DeleteContext, SaveContext
from tests.models import Customer, Order, OrderItem, OrderStatus, Product


class ProductView(ModelView, model=Product):
    form_fields = ("name", "price", "description")


class OrderView(ModelView, model=Order):
    form_fields = ("customer", "status", "total", "created_at", "note")
    readonly_fields = ("total",)
    fields = (RelationField("customer", target=Customer, required=True),)


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "region", "is_active")


@pytest.fixture
def products() -> ProductView:
    return ProductView()


@pytest.fixture
def orders() -> OrderView:
    return OrderView()


class TestReadingAForm:
    def test_values_come_back_converted(self, products: ProductView) -> None:
        result = products.parse_form(
            {"name": "Felt hat", "price": "42.50", "description": ""}
        )

        assert result.ok is True
        assert result.values == {
            "name": "Felt hat",
            "price": Decimal("42.50"),
            "description": None,
        }

    def test_a_bad_value_becomes_a_message(self, products: ProductView) -> None:
        result = products.parse_form({"name": "Hat", "price": "free"})

        assert result.ok is False
        assert result.errors == {"price": "Enter an amount, for example 12.50."}

    def test_a_missing_required_value_is_reported(self, products: ProductView) -> None:
        result = products.parse_form({"name": "", "price": "10.00"})

        assert result.errors == {"name": "This field is required."}

    def test_readonly_fields_are_not_read_from_the_form(
        self, orders: OrderView
    ) -> None:
        result = orders.parse_form(
            {
                "customer": "1",
                "status": "PAID",
                "total": "999.00",
                "created_at": "2026-09-18T10:00",
            }
        )

        assert "total" not in result.values

    def test_a_form_can_send_several_values_for_one_field(self) -> None:
        class CustomerWithOrders(ModelView, model=Customer):
            form_fields = ("name", "email", "orders")

        result = CustomerWithOrders().parse_form(
            {"name": "Lena", "email": "lena@example.com", "orders": ["1", "2"]}
        )

        assert result.values["orders"] == ["1", "2"]


class TestCreating:
    async def test_a_record_is_created(
        self, database: Database, products: ProductView
    ) -> None:
        async with database.session() as session:
            record = await products.save(
                session, {"name": "Felt hat", "price": Decimal("42.00")}
            )

            assert record.id is not None
            assert await session.get(Product, record.id) is not None

    async def test_a_link_is_made_from_a_key(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            customer = await session.scalar(select(Customer))
            assert customer is not None

            record = await orders.save(
                session,
                {
                    "customer": str(customer.id),
                    "status": OrderStatus.PENDING,
                    "created_at": datetime(2026, 9, 19, 12, 0),
                },
            )

            assert record.customer_id == customer.id

    async def test_a_key_that_matches_nothing_is_refused(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            with pytest.raises(RecordNotFoundError, match="No Customer"):
                await orders.save(
                    session,
                    {
                        "customer": "9999",
                        "status": OrderStatus.PENDING,
                        "created_at": datetime(2026, 9, 19, 12, 0),
                    },
                )

    async def test_many_links_are_made_from_keys(self, database: Database) -> None:
        class CustomerWithOrders(ModelView, model=Customer):
            form_fields = ("name", "email", "orders")

        view = CustomerWithOrders()
        async with database.session() as session:
            existing = list((await session.scalars(select(Order))).all())[:2]

            record = await view.save(
                session,
                {
                    "name": "Mara Svensson",
                    "email": "mara@example.com",
                    "orders": [str(order.id) for order in existing],
                },
            )

            assert len(record.orders) == 2


class TestChanging:
    async def test_a_record_is_changed(
        self, database: Database, products: ProductView
    ) -> None:
        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None

            await products.save(session, {"name": "Renamed"}, record=record)

            assert record.name == "Renamed"

    async def test_fields_not_sent_are_left_alone(
        self, database: Database, products: ProductView
    ) -> None:
        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None
            price = record.price

            await products.save(session, {"name": "Renamed"}, record=record)

            assert record.price == price


class TestDeleting:
    async def test_a_record_is_deleted(
        self, database: Database, products: ProductView
    ) -> None:
        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None
            key = record.id

            await products.delete(session, record)

            assert await session.get(Product, key) is None

    async def test_deleting_a_parent_takes_its_children(
        self, database: Database, orders: OrderView
    ) -> None:
        async with database.session() as session:
            order = await orders.repository.get(session, 1, paths=("items.quantity",))
            assert order is not None

            await orders.delete(session, order)

            left = await session.scalar(
                select(func.count())
                .select_from(OrderItem)
                .where(OrderItem.order_id == 1)
            )
            assert left == 0


class TestHooks:
    async def test_a_hook_sees_the_values_before_they_are_written(
        self, database: Database
    ) -> None:
        seen: list[SaveContext] = []

        class Watching(ModelView, model=Product):
            form_fields = ("name", "price")

            async def before_save(self, context: SaveContext) -> None:
                seen.append(context)

        async with database.session() as session:
            await Watching().save(
                session, {"name": "Watched", "price": Decimal("1.00")}
            )

            assert seen[0].created is True
            assert seen[0].values["name"] == "Watched"

    async def test_a_hook_can_change_the_record(self, database: Database) -> None:
        class Stamping(ModelView, model=Product):
            form_fields = ("name", "price")

            async def before_save(self, context: SaveContext) -> None:
                context.record.description = "Added by a hook"

        async with database.session() as session:
            record = await Stamping().save(
                session, {"name": "Stamped", "price": Decimal("2.00")}
            )

            assert record.description == "Added by a hook"

    async def test_a_hook_can_use_the_session(self, database: Database) -> None:
        class Counting(ModelView, model=Product):
            form_fields = ("name", "price")
            seen_before = 0

            async def before_save(self, context: SaveContext) -> None:
                self.seen_before = await context.session.scalar(
                    select(func.count()).select_from(Product)
                )

        view = Counting()
        async with database.session() as session:
            await view.save(session, {"name": "Counted", "price": Decimal("3")})

            assert view.seen_before == 3

    async def test_a_hook_can_write_in_the_same_transaction(
        self, database: Database
    ) -> None:
        class Auditing(ModelView, model=Product):
            form_fields = ("name", "price")

            async def after_save(self, context: SaveContext) -> None:
                await context.session.add(
                    Product(name=f"Copy of {context.record.name}", price=Decimal(0))
                )

        async with database.session() as session:
            await Auditing().save(
                session, {"name": "Original", "price": Decimal("4.00")}
            )

            copies = await session.scalar(
                select(func.count())
                .select_from(Product)
                .where(Product.name == "Copy of Original")
            )
            assert copies == 1

    async def test_a_hook_that_raises_rolls_the_save_back(
        self, database: Database
    ) -> None:
        class Refusing(ModelView, model=Product):
            form_fields = ("name", "price")

            async def before_save(self, context: SaveContext) -> None:
                raise RuntimeError("that price is too low")

        async with database.session() as session:
            before = await session.scalar(select(func.count()).select_from(Product))

            with pytest.raises(RuntimeError, match="too low"):
                await Refusing().save(
                    session, {"name": "Refused", "price": Decimal("0.01")}
                )

            after = await session.scalar(select(func.count()).select_from(Product))
            assert after == before

    async def test_an_after_hook_can_still_refuse_the_save(
        self, database: Database
    ) -> None:
        class SecondThoughts(ModelView, model=Product):
            form_fields = ("name", "price")

            async def after_save(self, context: SaveContext) -> None:
                raise RuntimeError("not allowed after all")

        async with database.session() as session:
            with pytest.raises(RuntimeError):
                await SecondThoughts().save(
                    session, {"name": "Gone", "price": Decimal("5.00")}
                )

            left = await session.scalar(
                select(func.count()).select_from(Product).where(Product.name == "Gone")
            )
            assert left == 0

    async def test_changing_a_record_says_it_was_not_created(
        self, database: Database
    ) -> None:
        marks: list[bool] = []

        class Marking(ModelView, model=Product):
            form_fields = ("name", "price")

            async def before_save(self, context: SaveContext) -> None:
                marks.append(context.created)

        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None

            await Marking().save(session, {"name": "Edited"}, record=record)

            assert marks == [False]

    async def test_a_delete_hook_can_refuse(self, database: Database) -> None:
        class Protective(ModelView, model=Product):
            async def before_delete(self, context: DeleteContext) -> None:
                raise RuntimeError("this product is still selling")

        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None
            # Read the key first: a rollback expires the record, and
            # reading it again would go back to the database.
            key = record.id

            with pytest.raises(RuntimeError, match="still selling"):
                await Protective().delete(session, record)

            assert await session.get(Product, key) is not None

    async def test_delete_hooks_see_the_record(self, database: Database) -> None:
        names: list[str] = []

        class Logging(ModelView, model=Product):
            async def after_delete(self, context: DeleteContext) -> None:
                names.append(context.record.name)

        async with database.session() as session:
            record = await session.scalar(select(Product))
            assert record is not None

            await Logging().delete(session, record)

            assert names == [record.name]


class TestRoundTrip:
    async def test_a_form_can_be_read_saved_and_listed(
        self, database: Database
    ) -> None:
        view = CustomerView()
        async with database.session() as session:
            result = view.parse_form(
                {
                    "name": "Mara Svensson",
                    "email": "mara@example.com",
                    "region": "SE",
                    "is_active": "on",
                }
            )
            assert result.ok

            await view.save(session, result.values)

            page = await view.repository.list(
                session,
                QuerySpec(search="mara", search_paths=("name", "email")),
            )
            assert [row.name for row in page] == ["Mara Svensson"]
            assert page.rows[0].is_active is True
