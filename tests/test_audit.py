import re
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, inspect
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.actions import Selection, action
from adminsite.audit import AuditEntry, AuditEvent, AuditLog, as_json, diff
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import RefusedError
from adminsite.fields import ChoiceField
from adminsite.views.writing import SaveContext
from tests.models import Order, OrderStatus, Product
from tests.support import spare_product


class ProductView(ModelView, model=Product):
    form_fields = ("name", "price", "description")


class OrderView(ModelView, model=Order):
    display_template = "Order {id}"
    list_display = ("id", "customer.name", "status", "total")
    list_filter = ("status",)
    form_fields = ("customer", "status", "note", "created_at")

    @action(
        "Mark as shipped",
        inputs=[ChoiceField("carrier", choices=(("dhl", "DHL"),), required=True)],
    )
    async def ship(self, selection: Selection, carrier: str) -> str:
        changed = await selection.update(status=OrderStatus.SHIPPED)
        return f"{changed} orders shipped with {carrier}."

    @action("Remove")
    async def remove(self, selection: Selection) -> str:
        removed = await selection.delete()
        return f"{removed} removed."


class RefusingProducts(ModelView, model=Product):
    name = "refusing"
    form_fields = ("name", "price")

    async def after_save(self, context: SaveContext) -> None:
        raise RefusedError("Not today.")


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
def admin(database: Database, log: AuditLog) -> Admin:
    site = Admin(database, title="Shop", audit=log)
    site.add_view(ProductView)
    site.add_view(OrderView)
    site.add_view(RefusingProducts)
    return site


@pytest.fixture
def client(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestTheLog:
    async def test_the_default_file_gets_its_table(self, log: AuditLog) -> None:
        await log.record(
            [AuditEntry(view="products", record_key="1", event=AuditEvent.CREATED)]
        )

        found = await log.history("products", "1")

        assert len(found) == 1
        assert found[0].event is AuditEvent.CREATED

    async def test_history_is_newest_first(self, log: AuditLog) -> None:
        await log.record(
            [
                AuditEntry(
                    view="products",
                    record_key="1",
                    event=AuditEvent.CREATED,
                    occurred_at=datetime(2026, 9, 1),
                ),
                AuditEntry(
                    view="products",
                    record_key="1",
                    event=AuditEvent.UPDATED,
                    occurred_at=datetime(2026, 9, 2),
                ),
            ]
        )

        found = await log.history("products", "1")

        assert [entry.event for entry in found] == [
            AuditEvent.UPDATED,
            AuditEvent.CREATED,
        ]

    async def test_changes_survive_the_trip(self, log: AuditLog) -> None:
        await log.record(
            [
                AuditEntry(
                    view="products",
                    record_key="1",
                    event=AuditEvent.UPDATED,
                    changes={"name": ("Hat", "Cap")},
                    user="nima",
                )
            ]
        )

        entry = (await log.history("products", "1"))[0]

        assert entry.changes == {"name": ("Hat", "Cap")}
        assert entry.user == "nima"

    def test_your_own_database_is_left_alone_unless_asked(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'main.db'}")

        assert AuditLog(engine).create_table is False
        assert AuditLog(engine, create_table=True).create_table is True
        engine.dispose()

    async def test_it_can_live_in_your_own_database(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'main.db'}")
        log = AuditLog(engine, create_table=True)

        await log.record(
            [AuditEntry(view="orders", record_key="3", event=AuditEvent.DELETED)]
        )

        assert "adminsite_audit_log" in inspect(engine).get_table_names()
        engine.dispose()

    def test_values_are_written_readably(self) -> None:
        assert as_json(Decimal("12.50")) == "12.50"
        assert as_json(OrderStatus.PAID) == "PAID"
        assert as_json(datetime(2026, 9, 1, 10, 30)) == "2026-09-01T10:30:00"

    def test_a_diff_keeps_only_what_changed(self) -> None:
        changes = diff({"name": "Hat", "price": "10"}, {"name": "Cap", "price": "10"})

        assert changes == {"name": ("Hat", "Cap")}


class TestSavesAndDeletes:
    async def test_creating_a_record_is_written_down(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        response = await client.post(
            "/admin/products/new", data={"name": "Felt hat", "price": "42.00"}
        )
        key = response.headers["location"].rsplit("/", 1)[-1]

        entry = (await log.history("products", key))[0]

        assert entry.event is AuditEvent.CREATED
        assert entry.changes["name"] == (None, "Felt hat")
        assert entry.record_title == "Felt hat"

    async def test_a_change_records_before_and_after(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post(
            "/admin/orders/1/edit",
            data={
                "customer": "2",
                "status": "PAID",
                "note": "Checked",
                "created_at": "2026-09-01T10:30",
            },
        )

        entry = (await log.history("orders", "1"))[0]

        assert entry.event is AuditEvent.UPDATED
        assert entry.changes["status"] == ("Shipped", "Paid")
        assert entry.changes["customer"] == ("Lena Fischer", "Marco Rossi")
        assert entry.changes["note"] == ("", "Checked")
        assert "created_at" not in entry.changes

    async def test_saving_without_changes_writes_nothing(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post(
            "/admin/orders/1/edit",
            data={
                "customer": "1",
                "status": "SHIPPED",
                "note": "",
                "created_at": "2026-09-01T10:30",
            },
        )

        assert await log.history("orders", "1") == []

    async def test_deleting_keeps_what_the_record_held(
        self, client: httpx.AsyncClient, database: Database, log: AuditLog
    ) -> None:
        key = await spare_product(database)

        await client.post(f"/admin/products/{key}/delete")

        entry = (await log.history("products", str(key)))[0]
        assert entry.event is AuditEvent.DELETED
        assert entry.changes["name"] == ("Gift card", None)

    async def test_a_refused_save_leaves_no_trace(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post("/admin/refusing/new", data={"name": "Nope", "price": "1.00"})

        assert await log.recent() == []

    async def test_without_auditing_nothing_is_recorded(
        self, database: Database, log: AuditLog
    ) -> None:
        plain = Admin(database, title="Shop")
        plain.add_view(ProductView)
        app = Starlette()
        app.mount("/admin", plain)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await client.post(
                "/admin/products/new", data={"name": "Quiet", "price": "1.00"}
            )

        assert await log.recent() == []


class TestBulkActions:
    async def test_each_affected_row_gets_its_own_entry(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post(
            "/admin/orders/action/ship",
            data={"keys": ["3", "7"], "carrier": "dhl"},
        )

        third = (await log.history("orders", "3"))[0]
        seventh = (await log.history("orders", "7"))[0]

        assert third.event is AuditEvent.ACTION
        assert third.action == "Mark as shipped"
        assert third.changes["status"] == ("Pending", "Shipped")
        assert third.batch is not None
        assert third.batch == seventh.batch

    async def test_selecting_every_match_is_recorded_row_by_row(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post(
            "/admin/orders/action/ship?status=PENDING",
            data={"everything": "1", "carrier": "dhl"},
        )

        entries = await log.recent(view="orders")

        # The rows no longer match the filter once shipped, so the keys have
        # to be read before the action runs.
        assert len(entries) == 2
        assert {entry.record_key for entry in entries} == {"3", "7"}

    async def test_a_bulk_delete_keeps_what_each_row_held(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await client.post("/admin/orders/action/remove", data={"keys": ["7"]})

        entry = (await log.history("orders", "7"))[0]

        assert entry.action == "Remove"
        assert entry.changes["status"] == ("Pending", None)


class TestPages:
    async def test_a_record_shows_its_history(self, client: httpx.AsyncClient) -> None:
        await client.post(
            "/admin/orders/1/edit",
            data={
                "customer": "1",
                "status": "PAID",
                "note": "",
                "created_at": "2026-09-01T10:30",
            },
        )

        response = await client.get("/admin/orders/1")

        assert "History (1)" in response.text
        assert "Shipped" in response.text
        assert "Paid" in response.text

    async def test_the_activity_page_lists_recent_changes(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post(
            "/admin/products/new", data={"name": "Wool cap", "price": "15.00"}
        )

        response = await client.get("/admin/-/activity")

        assert response.status_code == 200
        assert "Wool cap" in response.text
        assert "created" in response.text

    async def test_the_activity_page_can_show_one_model(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post(
            "/admin/products/new", data={"name": "Wool cap", "price": "15.00"}
        )

        response = await client.get("/admin/-/activity?view=orders")

        assert "Wool cap" not in response.text

    async def test_the_sidebar_links_to_activity(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/")

        assert "/admin/-/activity" in response.text

    async def test_without_auditing_there_is_no_activity_page(
        self, database: Database
    ) -> None:
        plain = Admin(database, title="Shop")
        plain.add_view(ProductView)
        app = Starlette()
        app.mount("/admin", plain)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/admin/-/activity")

            assert response.status_code == 404

    async def test_audit_true_uses_the_default_file(
        self, database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        site = Admin(database, audit=True)

        assert site.audit is not None
        assert site.audit.create_table is True
        site.audit.close()


class TestWhoDidIt:
    async def test_the_signed_in_user_is_recorded(
        self, database: Database, log: AuditLog
    ) -> None:
        site = Admin(
            database,
            title="Shop",
            audit=log,
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key="secret-for-the-tests",
        )
        site.add_view(ProductView)
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/login")
            token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
            assert token is not None
            await client.post(
                "/admin/login",
                data={
                    "username": "nima",
                    "password": "letmein",
                    "_csrf": token.group(1),
                },
            )
            await client.post(
                "/admin/products/new",
                data={"name": "Signed", "price": "5.00", "_csrf": token.group(1)},
            )

        entry = (await log.recent())[0]
        assert entry.user == "nima"
