import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Html, Message, ModelView
from adminsite.actions import Selection, action
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from tests.models import Order


class OrderView(ModelView, model=Order):
    list_display = ("id", "status")

    @action("Rotate the key", on="record")
    async def rotate(self, record: Order, session: SessionAdapter) -> Message:
        return Message("The new key is ready. It is not shown again.", copy="abc123")

    @action("Export")
    async def queue_export(self, selection: Selection) -> Message:
        return Message(
            "The export is on its way.",
            link="/admin/orders?status=PAID",
            link_text="Paid orders",
        )

    @action("Recheck", on="view")
    async def recheck(self, session: SessionAdapter) -> Html:
        return Html("Queued <b>40</b> checks.")

    @action("Note", on="view")
    async def note(self, session: SessionAdapter) -> str:
        return "Checked <b>nothing</b>."

    @action("Pin", on="view")
    async def pin(self, session: SessionAdapter) -> Message:
        return Message("Read this slowly.", sticky=True)


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(
        database,
        views=[OrderView],
        audit=log,
        api=True,
        secret_key="for-the-session",
    )
    app = Starlette()
    app.mount("/admin", site)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def run(client: httpx.AsyncClient, name: str, **data: str) -> httpx.Response:
    """Run an action as the page does, and return the page it lands on."""
    page = await client.get("/admin/orders")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    return await client.post(
        f"/admin/orders/action/{name}",
        data={"_csrf": token.group(1), **data},
        follow_redirects=True,
    )


def toast(page: httpx.Response) -> str:
    """The message area of a page."""
    found = re.search(r'<div class="toast.*?</div>\s*</div>\s*</div>', page.text, re.S)
    assert found is not None, "no message on the page"
    return found.group(0)


class TestAValueToCopy:
    async def test_it_stays_with_a_copy_button(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "rotate", keys="1")

        shown = toast(page)
        assert "The new key is ready." in shown
        assert re.search(r"<code[^>]*>abc123</code>", shown)
        assert "Copy" in shown
        assert "setTimeout" not in shown

    async def test_the_log_keeps_the_text_but_never_the_value(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await run(client, "rotate", keys="1")

        entries = await log.find(AuditQuery(events=[AuditEvent.ACTION]), limit=5)

        assert entries[0].message == "The new key is ready. It is not shown again."
        assert not any("abc123" in repr(entry) for entry in entries)

    async def test_the_api_answers_with_the_link(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/orders")
        token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        assert token is not None
        answer = await client.post(
            "/admin/-/api/orders/actions/queue_export",
            json={"keys": ["1"]},
            headers={"X-CSRF-Token": token.group(1)},
        )

        assert answer.json() == {
            "message": "The export is on its way.",
            "link": "/admin/orders?status=PAID",
        }


class TestALink:
    async def test_it_follows_the_text(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "queue_export", keys="1")

        assert (
            '<a class="link font-medium" href="/admin/orders?status=PAID">'
            "Paid orders</a>" in toast(page)
        )


class TestMarkup:
    async def test_html_is_written_as_markup(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "recheck")

        assert "Queued <b>40</b> checks." in toast(page)

    async def test_plain_text_is_escaped(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        assert "Checked &lt;b&gt;nothing&lt;/b&gt;." in toast(page)


class TestHowLongItStays:
    async def test_good_news_still_fades(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        assert "setTimeout" in toast(page)

    async def test_a_sticky_message_stays(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "pin")

        assert "Read this slowly." in toast(page)
        assert "setTimeout" not in toast(page)
