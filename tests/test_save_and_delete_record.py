"""save_record and delete_record let a view write a record its own way."""

import io
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.datastructures import Headers, UploadFile

from adminsite import (
    Admin,
    DeleteContext,
    Inline,
    ModelView,
    RefusedError,
    SaveContext,
)
from adminsite.audit import AuditEvent, AuditLog
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.fields import ImageField
from adminsite.fields.files import NewFile
from adminsite.files import LocalStorage
from tests.models import Customer, Order, OrderItem, Product
from tests.support import request_from

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

# What ran, in order, for the test that just ran.
calls: list[str] = []


class KeptOrders(ModelView[Order]):
    """Delete keeps the order and writes a note on it instead."""

    name = "orders"
    fields = [Order.customer, Order.status, Order.note]

    async def before_delete(self, context: DeleteContext[Order]) -> None:
        calls.append("before_delete")

    async def delete_record(self, context: DeleteContext[Order]) -> None:
        calls.append("delete_record")
        context.record.note = "Deleted"

    async def after_delete(self, context: DeleteContext[Order]) -> None:
        calls.append("after_delete")


class LockedOrders(KeptOrders):
    name = "locked_orders"
    can_delete = False


class StubbornOrders(KeptOrders):
    name = "stubborn_orders"

    async def delete_record(self, context: DeleteContext[Order]) -> None:
        raise RefusedError("Orders are never deleted.")


class LoudCustomers(ModelView[Customer]):
    """Names are stored in capitals, by the view's own code."""

    name = "customers"
    fields = [Customer.name, Customer.email, Customer.region]

    async def before_save(self, context: SaveContext[Customer]) -> None:
        calls.append("before_save")

    async def save_record(self, context: SaveContext[Customer]) -> None:
        calls.append("save_record")
        customer = context.record
        customer.name = context.values[Customer.name].get().upper()
        customer.email = context.values[Customer.email].get()
        customer.region = context.values[Customer.region].get()
        if context.created:
            await context.session.add(customer)

    async def after_save(self, context: SaveContext[Customer]) -> None:
        calls.append("after_save")


class ForgetfulCustomers(ModelView[Customer]):
    """Forgets to store a new customer."""

    name = "forgetful"
    fields = [Customer.name, Customer.email, Customer.region]

    async def save_record(self, context: SaveContext[Customer]) -> None:
        pass


class CountedOrders(ModelView[Order]):
    """The usual save, then a note with how many lines the order holds."""

    name = "counted_orders"
    fields = [Order.customer, Order.status, Order.note, Order.created_at]
    inlines = [
        Inline(
            Order.items,
            fields=[OrderItem.product, OrderItem.quantity, OrderItem.unit_price],
        )
    ]

    async def save_record(self, context: SaveContext[Order]) -> None:
        await super().save_record(context)
        context.record.note = f"{len(context.record.items)} lines"


@pytest.fixture(autouse=True)
def forget_calls() -> Iterator[None]:
    calls.clear()
    yield
    calls.clear()


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[KeptOrders, LockedOrders, StubbornOrders, LoudCustomers],
        audit=log,
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def note_of(database: Database, key: int) -> str | None:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None, "the order is still there"
        return order.note


class TestDeleteRecord:
    async def test_the_record_page_s_delete_keeps_the_row(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.post("/admin/orders/3/delete")

        assert answer.status_code == 303
        assert await note_of(database, 3) == "Deleted"

    async def test_the_hooks_run_around_it(self, client: httpx.AsyncClient) -> None:
        await client.post("/admin/orders/3/delete")

        assert calls == ["before_delete", "delete_record", "after_delete"]

    async def test_deleting_the_ticked_rows_runs_it_for_each(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.post(
            "/admin/orders/action/delete_selected", data={"keys": ["1", "2"]}
        )

        assert answer.status_code == 303
        assert await note_of(database, 1) == "Deleted"
        assert await note_of(database, 2) == "Deleted"
        assert calls.count("delete_record") == 2

    async def test_the_api_runs_it(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.delete("/admin/-/api/orders/4")

        assert answer.status_code == 204
        assert await note_of(database, 4) == "Deleted"

    async def test_the_audit_log_calls_it_a_delete(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post("/admin/orders/3/delete")

        entry = (await log.history("orders", "3"))[0]
        assert entry.event is AuditEvent.DELETED

    async def test_the_permission_still_decides(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post("/admin/locked_orders/3/delete")

        assert await note_of(database, 3) is None
        assert calls == []

    async def test_it_can_refuse(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.delete("/admin/-/api/stubborn_orders/3")

        assert answer.status_code == 409
        assert answer.json() == {"error": "Orders are never deleted."}
        assert await note_of(database, 3) is None


class TestSaveRecord:
    async def test_a_new_record_is_stored_its_way(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await client.post(
            "/admin/customers/new",
            data={"name": "Nina Holm", "email": "nina@holm.dk", "region": "DK"},
        )

        async with database.session() as session:
            nina = await session.scalar(
                select(Customer).where(Customer.email == "nina@holm.dk")
            )
        assert nina is not None
        assert nina.name == "NINA HOLM"
        assert answer.status_code == 303
        assert answer.headers["location"].endswith(f"/admin/customers/{nina.id}")

    async def test_an_edit_is_stored_its_way(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await client.post(
            "/admin/customers/1/edit",
            data={"name": "Lena Fischer", "email": "lena@fischer.de", "region": "DE"},
        )

        async with database.session() as session:
            lena = await session.get(Customer, 1)
        assert lena is not None
        assert lena.name == "LENA FISCHER"

    async def test_the_hooks_run_around_it(self, client: httpx.AsyncClient) -> None:
        await client.post(
            "/admin/customers/1/edit",
            data={"name": "Lena Fischer", "email": "lena@fischer.de", "region": "DE"},
        )

        assert calls == ["before_save", "save_record", "after_save"]

    async def test_the_audit_log_holds_what_it_stored(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post(
            "/admin/customers/1/edit",
            data={"name": "Lena Fischer", "email": "lena@fischer.de", "region": "DE"},
        )

        entry = (await log.history("customers", "1"))[0]
        assert entry.event is AuditEvent.UPDATED
        assert entry.changes["name"] == ("Lena Fischer", "LENA FISCHER")

    async def test_a_new_record_left_unstored_says_what_to_do(
        self, database: Database
    ) -> None:
        view = ForgetfulCustomers()
        values = {"name": "Nina Holm", "email": "nina@holm.dk", "region": "DK"}
        async with database.session() as session:
            with pytest.raises(AdminSiteError, match="did not store the new Customer"):
                await view._saver.save(session, values, request=request_from())

    async def test_super_keeps_the_usual_save_with_the_inline_rows(
        self, database: Database
    ) -> None:
        view = CountedOrders()
        form = {
            "customer": "1",
            "status": "PAID",
            "note": "",
            "created_at": "2026-09-20T10:30",
            "items-count": "2",
            "items-0-key": "",
            "items-0-product": "1",
            "items-0-quantity": "1",
            "items-0-unit_price": "59.00",
            "items-1-key": "",
            "items-1-product": "2",
            "items-1-quantity": "2",
            "items-1-unit_price": "24.00",
        }
        async with database.session() as session:
            result = view._forms.parse(form, request=request_from())
            order = await view._saver.save(
                session,
                result.values,
                inline_rows=result.inline_rows,
                request=request_from(),
            )
            key = order.id

        async with database.session() as session:
            items = (
                await session.scalars(
                    select(OrderItem).where(OrderItem.order_id == key)
                )
            ).all()
        assert len(items) == 2
        assert await note_of(database, key) == "2 lines"


class TestFilesItLeavesAlone:
    async def test_an_old_file_the_record_still_holds_is_kept(
        self, database: Database, tmp_path: Path
    ) -> None:
        storage = LocalStorage(tmp_path / "uploads")

        class ProductView(ModelView[Product]):
            fields = [
                "name",
                ImageField("description", label="Photo", storage=storage),
            ]

        class NameOnly(ProductView):
            """Stores the name alone, whatever photo was sent."""

            async def save_record(self, context: SaveContext[Product]) -> None:
                context.record.name = context.values[Product.name].get()

        async def save(view: ModelView[Any], name: str) -> None:
            photo = UploadFile(
                io.BytesIO(PNG),
                size=len(PNG),
                filename=name,
                headers=Headers({"content-type": "image/png"}),
            )
            async with database.session() as session:
                record = await session.get(Product, 1)
                await view._saver.save(
                    session,
                    {"name": "Linen shirt", "description": NewFile(photo)},
                    record=record,
                    request=request_from(),
                )

        async def photo() -> str | None:
            async with database.session() as session:
                shirt = await session.get(Product, 1)
                assert shirt is not None
                return shirt.description

        await save(ProductView(), "one.png")
        first = await photo()
        assert first is not None

        await save(NameOnly(), "two.png")

        assert await photo() == first
        assert storage.path_for(first).exists()
