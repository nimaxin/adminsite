"""Times shown on the clock of the person reading them."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC
from zoneinfo import ZoneInfo

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ModelView
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.timezones import (
    activate_timezone,
    current_timezone,
    database_timezone,
    find_timezone,
)
from tests.models import Order

TEHRAN = ZoneInfo("Asia/Tehran")
PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture(autouse=True)
def utc_afterwards() -> Iterator[None]:
    yield
    activate_timezone(UTC, UTC)


def asking(cookies: str = "") -> Request:
    """A request carrying these cookies, such as the browser's time zone."""
    headers = [(b"cookie", cookies.encode())] if cookies else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


class OrderView(ModelView[Order]):
    fields = ["id", "created_at", "total"]


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


class TestFindingAZone:
    def test_a_name_from_the_time_zone_database(self) -> None:
        assert find_timezone("Asia/Tehran") == TEHRAN
        assert find_timezone("America/Argentina/Buenos_Aires") is not None
        assert find_timezone("UTC") is UTC

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "Mars/Olympus",
            "Asia",
            "../etc/passwd",
            "/etc/localtime",
            "Asia/Tehran\x00",
            "Asia/Tehran; path=/",
            "a" * 300,
        ],
    )
    def test_anything_else_is_no_zone(self, name: str) -> None:
        assert find_timezone(name) is None


class TestTheSettings:
    def test_an_unknown_zone_stops_the_admin(self, database: Database) -> None:
        with pytest.raises(AdminSiteError, match="No time zone is called 'Asia/Tehrn'"):
            Admin(database, timezone="Asia/Tehrn")

    def test_so_does_an_unknown_database_zone(self, database: Database) -> None:
        with pytest.raises(AdminSiteError, match="'Mars/Olympus'"):
            Admin(database, database_timezone="Mars/Olympus")


class TestTheZoneOfARequest:
    def test_utc_until_the_browser_says(self, database: Database) -> None:
        admin = Admin(database)

        assert admin.timezone_for(asking()) == "UTC"

    def test_the_admins_zone_until_the_browser_says(self, database: Database) -> None:
        admin = Admin(database, timezone="Europe/Paris")

        assert admin.timezone_for(asking()) == "Europe/Paris"

    def test_the_browsers_zone_once_it_has_said(self, database: Database) -> None:
        admin = Admin(database, timezone="Europe/Paris")

        found = admin.timezone_for(asking("adminsite_browser_timezone=Asia/Tehran"))

        assert found == "Asia/Tehran"

    def test_a_zone_nobody_knows_is_ignored(self, database: Database) -> None:
        admin = Admin(database, timezone="Europe/Paris")

        found = admin.timezone_for(asking("adminsite_browser_timezone=Mars/Olympus"))

        assert found == "Europe/Paris"

    def test_each_request_shows_times_on_its_clock(self, database: Database) -> None:
        admin = Admin(database, database_timezone="Europe/Paris")
        request = asking("adminsite_browser_timezone=Asia/Tehran")

        admin.speak(request)

        assert current_timezone.get() == TEHRAN
        assert database_timezone.get() == PARIS
        assert request.scope["adminsite_timezone"] == "Asia/Tehran"


@pytest.fixture
async def client(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView],
        auth=PasswordAuth({"nima": hash_password("letmein")}),
        secret_key="for-the-session",
    )
    async with serve(admin) as served:
        yield served


class TestTheBrowserSaysItsZone:
    async def test_on_the_sign_in_page(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/login")

        assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in page.text
        assert '"adminsite_browser_timezone=" + zone' in page.text
        assert '"; path=" + "/admin/"' in page.text

    async def test_on_every_other_page(self, database: Database) -> None:
        async with serve(Admin(database, views=[OrderView])) as served:
            page = await served.get("/admin/orders")

        assert '"adminsite_browser_timezone=" + zone' in page.text
