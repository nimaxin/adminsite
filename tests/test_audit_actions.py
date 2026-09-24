import re
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.responses import Response

from adminsite import Admin, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.audit import (
    AuditEntry,
    AuditEvent,
    AuditLog,
    AuditQuery,
    audit_metadata,
)
from adminsite.audit.inputs import looks_secret, recorded_inputs
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.exceptions import RefusedError
from adminsite.fields import DecimalField, StringField
from tests.models import Order, OrderStatus


class OrderView(ModelView, model=Order):
    list_display = ("id", "status", "total")
    list_filter = ("status",)
    form_fields = ("status", "note")

    @action(
        "Top up",
        on="record",
        inputs=[
            DecimalField("amount", required=True),
            StringField("api_key"),
            StringField("reference", secret=True),
            StringField("keyboard"),
        ],
    )
    async def top_up(
        self,
        record: Order,
        session: SessionAdapter,
        amount: Decimal,
        api_key: str | None,
        reference: str | None,
        keyboard: str | None,
    ) -> str:
        record.note = f"Topped up by {amount}"
        record.status = OrderStatus.PAID
        return "Topped up."

    @action("Confirm", on="record")
    async def confirm(self, record: Order, session: SessionAdapter) -> str:
        # The change reaches the database before the refusal, so the
        # rollback has something to undo.
        record.note = "Confirmed"
        await session.flush()
        raise RefusedError("Already paid.")

    @action("Break", on="record")
    async def crash(self, record: Order, session: SessionAdapter) -> str:
        return str(1 // 0)

    @action("Download archive")
    async def download(self, selection: Selection) -> Response:
        records = await selection.records()
        return Response(f"{len(records)} orders", media_type="application/zip")

    @action("Rotate the key", on="record", audit_answer=False)
    async def rotate(self, record: Order, session: SessionAdapter) -> str:
        return "Your new key: abc123"

    @action("Sync from the provider", on="view", permission=Permission.VIEW)
    async def sync(self, session: SessionAdapter) -> str:
        return "Synced 3 orders."

    @action("Pay out", on="record", permission="pay_out")
    async def pay_out(self, record: Order, session: SessionAdapter) -> str:
        return "Paid out."

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        if action == "pay_out":
            return False
        return await super().allows(action, request=request, record=record)


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(database, views=[OrderView], audit=log, secret_key="for-the-session")
    app = Starlette()
    app.mount("/admin", site)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"user-agent": "Firefox/140"},
    ) as client:
        yield client


async def run(
    client: httpx.AsyncClient, name: str, data: dict[str, Any] | None = None
) -> httpx.Response:
    page = await client.get("/admin/orders")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    return await client.post(
        f"/admin/orders/action/{name}", data={"_csrf": token.group(1), **(data or {})}
    )


async def entries(log: AuditLog) -> list[AuditEntry]:
    return await log.find(AuditQuery(events=[AuditEvent.ACTION]), limit=20)


class TestWhatItWasRunWith:
    async def test_a_top_up_records_the_amount_and_hides_the_secrets(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(
            client,
            "top_up",
            {
                "keys": "1",
                "amount": "50",
                "api_key": "sk-live-123",
                "reference": "INV-9",
                "keyboard": "qwerty",
            },
        )

        entry = (await entries(log))[0]

        assert entry.inputs == {
            "amount": "50",
            "api_key": "***",
            "reference": "***",
            "keyboard": "qwerty",
        }
        assert entry.message == "Topped up."
        assert entry.succeeded

    async def test_a_record_action_records_what_it_changed(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "top_up", {"keys": "1", "amount": "50"})

        entry = (await entries(log))[0]

        assert entry.record_key == "1"
        assert entry.changes["note"][1] == "Topped up by 50"
        assert entry.changes["status"][1] == "Paid"

    async def test_the_history_shows_it(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "top_up", {"keys": "1", "amount": "50", "api_key": "x"})

        page = await client.get("/admin/orders/1")

        assert "ran Top up" in page.text
        assert re.search(r"<dt[^>]*>Amount</dt>\s*<dd[^>]*>50</dd>", page.text)
        assert re.search(r"<dt[^>]*>Api key</dt>\s*<dd[^>]*>\*\*\*</dd>", page.text)

    def test_a_name_made_of_a_secret_word_is_hidden(self) -> None:
        assert looks_secret("password")
        assert looks_secret("api_key")
        assert looks_secret("newPassword")
        assert looks_secret("pin-code")
        assert not looks_secret("keyboard")
        assert not looks_secret("monkey")

    def test_a_file_is_kept_by_name_type_and_size(self) -> None:
        from io import BytesIO

        from starlette.datastructures import Headers, UploadFile

        upload = UploadFile(
            BytesIO(b"a,b\n1,2\n"),
            size=8,
            filename="rows.csv",
            headers=Headers({"content-type": "text/csv"}),
        )

        kept = recorded_inputs([StringField("rows")], {"rows": upload})

        assert kept == {"rows": {"file": "rows.csv", "type": "text/csv", "size": 8}}


class TestAnAnswerKeptOutOfTheLog:
    async def test_a_new_key_never_reaches_the_log(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "rotate", {"keys": "1"})
        shown = await client.get("/admin/orders")
        record = await client.get("/admin/orders/1")
        activity = await client.get("/admin/-/activity")

        entry = (await entries(log))[0]
        everything = await log.find(AuditQuery(), limit=100)

        # The person who ran it sees the key once; the log never holds it.
        assert "abc123" in shown.text
        assert (entry.action, entry.record_key, entry.message) == (
            "Rotate the key",
            "1",
            None,
        )
        assert entry.user_agent == "Firefox/140"
        assert not any("abc123" in repr(item) for item in everything)
        assert "abc123" not in record.text
        assert "abc123" not in activity.text


class TestEveryAction:
    async def test_an_action_on_the_whole_view_is_recorded(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "sync")
        page = await client.get("/admin/-/activity")

        entry = (await entries(log))[0]

        assert (entry.view, entry.record_key) == ("orders", "")
        assert entry.message == "Synced 3 orders."
        assert "ran Sync from the provider on Orders" in page.text

    async def test_a_download_records_who_took_which_records(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        answer = await run(client, "download", {"keys": ["1", "2", "3"]})

        found = await entries(log)

        assert answer.headers["content-type"] == "application/zip"
        assert sorted(entry.record_key for entry in found) == ["1", "2", "3"]
        assert len({entry.batch for entry in found}) == 1
        assert {entry.user_agent for entry in found} == {"Firefox/140"}
        assert all(entry.action == "Download archive" for entry in found)


class TestWhatFailed:
    async def test_a_refusal_is_recorded_with_its_message(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "confirm", {"keys": "1"})
        page = await client.get("/admin/orders/1")

        found = await entries(log)

        assert len(found) == 1
        assert found[0].error == "Already paid."
        assert not found[0].succeeded
        assert "Already paid." in page.text

    async def test_a_fault_is_recorded_by_its_kind(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        with pytest.raises(ZeroDivisionError):
            await run(client, "crash", {"keys": "1"})

        found = await entries(log)

        assert found[0].error == "It stopped with an error: ZeroDivisionError."

    async def test_an_action_the_person_may_not_run_is_recorded(
        self, client: httpx.AsyncClient, log: AuditLog, database: Database
    ) -> None:
        answer = await run(client, "pay_out", {"keys": "1"})

        found = await entries(log)

        assert answer.status_code == 403
        assert found[0].action == "Pay out"
        assert found[0].error is not None

    async def test_the_failed_work_is_still_undone(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            before = order.note

        await run(client, "confirm", {"keys": "1"})

        async with database.session() as session:
            order = await session.get(Order, 1)
            assert order is not None
            assert order.note == before


class TestExporting:
    async def test_an_export_records_which_list_was_taken(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        answer = await client.get("/admin/orders/export?status=PAID&q=lena&page=2")
        page = await client.get("/admin/-/activity")

        entry = (await log.find(AuditQuery(events=[AuditEvent.EXPORTED]), limit=1))[0]

        assert answer.status_code == 200
        assert entry.view == "orders"
        assert entry.inputs == {"status": "PAID", "q": "lena"}
        assert "exported Orders" in page.text


class TestTheLogInTheSameDatabase:
    async def test_a_failure_is_written_after_the_rollback(
        self, database: Database
    ) -> None:
        # Written any earlier, the entry would wait on the locks the failed
        # transaction holds, or be undone with it.
        def start_over(session: Session) -> None:
            audit_metadata.drop_all(session.connection())

        async with database.session() as session:
            await session.run(start_over)
            await session.commit()
        log = AuditLog(database, create_table=True)
        site = Admin(
            database, views=[OrderView], audit=log, secret_key="for-the-session"
        )
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await run(client, "confirm", {"keys": "1"})
            await run(client, "top_up", {"keys": "1", "amount": "5"})

        found = await entries(log)

        assert [entry.succeeded for entry in found] == [True, False]
        assert found[1].error == "Already paid."
