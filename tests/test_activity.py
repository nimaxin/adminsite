import re
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.audit import AuditEntry, AuditEvent, AuditLog
from adminsite.auth import PasswordAuth, hash_password
from adminsite.backends.sqlalchemy import Database
from adminsite.http.activity import position_of, read_position
from tests.models import Order, Product

ITEM = re.compile(r'<li class="relative mb-6')
MONDAY = datetime(2026, 9, 14)


class OrderView(ModelView, model=Order):
    display_template = "Order {id}"


class ProductView(ModelView, model=Product):
    pass


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(
        database,
        title="Shop",
        views=[OrderView, ProductView],
        audit=log,
        auth=PasswordAuth({"auditor": hash_password("letmein")}),
        secret_key="for-the-tests",
    )
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
                "username": "auditor",
                "password": "letmein",
                "_csrf": token.group(1),
            },
        )
        yield client


def failed_sign_in(user: str, when: datetime) -> AuditEntry:
    return AuditEntry(
        view="",
        record_key="",
        event=AuditEvent.SIGN_IN_FAILED,
        user=user,
        user_key=user,
        error="The password was wrong.",
        occurred_at=when,
    )


def link(page: httpx.Response, text: str) -> str:
    found = re.search(rf'href="([^"]+)"[^>]*>\s*{text}\s*</a>', page.text)
    assert found is not None, f"no link {text!r}"
    return found.group(1).replace("&amp;", "&")


class TestFilters:
    async def test_every_failed_sign_in_of_one_person_last_week(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record(
            [
                failed_sign_in("nima", MONDAY - timedelta(days=3)),
                failed_sign_in("nima", MONDAY + timedelta(hours=9)),
                failed_sign_in("nima", MONDAY + timedelta(days=6, hours=23)),
                failed_sign_in("lena", MONDAY + timedelta(days=2)),
                failed_sign_in("nima", MONDAY + timedelta(days=8)),
            ]
        )

        page = await client.get(
            "/admin/-/activity",
            params={
                "event": "sign_in_failed",
                "user": "nima",
                "since": "2026-09-14",
                "until": "2026-09-20",
            },
        )

        # The last day counts in full: its 23:00 attempt is in.
        assert len(ITEM.findall(page.text)) == 2
        assert "lena" not in page.text
        assert 'value="sign_in_failed" selected' in page.text
        assert 'value="2026-09-20"' in page.text

    async def test_the_tabs_keep_the_other_filters(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get(
            "/admin/-/activity", params={"event": "updated", "user": "nima"}
        )

        tab = link(page, "Orders")

        assert "view=orders" in tab
        assert "event=updated" in tab
        assert "user=nima" in tab

    async def test_nonsense_in_the_address_is_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get(
            "/admin/-/activity",
            params={
                "event": "exploded",
                "since": "last tuesday",
                "older": "yesterday",
                "view": "secrets",
            },
        )

        assert page.status_code == 200
        assert "Nothing matches" not in page.text

    def test_a_page_end_reads_back(self) -> None:
        entry = AuditEntry(
            view="orders",
            record_key="1",
            event=AuditEvent.UPDATED,
            occurred_at=datetime(2026, 9, 14, 9, 30, 0, 123456),
            id=57,
        )

        assert read_position(position_of(entry)) == (entry.occurred_at, 57)
        assert read_position("nonsense") is None


class TestPaging:
    async def test_a_busy_record_pages_back_to_its_creation(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record(
            [
                AuditEntry(
                    view="orders",
                    record_key="1",
                    event=AuditEvent.CREATED if number == 0 else AuditEvent.UPDATED,
                    user="nima",
                    occurred_at=MONDAY + timedelta(minutes=number),
                )
                for number in range(120)
            ]
        )

        record = await client.get("/admin/orders/1")
        assert "History (20+)" in record.text

        older = link(record, "Older entries are on the Activity page")
        pages = [await client.get(older)]
        while "Older</a>" in pages[-1].text:
            pages.append(await client.get(link(pages[-1], "Older")))

        assert [len(ITEM.findall(page.text)) for page in pages] == [50, 50, 20]
        assert "created" in pages[-1].text
        assert "Newest</a>" in pages[-1].text
        assert "Newest</a>" not in pages[0].text

    async def test_a_quiet_record_shows_everything_on_its_page(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await log.record(
            [
                AuditEntry(
                    view="orders",
                    record_key="1",
                    event=AuditEvent.UPDATED,
                    occurred_at=MONDAY + timedelta(minutes=number),
                )
                for number in range(3)
            ]
        )

        record = await client.get("/admin/orders/1")

        assert "History (3)" in record.text
        assert "Older entries are on the Activity page" not in record.text
