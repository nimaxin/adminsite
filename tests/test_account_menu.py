"""The account menu at the foot of the sidebar."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from tests.models import Order
from tests.test_auth import sign_in, token_from

STYLESHEET = (
    Path(__file__).parent.parent / "src" / "adminsite" / "static" / "adminsite.css"
)


class OrderView(ModelView[Order]):
    fields = ["id", "status", "total"]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def signed_in(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView],
        auth=PasswordAuth({"nima": hash_password("letmein")}),
        secret_key="for-the-session",
        languages=["fa"],
        timezones=["Europe/Paris"],
    )
    async with serve(admin) as client:
        await sign_in(client)
        yield client


class TestTheMenu:
    async def test_it_opens_from_the_name_of_whoever_is_signed_in(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        page = await signed_in.get("/admin/orders")
        menu = page.text.split('<div class="dropdown dropdown-top w-full">', 1)[1]

        assert re.search(
            r'<span class="min-w-0 flex-1 truncate font-medium[^"]*">nima<', menu
        )
        assert 'role="switch"' in menu and "Dark mode" in menu
        assert "Compact lists" in menu
        assert '<span class="whitespace-nowrap">Language</span>' in menu
        assert '<span class="whitespace-nowrap">Time zone</span>' in menu
        assert 'form="sign-out-form"' in menu and "Sign out" in menu

    async def test_its_sign_out_signs_out(self, signed_in: httpx.AsyncClient) -> None:
        page = await signed_in.get("/admin/orders")
        form = re.search(
            r'<form id="sign-out-form" method="post" action="([^"]+)"', page.text
        )
        assert form is not None

        await signed_in.post(form.group(1), data={"_csrf": token_from(page.text)})
        after = await signed_in.get("/admin/orders")

        assert after.status_code == 303
        assert after.headers["location"].endswith("/admin/login")

    async def test_without_signing_in_it_is_the_settings(
        self, database: Database
    ) -> None:
        async with serve(Admin(database, views=[OrderView])) as client:
            page = await client.get("/admin/orders")

        assert re.search(
            r'<span class="min-w-0 flex-1 truncate font-medium[^"]*">Settings</span>',
            page.text,
        )
        assert "Dark mode" in page.text
        assert "sign-out-form" not in page.text
        # One language and one zone: nothing to choose between.
        assert 'name="language"' not in page.text
        assert 'name="timezone"' not in page.text

    async def test_compact_lists_is_a_switch(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        page = await signed_in.get("/admin/orders")
        menu = page.text.split('<div class="dropdown dropdown-top w-full">', 1)[1]
        button = menu.split("Compact lists", 1)[0].rsplit("<button", 1)[1]

        assert 'role="switch"' in button
        assert '@click="switchDensity(); compact = listsAreCompact()"' in button

    def test_its_closed_menus_take_no_room(self) -> None:
        # The language and time zone menus are popovers drawn with daisyUI's
        # menu, whose display wins over the browser's own way of hiding a
        # closed popover: laid out unseen under the account menu, they made
        # the sidebar taller than the window.
        stylesheet = STYLESHEET.read_text(encoding="utf-8")

        assert "[popover]:not(:popover-open){display:none}" in stylesheet


class TestCompactLists:
    async def test_a_page_reads_the_choice_before_it_draws(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        page = await signed_in.get("/admin/orders")

        # In the head, as the theme is, so a list never jumps from one spacing
        # to the other after it is drawn.
        head = page.text.split("</head>", 1)[0]
        assert 'localStorage.getItem("adminsite-density") === "compact"' in head

    async def test_the_rows_take_their_sizes_from_it(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        page = await signed_in.get("/admin/orders")
        rows = page.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        stylesheet = STYLESHEET.read_text(encoding="utf-8")

        assert rows.count("h-(--list-row)") == rows.count("<td")
        assert "px-(--list-cell)" in rows
        assert "--list-row:3rem" in stylesheet
        compact = stylesheet.split("[data-density=compact]{", 1)[1].split("}", 1)[0]
        assert "--list-row:2.25rem" in compact
