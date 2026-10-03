"""A save hook reads and changes typed values, and runs again once committed."""

import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any, assert_type

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette

from adminsite import (
    Admin,
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
from tests.models import (
    Account,
    Article,
    Customer,
    Draft,
    Order,
    OrderStatus,
    Product,
    Tag,
)
from tests.support import Backend, count_queries, request_from, spare_product

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


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestTheValues:
    async def test_a_value_reads_as_its_columns_type(self, database: Database) -> None:
        class Reading(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                price = context.values[Product.price].get()
                seen["price"] = assert_type(price, Decimal)

        async with database.session() as session:
            await Reading()._saver.save(
                session,
                {"name": "Scarf", "price": Decimal("12.5")},
                request=request_from(),
            )

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
            await Reading()._saver.save(
                session, {"price": Decimal("1")}, record=record, request=request_from()
            )

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
            await Reading()._saver.save(
                session, {"name": "Scarf", "price": Decimal(1)}, request=request_from()
            )

        assert seen["description"] is None

    async def test_on_a_new_record_one_with_a_default_is_empty_until_the_insert(
        self, database: Database
    ) -> None:
        class Reading(ModelView[Order]):
            fields = [Order.customer, Order.created_at]

            async def before_save(self, context: SaveContext[Order]) -> None:
                seen["before"] = context.values[Order.total].get()

            async def after_save(self, context: SaveContext[Order]) -> None:
                seen["after"] = context.values[Order.total].get()

        async with database.session() as session:
            await Reading()._saver.save(
                session,
                {"customer": "2", "created_at": datetime(2026, 9, 30)},
                request=request_from(),
            )

        # As the record's own attribute is, though the column holds no None.
        assert seen["before"] is None
        assert seen["after"] == Decimal(0)

    async def test_set_stores_a_column_the_form_leaves_out(
        self, database: Database
    ) -> None:
        class Describing(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                name = context.values[Product.name].get()
                context.values[Product.description].set(f"All about {name}")

        async with database.session() as session:
            record = await Describing()._saver.save(
                session, {"name": "Scarf", "price": Decimal(1)}, request=request_from()
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
            await AccountView()._saver.save(
                session, {"email": "ana@example.com"}, request=request_from()
            )

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
                await Mixing()._saver.save(
                    session,
                    {"name": "Scarf", "price": Decimal(1)},
                    request=request_from(),
                )

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
                await Misspelling()._saver.save(
                    session,
                    {"name": "Scarf", "price": Decimal(1)},
                    request=request_from(),
                )

    @pytest.mark.parametrize("created", [True, False])
    async def test_a_misspelt_name_is_refused_when_set(
        self, database: Database, created: bool
    ) -> None:
        class Misspelling(ModelView[Product]):
            fields = [Product.name, Product.price]

            async def before_save(self, context: SaveContext[Product]) -> None:
                context.values["descripton"].set("typo")

        async with database.session() as session:
            record = None if created else await session.get(Product, 1)
            with pytest.raises(AdminSiteError) as raised:
                await Misspelling()._saver.save(
                    session,
                    {"name": "Scarf", "price": Decimal(1)},
                    record=record,
                    request=request_from(),
                )

        assert str(raised.value) == (
            "Product has no column named 'descripton', and the view asks for no "
            "value by that name."
        )

    async def test_a_column_of_a_linked_record_is_refused_when_set(
        self, database: Database
    ) -> None:
        class Reaching(ModelView[Order]):
            fields = [Order.customer, Order.created_at]

            async def before_save(self, context: SaveContext[Order]) -> None:
                context.values["customer.email"].set("new@example.com")

        async with database.session() as session:
            with pytest.raises(AdminSiteError) as raised:
                await Reaching()._saver.save(
                    session,
                    {"customer": "2", "created_at": datetime(2026, 9, 30)},
                    request=request_from(),
                )

        assert str(raised.value) == (
            "'customer.email' is not a column of Order. A save stores the "
            "record's own columns, such as Order.id."
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
                await Reading()._saver.save(
                    session,
                    {"status": OrderStatus.PAID},
                    record=record,
                    request=request_from(),
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
            record = await view._reader.fetch_record(
                session, 1, paths=["customer"], request=request_from()
            )
            assert record is not None
            await view._saver.save(
                session,
                {"status": OrderStatus.PAID},
                record=record,
                request=request_from(),
            )

        assert isinstance(seen["customer"], Customer)
        assert seen["customer"].id == record.customer_id

    async def test_a_link_sent_as_a_key_reads_as_its_record(
        self, database: Database
    ) -> None:
        async with database.session() as session:
            await Ordering()._saver.save(
                session,
                {"customer": "2", "created_at": datetime(2026, 9, 30)},
                request=request_from(),
            )

        assert isinstance(seen["customer"], Customer)
        assert seen["customer"].id == 2

    async def test_so_does_a_link_to_many(self, database: Database) -> None:
        class Tagging(ModelView[Article]):
            fields = [Article.title, Article.tags]

            async def before_save(self, context: SaveContext[Article]) -> None:
                seen["tags"] = assert_type(
                    context.values[Article.tags].get(), list[Tag]
                )

        async with database.session() as session:
            tags = [Tag(name="Linen"), Tag(name="Summer")]
            for tag in tags:
                await session.add(tag)
            await session.commit()
            keys = [str(tag.id) for tag in tags]
            await Tagging()._saver.save(
                session, {"title": "New in", "tags": keys}, request=request_from()
            )

        assert [tag.name for tag in seen["tags"]] == ["Linen", "Summer"]

    @pytest.mark.parametrize("with_customers", [False, True])
    async def test_a_form_hands_the_hook_a_record_with_or_without_its_view(
        self, database: Database, with_customers: bool
    ) -> None:
        views: list[type[ModelView[Any]]] = [Ordering]
        if with_customers:
            views.append(ModelView[Customer])
        app = Starlette()
        app.mount("/admin", Admin(database, views=views))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            answer = await client.post(
                "/admin/orders/new",
                data={"customer": "1", "created_at": "2026-09-30T10:00"},
            )

        assert answer.status_code == 303, answer.text
        assert isinstance(seen["customer"], Customer)
        assert seen["customer"].name == "Lena Fischer"


class Ordering(ModelView[Order]):
    """Reads the customer before an order is saved, as the hooks page shows."""

    name = "orders"
    fields = [Order.customer, Order.created_at]

    async def before_save(self, context: SaveContext[Order]) -> None:
        customer = context.values[Order.customer].get()
        seen["customer"] = customer
        if not customer.is_active:
            raise RefusedError(
                "This customer's account is closed.", field=Order.customer
            )


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
            await Watched()._saver.save(
                session, {"name": "Scarf", "price": Decimal(1)}, request=request_from()
            )

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
            await Watched()._saver.save(
                session, {"price": Decimal(2)}, record=record, request=request_from()
            )

        assert seen["created"] is False

    async def test_a_refused_save_never_reaches_it(self, database: Database) -> None:
        class Refusing(Watched):
            async def after_save(self, context: SaveContext[Product]) -> None:
                await super().after_save(context)
                raise RefusedError("Not today.")

        async with database.session() as session:
            with pytest.raises(RefusedError):
                await Refusing()._saver.save(
                    session,
                    {"name": "Scarf", "price": Decimal(1)},
                    request=request_from(),
                )

        assert events == ["before_save", "after_save"]

    async def test_one_that_fails_is_logged_and_the_save_stands(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Failing(Watched):
            async def after_save_committed(self, context: SaveContext[Product]) -> None:
                raise RuntimeError("The mail server is down.")

        with caplog.at_level(logging.ERROR, logger="adminsite"):
            async with database.session() as session:
                record = await Failing()._saver.save(
                    session,
                    {"name": "Scarf", "price": Decimal(1)},
                    request=request_from(),
                )

        assert await product(database, record.id) is not None
        assert "The mail server is down." in caplog.text

    async def test_a_delete_reaches_it_once_committed(self, database: Database) -> None:
        async with database.session() as session:
            record = await Watched()._saver.save(
                session, {"name": "Scarf", "price": Decimal(1)}, request=request_from()
            )
            events.clear()
            await Watched()._saver.delete(session, record, request=request_from())

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
                str(
                    (
                        await view._saver.save(
                            session,
                            {"name": name, "price": Decimal(1)},
                            request=request_from(),
                        )
                    ).id
                )
                for name in ("Scarf", "Hat")
            ]
            events.clear()
            selection = Selection(
                view=view,
                session=session,
                spec=QuerySpec(),
                keys=keys,
                request=request_from(),
            )
            async with session.transaction():
                await view._delete_selected(selection)

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

    async def test_the_create_form_reaches_it(self, database: Database) -> None:
        async with serve(Admin(database, views=[Watched])) as client:
            answer = await client.post(
                "/admin/products/new", data={"name": "Scarf", "price": "12.50"}
            )

        assert answer.status_code == 303, answer.text
        assert events[-2:] == ["commit", "after_save_committed"]
        assert seen["created"] is True

    async def test_a_delete_through_the_api_reaches_it(
        self, database: Database
    ) -> None:
        key = await spare_product(database)
        async with serve(Admin(database, views=[Watched], api=True)) as client:
            answer = await client.delete(f"/admin/-/api/products/{key}")

        assert answer.status_code == 204, answer.text
        assert events[-2:] == ["commit", "after_delete_committed"]
        assert seen["deleted"] == ["Gift card"]

    async def test_it_reads_the_record_whatever_the_session_factory(
        self, plain_factory: async_sessionmaker[AsyncSession] | sessionmaker[Session]
    ) -> None:
        class Reading(Watched):
            async def after_save_committed(self, context: SaveContext[Product]) -> None:
                seen["name"] = context.record.name

        async with serve(Admin(plain_factory, views=[Reading])) as client:
            answer = await client.post(
                "/admin/products/new", data={"name": "Scarf", "price": "12.50"}
            )

        assert answer.status_code == 303, answer.text
        assert seen["name"] == "Scarf"

    async def test_it_reads_a_value_the_database_set_in_the_save(
        self, database: Database, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Stamped(ModelView[Draft]):
            fields = [Draft.title]

            async def after_save(self, context: SaveContext[Draft]) -> None:
                seen["in the save"] = context.record.updated_at

            async def after_save_committed(self, context: SaveContext[Draft]) -> None:
                seen["once committed"] = context.record.updated_at

        async with database.session() as session:
            draft = Draft(title="Start")
            await session.add(draft)
            await session.commit()
        with caplog.at_level(logging.ERROR, logger="adminsite"):
            async with database.session() as session:
                record = await session.get(Draft, draft.id)
                await Stamped()._saver.save(
                    session, {"title": "About"}, record=record, request=request_from()
                )

        assert isinstance(seen["in the save"], datetime)
        assert isinstance(seen["once committed"], datetime)
        assert "Work that waited on a commit failed." not in caplog.text

    async def test_it_reads_it_when_after_save_changes_the_record(
        self, backend: Backend, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Checked(ModelView[Draft]):
            fields = [Draft.title]

            async def after_save(self, context: SaveContext[Draft]) -> None:
                context.record.title += " (checked)"

            async def after_save_committed(self, context: SaveContext[Draft]) -> None:
                with count_queries(backend) as counter:
                    seen["once committed"] = context.record.updated_at
                seen["queries"] = counter.count

        database = backend.database
        async with database.session() as session:
            draft = Draft(title="Start")
            await session.add(draft)
            await session.commit()
        with caplog.at_level(logging.ERROR, logger="adminsite"):
            async with database.session() as session:
                record = await session.get(Draft, draft.id)
                await Checked()._saver.save(
                    session, {"title": "About"}, record=record, request=request_from()
                )

        assert "Work that waited on a commit failed." not in caplog.text
        assert isinstance(seen["once committed"], datetime)
        assert seen["queries"] == 0
        async with database.session() as session:
            stored = await session.get(Draft, draft.id)
        assert stored is not None
        assert stored.title == "About (checked)"
        assert stored.updated_at == seen["once committed"]
