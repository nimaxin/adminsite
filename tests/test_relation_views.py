import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, Inline, ModelView, Statement
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import ComputedField, RelationField
from tests.models import Customer, Order, OrderItem, OrderStatus
from tests.support import Backend, count_queries


class CustomerView(ModelView[Customer]):
    """Every customer outside Sweden."""

    record_title = "{name}"
    fields = ["name", "email", "orders"]

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region != "SE")


class NordicView(ModelView[Customer]):
    """The Swedish customers, the ones the first view leaves out."""

    name = "nordic"
    record_title = "{name}"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Customer.region == "SE")


class OrderView(ModelView[Order]):
    record_title = "Order #{id}"
    fields = ["customer", "status"]


class NordicOrderView(ModelView[Order]):
    """Orders whose customer is picked from, and linked to, the Nordic view."""

    name = "nordic_orders"
    record_title = "Order #{id}"
    fields = [RelationField("customer", view="nordic"), "status"]


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database, views=[CustomerView, NordicView, OrderView, NordicOrderView]
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def customer_and_order(database: Database, email: str) -> tuple[int, int]:
    async with database.session() as session:
        wanted = select(Customer.id).where(Customer.email == email)
        customer = await session.scalar(wanted)
        order = await session.scalar(
            select(Order.id).where(Order.customer_id == customer).order_by(Order.id)
        )
    assert customer is not None and order is not None
    return int(customer), int(order)


class TestAFieldThatNamesItsView:
    async def test_its_picker_lists_from_that_view(
        self, client: httpx.AsyncClient
    ) -> None:
        found = await client.get("/admin/nordic_orders/lookup/customer")

        assert "Jonas Berg" in found.text
        assert "Lena Fischer" not in found.text

    async def test_its_link_opens_that_view(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, order = await customer_and_order(database, "jonas@berg.se")

        page = await client.get(f"/admin/nordic_orders/{order}")

        assert f'href="/admin/nordic/{jonas}"' in page.text

    async def test_saving_checks_the_key_against_that_view(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, order = await customer_and_order(database, "jonas@berg.se")
        lena, _other = await customer_and_order(database, "lena@fischer.de")

        refused = await client.post(
            f"/admin/nordic_orders/{order}/edit",
            data={"customer": str(lena), "status": "PAID"},
        )
        kept = await client.post(
            f"/admin/nordic_orders/{order}/edit",
            data={"customer": str(jonas), "status": "PAID"},
        )

        assert refused.status_code == 422
        assert "Choose a record." in refused.text
        assert kept.status_code == 303

    def test_a_view_that_does_not_exist_is_named(self, database: Database) -> None:
        admin = Admin(database, views=[CustomerView])
        field = RelationField("customer", target=Customer, view="buyers")

        with pytest.raises(AdminSiteError, match="'buyers'"):
            admin.views.for_relation(field)

    def test_a_view_of_another_model_is_named(self, database: Database) -> None:
        admin = Admin(database, views=[CustomerView, OrderView])
        field = RelationField("customer", target=Customer, view="orders")

        with pytest.raises(AdminSiteError, match="shows Order, not Customer"):
            admin.views.for_relation(field)


class BuyerView(ModelView[Customer]):
    name = "buyers"


class TestAViewALinkNamesIsCheckedAtStartup:
    def refusal(self, *views: type[ModelView[Any]]) -> str:
        admin = Admin(create_engine("sqlite://"), views=views)
        with pytest.raises(AdminSiteError) as raised:
            admin.app  # noqa: B018  # built for the error it raises
        return str(raised.value)

    def test_a_misspelt_name(self) -> None:
        class TypoView(ModelView[Order]):
            fields = [RelationField(Order.customer, view="custmers")]

        assert self.refusal(CustomerView, BuyerView, TypoView) == (
            "TypoView.fields: The field 'customer' names the view 'custmers', "
            "which is not registered with this admin. The views of Customer: "
            "'customers', 'buyers'."
        )

    def test_a_class_that_is_not_registered(self) -> None:
        class WalletView(ModelView[Order]):
            fields = [RelationField(Order.customer, view=BuyerView)]

        assert self.refusal(CustomerView, WalletView).startswith(
            "WalletView.fields: The field 'customer' names the view BuyerView, "
            "which is not registered with this admin."
        )

    def test_a_view_of_another_model(self) -> None:
        class MixedView(ModelView[Order]):
            name = "mixed"
            fields = [RelationField(Order.customer, view="orders")]

        assert self.refusal(OrderView, MixedView) == (
            "MixedView.fields: The field 'customer' names the view 'orders', which "
            "shows Order, not Customer. No view of Customer is registered."
        )

    def test_a_field_of_an_inline(self) -> None:
        class WithLines(ModelView[Order]):
            inlines = [
                Inline(
                    Order.items,
                    fields=[RelationField(OrderItem.product, view="prodcts")],
                )
            ]

        assert self.refusal(WithLines).startswith(
            "WithLines.inlines[0].fields: The field 'product' names the view "
            "'prodcts', which is not registered with this admin."
        )

    def test_a_view_registered_after_the_one_naming_it(self) -> None:
        class WalletView(ModelView[Order]):
            fields = [RelationField(Order.customer, view=BuyerView)]

        admin = Admin(create_engine("sqlite://"), views=[WalletView])
        admin.add_view(BuyerView)

        assert isinstance(admin.app, Starlette)


class TestALinkWithoutAView:
    async def test_it_opens_the_view_that_holds_the_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        jonas, jonas_order = await customer_and_order(database, "jonas@berg.se")
        lena, lena_order = await customer_and_order(database, "lena@fischer.de")

        swedish = await client.get(f"/admin/orders/{jonas_order}")
        german = await client.get(f"/admin/orders/{lena_order}")

        # The first view leaves Swedish customers out, so rather than a
        # link that answers 404, it takes the one that holds them.
        assert f'href="/admin/nordic/{jonas}"' in swedish.text
        assert f'href="/admin/customers/{lena}"' in german.text


class TestAToManyLinkOnTheRecordPage:
    async def test_it_names_twenty_and_counts_the_rest(
        self, client: httpx.AsyncClient, backend: Backend
    ) -> None:
        lena, _order = await customer_and_order(backend.database, "lena@fischer.de")
        async with backend.database.session() as session:
            for day in range(1, 31):
                await session.add(
                    Order(
                        customer_id=lena,
                        status=OrderStatus.PENDING,
                        created_at=datetime(2026, 8, day % 28 + 1),
                    )
                )
            await session.commit()
            hers = select(Order.id).where(Order.customer_id == lena)
            total = len((await session.scalars(hers)).all())

        with count_queries(backend) as queries:
            page = await client.get(f"/admin/customers/{lena}")

        assert f"and {total - 20:,} more" in page.text
        assert page.text.count("Order #") == 20
        orders = [
            item.lower() for item in queries.statements if "from orders" in item.lower()
        ]
        assert orders
        assert all("limit" in item or "count(" in item for item in orders)


class OpenOrderView(ModelView[Order]):
    """Every order but the refunded ones."""

    name = "open_orders"
    record_title = "Order #{id}"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status != OrderStatus.REFUNDED)


@pytest.fixture
async def open_orders(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[CustomerView, OpenOrderView])
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestAToManyLinkThroughAScopedView:
    async def test_it_names_and_counts_only_what_the_scope_shows(
        self, open_orders: httpx.AsyncClient, backend: Backend
    ) -> None:
        aisha, _order = await customer_and_order(backend.database, "aisha@khan.co.uk")
        async with backend.database.session() as session:
            for day in range(1, 31):
                status = OrderStatus.PAID if day % 4 else OrderStatus.REFUNDED
                await session.add(
                    Order(
                        customer_id=aisha,
                        status=status,
                        created_at=datetime(2026, 8, day % 28 + 1),
                    )
                )
            await session.commit()
            hers = select(Order.id, Order.status).where(Order.customer_id == aisha)
            rows = (await session.execute(hers)).all()
        refunded = [key for key, status in rows if status is OrderStatus.REFUNDED]
        shown = len(rows) - len(refunded)

        with count_queries(backend) as queries:
            page = await open_orders.get(f"/admin/customers/{aisha}")

        assert page.status_code == 200
        assert shown > 20
        assert f"and {shown - 20:,} more" in page.text
        named = {int(key) for key in re.findall(r"Order #(\d+)\b", page.text)}
        assert len(named) == 20
        assert not named & set(refunded)
        orders = [item for item in queries.statements if "FROM orders" in item]
        assert len(orders) == 2


class PaidOrderView(ModelView[Order]):
    """The paid orders."""

    name = "paid_orders"
    record_title = "Order #{id}"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status == OrderStatus.PAID)


class RefundView(ModelView[Order]):
    """The refunded orders, which the paid view leaves out."""

    name = "refunds"
    record_title = "Order #{id}"

    def scope_query(self, statement: Statement, *, request: Request) -> Statement:
        return statement.where(Order.status == OrderStatus.REFUNDED)


class RefundedCustomerView(ModelView[Customer]):
    """Customers whose orders are read and linked through the refunds alone."""

    name = "refunded_customers"
    record_title = "{name}"
    fields = ["name", RelationField("orders", view=RefundView)]


def serve(database: Database, *views: type[ModelView[Any]]) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", Admin(database, views=list(views)))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def orders_by_status(
    database: Database, customer: int
) -> dict[OrderStatus, list[int]]:
    async with database.session() as session:
        rows = await session.execute(
            select(Order.id, Order.status)
            .where(Order.customer_id == customer)
            .order_by(Order.id)
        )
        found: dict[OrderStatus, list[int]] = {}
        for key, status in rows.all():
            found.setdefault(status, []).append(int(key))
    return found


def named(page: httpx.Response) -> set[int]:
    """The orders a page names."""
    return {int(key) for key in re.findall(r"Order #(\d+)\b", page.text)}


class TestAToManyLinkSeveralViewsCouldOpen:
    async def test_each_record_opens_the_view_that_holds_it(
        self, database: Database
    ) -> None:
        aisha, _order = await customer_and_order(database, "aisha@khan.co.uk")
        orders = await orders_by_status(database, aisha)
        [paid], [refunded] = orders[OrderStatus.PAID], orders[OrderStatus.REFUNDED]

        async with serve(database, CustomerView, PaidOrderView, RefundView) as client:
            page = await client.get(f"/admin/customers/{aisha}")

        assert named(page) == {paid, refunded}
        assert f'href="/admin/paid_orders/{paid}">Order #{paid}</a>' in page.text
        assert f'href="/admin/refunds/{refunded}">Order #{refunded}</a>' in page.text

    async def test_it_names_and_counts_what_any_of_them_holds(
        self, backend: Backend
    ) -> None:
        aisha, _order = await customer_and_order(backend.database, "aisha@khan.co.uk")
        statuses = [OrderStatus.PAID, OrderStatus.REFUNDED, OrderStatus.PENDING]
        async with backend.database.session() as session:
            for day in range(1, 31):
                await session.add(
                    Order(
                        customer_id=aisha,
                        status=statuses[day % 3],
                        created_at=datetime(2026, 8, day % 28 + 1),
                    )
                )
            await session.commit()
        orders = await orders_by_status(backend.database, aisha)
        held = len(orders[OrderStatus.PAID]) + len(orders[OrderStatus.REFUNDED])

        views = (CustomerView, PaidOrderView, RefundView)
        async with serve(backend.database, *views) as client:
            with count_queries(backend) as queries:
                page = await client.get(f"/admin/customers/{aisha}")

        assert f"and {held - 20:,} more" in page.text
        assert len(named(page)) == 20
        assert not named(page) & set(orders[OrderStatus.PENDING])
        # Two read them and one for each view links them, however many there are.
        reads = [item for item in queries.statements if "FROM orders" in item]
        assert len(reads) == 4

    async def test_a_view_without_a_scope_opens_the_rest(
        self, database: Database
    ) -> None:
        aisha, _order = await customer_and_order(database, "aisha@khan.co.uk")
        orders = await orders_by_status(database, aisha)
        [paid], [refunded] = orders[OrderStatus.PAID], orders[OrderStatus.REFUNDED]

        async with serve(database, CustomerView, PaidOrderView, OrderView) as client:
            page = await client.get(f"/admin/customers/{aisha}")

        assert f'href="/admin/paid_orders/{paid}">' in page.text
        assert f'href="/admin/orders/{refunded}">' in page.text

    async def test_a_link_that_names_a_view_reads_through_it_alone(
        self, database: Database
    ) -> None:
        aisha, _order = await customer_and_order(database, "aisha@khan.co.uk")
        orders = await orders_by_status(database, aisha)
        [refunded] = orders[OrderStatus.REFUNDED]

        views = (RefundedCustomerView, PaidOrderView, RefundView)
        async with serve(database, *views) as client:
            page = await client.get(f"/admin/refunded_customers/{aisha}")

        assert named(page) == {refunded}
        assert f'href="/admin/refunds/{refunded}">' in page.text


class CustomerFormView(ModelView[Customer]):
    """A to-many link on the form, which the record page shows as well."""

    name = "customer_forms"
    fields = ["name", "orders"]


class CountedOrderView(ModelView[Order]):
    """A computed field that reads the items the page also lists."""

    name = "counted_orders"
    record_title = "Order #{id}"
    fields = [
        "status",
        "items",
        ComputedField("item_count", lambda order: len(order.items), needs=("items",)),
    ]


class NeedsByAttributeView(ModelView[Order]):
    """The same field, its needs written as an attribute, as the docs write them."""

    name = "needs_by_attribute"
    record_title = "Order #{id}"
    fields = [
        "status",
        "items",
        ComputedField(
            "item_count", lambda order: len(order.items), needs=[Order.items]
        ),
    ]


@pytest.fixture
async def pages(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[CustomerView, CustomerFormView, CountedOrderView, NeedsByAttributeView],
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def add(database: Database, record: Any) -> int:
    async with database.session() as session:
        await session.add(record)
        await session.flush()
        key = int(record.id)
        await session.commit()
    return key


def value_of(page: httpx.Response, label: str) -> str:
    found = re.search(rf"{label}</dt>\s*<dd[^>]*>(.*?)</dd>", page.text, re.S)
    assert found is not None, label
    return re.sub(r"<[^>]+>", "", found.group(1)).strip()


# An empty value as value_of reads it: the hyphen, then what a screen reader says.
EMPTY = "-Not set"


class TestAToManyLinkThatHoldsNothing:
    async def test_the_record_page_shows_it_empty(
        self, pages: httpx.AsyncClient, database: Database
    ) -> None:
        nadia = await add(database, Customer(name="Nadia New", email="nadia@new.nl"))

        page = await pages.get(f"/admin/customers/{nadia}")

        assert page.status_code == 200
        assert value_of(page, "Orders") == EMPTY

    async def test_so_does_one_that_is_on_the_form(
        self, pages: httpx.AsyncClient, database: Database
    ) -> None:
        nadia = await add(database, Customer(name="Nadia New", email="nadia@new.nl"))

        page = await pages.get(f"/admin/customer_forms/{nadia}")

        assert page.status_code == 200
        assert value_of(page, "Orders") == EMPTY


class TestAComputedFieldThatReadsAShownLink:
    async def test_it_is_worked_out_from_the_link(
        self, pages: httpx.AsyncClient
    ) -> None:
        page = await pages.get("/admin/counted_orders/1")

        assert page.status_code == 200
        assert value_of(page, "Item count") == "2"
        assert value_of(page, "Items").startswith("Order item #")

    async def test_its_needs_may_name_the_link_by_attribute(
        self, pages: httpx.AsyncClient
    ) -> None:
        page = await pages.get("/admin/needs_by_attribute/1")

        assert page.status_code == 200
        assert value_of(page, "Item count") == "2"
        assert value_of(page, "Items").startswith("Order item #")

    async def test_it_holds_nothing_too(
        self, pages: httpx.AsyncClient, database: Database
    ) -> None:
        empty = await add(
            database,
            Order(
                customer_id=1,
                status=OrderStatus.PENDING,
                created_at=datetime(2026, 9, 1),
            ),
        )

        page = await pages.get(f"/admin/counted_orders/{empty}")

        assert page.status_code == 200
        assert value_of(page, "Item count") == "0"
        assert value_of(page, "Items") == EMPTY
