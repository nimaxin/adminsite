"""A save hook reads and changes typed values, and runs again once committed."""

import logging
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal
from typing import Any, assert_type

import pytest

from adminsite import (
    AdminSiteError,
    DeleteContext,
    Link,
    ModelView,
    RefusedError,
    SaveContext,
)
from adminsite.actions import Selection
from adminsite.backends.sqlalchemy import (
    AsyncSessionAdapter,
    Database,
    SessionAdapter,
    SyncSessionAdapter,
)
from adminsite.fields import PasswordField
from adminsite.query import QuerySpec
from tests.models import Account, Customer, Order, OrderStatus, Product

events: list[str] = []
seen: dict[str, Any] = {}


Commit = Callable[[Any], Awaitable[None]]


def logged(commit: Commit) -> Commit:
    """A session's commit that writes itself down among the hooks."""

    async def logged_commit(session: SessionAdapter) -> None:
        events.append("commit")
        await commit(session)

    return logged_commit


@pytest.fixture(autouse=True)
def watch_commits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Write down each commit among the hooks, to see which run after it."""
    events.clear()
    seen.clear()
    for adapter in (AsyncSessionAdapter, SyncSessionAdapter):
        monkeypatch.setattr(adapter, "_commit", logged(adapter._commit))
    yield


class Watched(ModelView[Product]):
    """Writes down each hook as it runs."""

    fields = [Product.name, Product.price]

    async def before_save(self, context: SaveContext[Product]) -> None:
        events.append("before_save")

    async def after_save(self, context: SaveContext[Product]) -> None:
        events.append("after_save")

    async def after_save_committed(self, context: SaveContext[Product]) -> None:
        events.append("after_save_committed")
        seen["created"] = context.created

    async def before_delete(self, context: DeleteContext[Product]) -> None:
        events.append("before_delete")

    async def after_delete(self, context: DeleteContext[Product]) -> None:
        events.append("after_delete")

    async def after_delete_committed(self, context: DeleteContext[Product]) -> None:
        events.append("after_delete_committed")
        seen.setdefault("deleted", []).append(context.record.name)


async def product(database: Database, key: int) -> Product | None:
    async with database.session() as session:
        return await session.get(Product, key)


class TestTheValues:
    async def test_a_value_reads_as_its_columns_type(self, database: Database) -> None:
        class Reading(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                price = context.values[Product.price].get()
                seen["price"] = assert_type(price, Decimal)

        async with database.session() as session:
            await Reading().save(session, {"name": "Scarf", "price": Decimal("12.5")})

        assert seen["price"] == Decimal("12.5")

    async def test_one_the_save_leaves_reads_as_the_record_has_it(
        self, database: Database
    ) -> None:
        class Reading(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                seen["name"] = context.values[Product.name].get()
                seen["given"] = (
                    Product.name in context.values,
                    Product.price in context.values,
                )

        async with database.session() as session:
            record = await session.get(Product, 1)
            assert record is not None
            await Reading().save(session, {"price": Decimal("1")}, record=record)

        assert seen["name"] == record.name
        assert seen["given"] == (False, True)

    async def test_on_a_new_record_one_not_given_is_empty(
        self, database: Database
    ) -> None:
        class Reading(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                seen["description"] = context.values[Product.description].get()

        async with database.session() as session:
            await Reading().save(session, {"name": "Scarf", "price": Decimal(1)})

        assert seen["description"] is None

    async def test_set_stores_a_column_the_form_leaves_out(
        self, database: Database
    ) -> None:
        class Describing(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                name = context.values[Product.name].get()
                context.values[Product.description].set(f"All about {name}")

        async with database.session() as session:
            record = await Describing().save(
                session, {"name": "Scarf", "price": Decimal(1)}
            )

        stored = await product(database, record.id)
        assert stored is not None
        assert stored.description == "All about Scarf"

    async def test_a_string_names_a_value_that_is_no_column(
        self, database: Database
    ) -> None:
        class AccountView(ModelView[Account]):
            fields = [Account.email, PasswordField("password")]

            async def before_save(self, context: SaveContext[Account]) -> None:
                seen["password"] = context.values["password"].get()
                seen["given"] = "password" in context.values
                context.values[Account.password_hash].set("hashed")

        async with database.session() as session:
            await AccountView().save(session, {"email": "ana@example.com"})

        assert seen == {"password": None, "given": False}

    async def test_a_column_of_another_model_is_refused(
        self, database: Database
    ) -> None:
        class Mixing(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                context.values[Customer.name].get()

        async with database.session() as session:
            with pytest.raises(AdminSiteError) as raised:
                await Mixing().save(session, {"name": "Scarf", "price": Decimal(1)})

        assert str(raised.value) == (
            "Customer.name is not a column of Product. A save stores the "
            "record's own columns, such as Product.id."
        )

    async def test_a_misspelt_name_is_refused(self, database: Database) -> None:
        class Misspelling(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                context.values["nmae"].get()

        async with database.session() as session:
            with pytest.raises(AdminSiteError, match="Product has no column named"):
                await Misspelling().save(
                    session, {"name": "Scarf", "price": Decimal(1)}
                )

    async def test_a_relation_not_loaded_is_refused_rather_than_loaded(
        self, database: Database
    ) -> None:
        class Reading(ModelView[Order]):
            fields = [Order.status]

            async def before_save(self, context: SaveContext[Order]) -> None:
                context.values[Order.customer].get()

        async with database.session() as session:
            record = await session.get(Order, 1)
            assert record is not None
            with pytest.raises(AdminSiteError) as raised:
                await Reading().save(
                    session, {"status": OrderStatus.PAID}, record=record
                )

        assert str(raised.value) == (
            "'customer' is not loaded on the Order, and reading it here would "
            "need a query. Put it in the view's fields, or read it with "
            "context.session."
        )

    async def test_a_relation_on_the_form_reads_as_its_record(
        self, database: Database
    ) -> None:
        class Reading(ModelView[Order]):
            fields = [Order.status, Order.customer]

            async def before_save(self, context: SaveContext[Order]) -> None:
                seen["customer"] = assert_type(
                    context.values[Order.customer].get(), Customer
                )

        view = Reading()
        async with database.session() as session:
            record = await view.fetch_record(session, 1, paths=["customer"])
            assert record is not None
            await view.save(session, {"status": OrderStatus.PAID}, record=record)

        assert isinstance(seen["customer"], Customer)
        assert seen["customer"].id == record.customer_id


class TestRefusedByAttribute:
    def test_a_column_names_its_field(self) -> None:
        assert RefusedError("Say why.", field=Order.note).field == "note"

    def test_a_link_names_its_path(self) -> None:
        refused = RefusedError("Wrong.", field=Link(Order.customer, Customer.email))

        assert refused.field == "customer.email"

    def test_a_string_is_the_path_itself(self) -> None:
        assert RefusedError("Wrong.", field="customer.email").field == "customer.email"

    def test_none_names_no_field(self) -> None:
        assert RefusedError("Wrong.").field == ""


class TestCommittedHooks:
    async def test_a_save_reaches_it_once_committed(self, database: Database) -> None:
        async with database.session() as session:
            await Watched().save(session, {"name": "Scarf", "price": Decimal(1)})

        assert events == [
            "before_save",
            "after_save",
            "commit",
            "after_save_committed",
        ]
        assert seen["created"] is True

    async def test_it_knows_a_change_from_a_new_record(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            record = await session.get(Product, 1)
            await Watched().save(session, {"price": Decimal(2)}, record=record)

        assert seen["created"] is False

    async def test_a_refused_save_never_reaches_it(self, database: Database) -> None:
        class Refusing(Watched):
            async def after_save(self, context: SaveContext[Product]) -> None:
                await super().after_save(context)
                raise RefusedError("Not today.")

        async with database.session() as session:
            with pytest.raises(RefusedError):
                await Refusing().save(session, {"name": "Scarf", "price": Decimal(1)})

        assert events == ["before_save", "after_save"]

    async def test_one_that_fails_is_logged_and_the_save_stands(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Failing(Watched):
            async def after_save_committed(self, context: SaveContext[Product]) -> None:
                raise RuntimeError("The mail server is down.")

        with caplog.at_level(logging.ERROR, logger="adminsite"):
            async with database.session() as session:
                record = await Failing().save(
                    session, {"name": "Scarf", "price": Decimal(1)}
                )

        assert await product(database, record.id) is not None
        assert "The mail server is down." in caplog.text

    async def test_a_delete_reaches_it_once_committed(self, database: Database) -> None:
        async with database.session() as session:
            record = await Watched().save(
                session, {"name": "Scarf", "price": Decimal(1)}
            )
            events.clear()
            await Watched().delete(session, record)

        assert events == [
            "before_delete",
            "after_delete",
            "commit",
            "after_delete_committed",
        ]
        assert seen["deleted"] == ["Scarf"]

    async def test_deleting_the_chosen_rows_reaches_it_after_all_are_gone(
        self, database: Database
    ) -> None:
        view = Watched()
        async with database.session() as session:
            keys = [
                str((await view.save(session, {"name": name, "price": Decimal(1)})).id)
                for name in ("Scarf", "Hat")
            ]
            events.clear()
            selection = Selection(
                view=view, session=session, spec=QuerySpec(), keys=keys
            )
            async with session.transaction():
                await view.delete_selected(selection)

        assert events == [
            "before_delete",
            "after_delete",
            "before_delete",
            "after_delete",
            "commit",
            "after_delete_committed",
            "after_delete_committed",
        ]
        assert sorted(seen["deleted"]) == ["Hat", "Scarf"]
