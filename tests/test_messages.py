import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, Html, Message, ModelView, RefusedError
from adminsite.actions import Selection, action
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.database import Database, SessionAdapter
from tests.models import Order


class OrderView(ModelView[Order]):
    fields = ["id", "status"]

    @action("Rotate the key", on="record")
    async def rotate(self, record: Order, session: SessionAdapter) -> Message:
        return Message("The new key is ready. It is not shown again.", copy="abc123")

    @action("Export")
    async def queue_export(self, selection: Selection[Order]) -> Message:
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

    @action("Close the books", on="view")
    async def close_books(self, session: SessionAdapter) -> str:
        raise RefusedError("The books close at the end of the month.")


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


def messages(page: httpx.Response) -> str:
    """The messages of a page, at the end of its header."""
    found = re.search(r'<div id="messages".*?</header>', page.text, re.S)
    assert found is not None, "no place for messages on the page"
    assert "role=" in found.group(0), "no message on the page"
    return found.group(0)


class TestAValueToCopy:
    async def test_it_shows_with_a_copy_button(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "rotate", keys="1")

        shown = messages(page)
        assert "The new key is ready." in shown
        assert re.search(r"<code[^>]*>abc123</code>", shown)
        assert "Copy" in shown

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

        assert re.search(
            r'The export is on its way\. <a class="[^"]*" '
            r'href="/admin/orders\?status=PAID">Paid orders</a>',
            messages(page),
        )


class TestMarkup:
    async def test_html_is_written_as_markup(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "recheck")

        assert "Queued <b>40</b> checks." in messages(page)

    async def test_plain_text_is_escaped(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        assert "Checked &lt;b&gt;nothing&lt;/b&gt;." in messages(page)


class TestWhereItShows:
    async def test_it_is_under_the_heading(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        header = page.text.index("<header")
        assert header < page.text.index('id="messages"') < page.text.index("</header>")
        assert "Checked &lt;b&gt;nothing&lt;/b&gt;." in messages(page)

    async def test_it_stays_until_it_is_closed(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        shown = messages(page)
        assert "setTimeout" not in shown
        assert 'aria-label="Close"' in shown

    async def test_it_is_shown_once(self, client: httpx.AsyncClient) -> None:
        await run(client, "note")

        again = await client.get("/admin/orders")

        assert "Checked" not in again.text


class TestHowItIsAnnounced:
    async def test_news_is_announced_politely(self, client: httpx.AsyncClient) -> None:
        page = await run(client, "note")

        shown = messages(page)
        assert 'role="status"' in shown
        assert "message-error" not in shown

    async def test_a_problem_is_announced_at_once(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await run(client, "close_books")

        shown = messages(page)
        assert 'role="alert"' in shown
        assert "message-error" in shown
        assert "The books close at the end of the month." in shown
