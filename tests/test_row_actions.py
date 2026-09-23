import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.audit import AuditLog
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.exceptions import AdminSiteError, RefusedError
from adminsite.fields import ChoiceField
from tests.models import Customer, Order, OrderStatus

CARRIERS = (("dhl", "DHL"), ("ups", "UPS"))


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    list_filter = ("status",)

    @action("Confirm", on="record")
    async def confirm(self, record: Order, session: SessionAdapter) -> str:
        record.status = OrderStatus.PAID
        return f"Order {record.id} confirmed."

    @action(
        "Ship",
        on="record",
        confirm="Send this order?",
        inputs=[ChoiceField("carrier", choices=CARRIERS, required=True)],
    )
    async def ship(self, record: Order, session: SessionAdapter, carrier: str) -> str:
        record.status = OrderStatus.SHIPPED
        record.note = f"Sent with {carrier}"
        return f"Order {record.id} sent with {carrier}."

    @action("Download", on="record", permission=Permission.VIEW)
    async def download(self, record: Order, session: SessionAdapter) -> Response:
        return PlainTextResponse(f"order,{record.id}", media_type="text/csv")

    @action("Refuse me", on="record")
    async def refuse(self, record: Order, session: SessionAdapter) -> str:
        raise RefusedError("Not this one.")

    @action("Sync from the provider", on="view", permission=Permission.VIEW)
    async def sync(self, session: SessionAdapter) -> str:
        return "Synced 3 orders."

    @action(
        "Export a summary",
        on="view",
        permission=Permission.VIEW,
        inputs=[ChoiceField("group", choices=(("day", "Day"),), required=True)],
    )
    async def summary(self, session: SessionAdapter, group: str) -> Response:
        return PlainTextResponse(f"summary by {group}")

    @action("Note them all")
    async def note(self, selection: Selection) -> str:
        return f"{await selection.update(note='seen')} noted."

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        # Shipped orders are finished: no confirming them again.
        if action == Permission.EDIT and record is not None:
            return record.status is not OrderStatus.SHIPPED
        return await super().allows(action, request=request, record=record)


class CustomerView(ModelView, model=Customer):
    pass


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView, CustomerView],
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def run(client: httpx.AsyncClient, name: str, **data: str) -> httpx.Response:
    page = await client.get("/admin/orders")
    return await client.post(
        f"/admin/orders/action/{name}",
        data={"_csrf": token_in(page), **data},
        follow_redirects=True,
    )


async def status_of(database: Database, key: int = 1) -> OrderStatus:
    async with database.session() as session:
        order = await session.get(Order, key)
        assert order is not None
        return order.status


class TestDeclaring:
    def test_an_action_runs_on_one_of_three_things(self) -> None:
        view = OrderView()

        assert [item.name for item in view.actions_on("record")] == [
            "confirm",
            "download",
            "refuse",
            "ship",
        ]
        assert [item.name for item in view.actions_on("view")] == ["summary", "sync"]
        assert [item.name for item in view.actions_on("selection")] == ["note"]

    def test_anything_else_is_refused(self) -> None:
        with pytest.raises(AdminSiteError, match="'selection', 'record' or 'view'"):

            class Broken(ModelView, model=Order):
                @action("Nope", on="everything")
                async def nope(self, record: Order) -> str:
                    return ""


class TestOnARecord:
    async def test_it_runs_from_the_row(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "confirm", keys="3")

        assert await status_of(database, 3) is OrderStatus.PAID
        assert "Order 3 confirmed." in answer.text

    async def test_it_lands_on_the_record(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")
        answer = await client.post(
            "/admin/orders/action/confirm",
            data={"_csrf": token_in(page), "keys": "2"},
        )

        assert answer.headers["location"] == "/admin/orders/2"

    async def test_it_asks_first_and_takes_values(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "ship", keys="3", carrier="dhl")

        assert await status_of(database, 3) is OrderStatus.SHIPPED
        assert "Order 3 sent with dhl." in answer.text

    async def test_a_missing_value_is_reported(self, client: httpx.AsyncClient) -> None:
        answer = await run(client, "ship", keys="3")

        assert "Ship was not done." in answer.text

    async def test_a_refusal_keeps_the_record(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "refuse", keys="3")

        assert "Not this one." in answer.text
        assert await status_of(database, 3) is OrderStatus.PENDING

    async def test_an_unknown_record_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await run(client, "confirm", keys="999")

        assert answer.status_code == 404

    async def test_the_buttons_are_on_the_row_and_the_page(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")
        page = await client.get("/admin/orders/2")

        assert 'runRecordAction("confirm", "2")' in listed.text
        assert 'runRecordAction("confirm", "2")' in page.text
        assert 'id="action-form-confirm"' in listed.text

    async def test_a_record_that_refuses_hides_its_button(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")
        shipped = await client.get("/admin/orders/1")

        # Order 1 is shipped, so Confirm is not offered for it.
        assert 'runRecordAction("confirm", "1")' not in listed.text
        assert 'runRecordAction("confirm", "1")' not in shipped.text
        assert 'runRecordAction("download", "1")' in shipped.text

    async def test_it_is_written_to_the_history(
        self, database: Database, tmp_path: Path
    ) -> None:
        log = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
        admin = Admin(
            database, views=[OrderView], audit=log, secret_key="for-the-session"
        )
        async with serve(admin) as client:
            await run(client, "confirm", keys="2")
            entries = await log.history("orders", "2")
        log.close()

        assert [entry.action for entry in entries] == ["Confirm"]
        assert entries[0].message == "Order 2 confirmed."


class TestOnTheView:
    async def test_it_runs_with_nothing_ticked(self, client: httpx.AsyncClient) -> None:
        answer = await run(client, "sync")

        assert "Synced 3 orders." in answer.text

    async def test_its_button_sits_above_the_list(
        self, client: httpx.AsyncClient
    ) -> None:
        listed = await client.get("/admin/orders")

        assert "Sync from the provider" in listed.text
        assert 'runRecordAction("sync", "")' in listed.text


class TestAnswersThatAreNotMessages:
    async def test_a_record_action_can_hand_back_a_file(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")
        answer = await client.post(
            "/admin/orders/action/download",
            data={"_csrf": token_in(page), "keys": "1"},
        )

        assert answer.status_code == 200
        assert answer.text == "order,1"
        assert answer.headers["content-type"].startswith("text/csv")

    async def test_a_view_action_can_too(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/orders")
        answer = await client.post(
            "/admin/orders/action/summary",
            data={"_csrf": token_in(page), "group": "day"},
        )

        assert answer.text == "summary by day"


class TestSelectionActionsStillWork:
    async def test_they_run_over_the_ticked_rows(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await run(client, "note", keys="1")

        assert "1 noted." in answer.text
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.note == "seen"

    async def test_their_bar_is_unchanged(self, client: httpx.AsyncClient) -> None:
        listed = await client.get("/admin/orders")

        assert "Note them all" in listed.text
        assert 'x-show="picked > 0"' in listed.text
