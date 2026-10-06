"""Times shown on the clock of the person reading them."""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ModelView
from adminsite._http.activity import ActivityFilters
from adminsite.audit import AuditLog
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError, FieldValidationError
from adminsite.fields import DateTimeField
from adminsite.filters import DateRangeFilter, FilterValue, SQLAlchemyRepository
from adminsite.query import QuerySpec
from adminsite.timezones import (
    activate_timezone,
    current_timezone,
    database_timezone,
    find_timezone,
)
from tests.models import Meeting, Order

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


class MeetingView(ModelView[Meeting]):
    fields = ["title", "starts_at", "ends_at"]


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


class TestShowingATime:
    def test_utc_shows_it_as_kept(self) -> None:
        field = DateTimeField("starts_at")

        assert field.display(datetime(2026, 10, 6, 11, 0)) == "Oct 6, 2026 11:00"

    def test_a_plain_time_is_in_the_database_zone(self) -> None:
        activate_timezone(TEHRAN, PARIS)
        field = DateTimeField("starts_at")

        # 13:00 in Paris, still on summer time, is 11:00 UTC.
        assert field.display(datetime(2026, 10, 6, 13, 0)) == "Oct 6, 2026 14:30"

    def test_a_time_with_its_zone_keeps_it(self) -> None:
        activate_timezone(TEHRAN, PARIS)
        field = DateTimeField("starts_at")

        shown = field.display(datetime(2026, 10, 6, 11, 0, tzinfo=UTC))

        assert shown == "Oct 6, 2026 14:30"

    def test_the_input_starts_on_the_persons_clock(self) -> None:
        activate_timezone(TEHRAN, UTC)
        field = DateTimeField("starts_at")

        assert field.serialize(datetime(2026, 10, 6, 11, 0)) == "2026-10-06T14:30"

    def test_a_format_is_written_on_the_persons_clock(self) -> None:
        activate_timezone(TEHRAN, UTC)
        field = DateTimeField("starts_at", format="{:%H:%M}")

        assert field.text_for(None, datetime(2026, 10, 6, 11, 0)) == "14:30"


class TestReadingATypedTime:
    def test_on_the_persons_clock(self) -> None:
        activate_timezone(TEHRAN, UTC)

        parsed = DateTimeField("starts_at").parse("2026-10-06T14:30")

        assert parsed == datetime(2026, 10, 6, 11, 0)

    def test_kept_in_the_database_zone(self) -> None:
        activate_timezone(TEHRAN, PARIS)

        parsed = DateTimeField("starts_at").parse("2026-10-06T14:30")

        assert parsed == datetime(2026, 10, 6, 13, 0)

    def test_an_offset_typed_with_it_wins(self) -> None:
        activate_timezone(TEHRAN, UTC)

        parsed = DateTimeField("starts_at").parse("2026-10-06T14:30+02:00")

        assert parsed == datetime(2026, 10, 6, 12, 30)

    def test_a_column_that_keeps_the_zone_is_given_it(self) -> None:
        activate_timezone(TEHRAN, UTC)
        field = DateTimeField("starts_at", with_timezone=True)

        parsed = field.parse("2026-10-06T14:30")

        assert parsed.tzinfo is not None
        assert parsed == datetime(2026, 10, 6, 11, 0, tzinfo=UTC)

    @pytest.mark.parametrize("typed", ["2026-03-29T02:30", "2026-10-25T02:30"])
    def test_a_time_the_clocks_skip_or_repeat_is_refused(self, typed: str) -> None:
        activate_timezone(PARIS, UTC)

        with pytest.raises(FieldValidationError, match="Europe/Paris skips or repeats"):
            DateTimeField("starts_at").parse(typed)

    def test_the_column_says_whether_it_keeps_the_zone(
        self, database: Database
    ) -> None:
        view = Admin(database, views=[MeetingView]).views.find("meetings")
        assert view is not None

        kept = view._fields.field_for("starts_at")
        plain = view._fields.field_for("ends_at")

        assert isinstance(kept, DateTimeField) and kept.with_timezone is True
        assert isinstance(plain, DateTimeField) and plain.with_timezone is False


async def meeting(database: Database) -> Meeting | None:
    async with database.session() as session:
        return await session.get(Meeting, 1)


def in_utc(moment: datetime | None) -> datetime | None:
    """A stored time in UTC: a plain one is already, one with its zone is moved."""
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


@pytest.fixture
async def meetings(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(database, views=[MeetingView], secret_key="for-the-session")
    async with serve(admin) as served:
        served.cookies.set("adminsite_browser_timezone", "Asia/Tehran")
        yield served


class TestOnEveryDatabase:
    async def test_a_typed_time_is_saved_as_the_moment_it_names(
        self, meetings: httpx.AsyncClient, database: Database
    ) -> None:
        form = await meetings.get("/admin/meetings/new")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        answer = await meetings.post(
            "/admin/meetings/new",
            data={
                "_csrf": token.group(1),
                "title": "Planning",
                "starts_at": "2026-10-06T14:30",
                "ends_at": "2026-10-06T15:30",
            },
        )
        saved = await meeting(database)

        assert answer.status_code == 303
        assert saved is not None
        assert in_utc(saved.starts_at) == datetime(2026, 10, 6, 11, 0)
        assert in_utc(saved.ends_at) == datetime(2026, 10, 6, 12, 0)

    async def test_a_kept_time_is_shown_on_the_readers_clock(
        self, meetings: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            await session.add(
                Meeting(
                    title="Planning",
                    starts_at=datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
                    ends_at=datetime(2026, 10, 6, 12, 0),
                )
            )
            await session.commit()

        page = await meetings.get("/admin/meetings/1")
        form = await meetings.get("/admin/meetings/1/edit")

        assert "Oct 6, 2026 14:30" in page.text
        assert "Oct 6, 2026 15:30" in page.text
        assert 'value="2026-10-06T14:30"' in form.text
        assert 'value="2026-10-06T15:30"' in form.text


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


class TestTheHistory:
    async def test_shows_when_on_the_readers_clock_and_keeps_changes_in_utc(
        self, database: Database, log: AuditLog
    ) -> None:
        async with database.session() as session:
            await session.add(
                Meeting(title="Planning", ends_at=datetime(2026, 10, 6, 12, 0))
            )
            await session.commit()
        admin = Admin(
            database, views=[MeetingView], audit=log, secret_key="for-the-session"
        )
        async with serve(admin) as served:
            served.cookies.set("adminsite_browser_timezone", "Asia/Tehran")
            form = await served.get("/admin/meetings/1/edit")
            token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
            assert token is not None
            await served.post(
                "/admin/meetings/1/edit",
                data={
                    "_csrf": token.group(1),
                    "title": "Planning",
                    "ends_at": "2026-10-06T16:30",
                },
            )
            page = await served.get("/admin/meetings/1")
        changed = re.search(r"Last changed by Someone on ([^<]+)</p>", page.text)

        # Whoever saved it, the log says what the field held in UTC.
        assert "Oct 6, 2026 12:00 UTC" in page.text
        assert "Oct 6, 2026 13:00 UTC" in page.text
        assert changed is not None
        shown = datetime.strptime(changed.group(1), "%b %d, %Y %H:%M")
        now = datetime.now(TEHRAN).replace(tzinfo=None)
        assert abs(now - shown).total_seconds() < 120


async def plan_two_meetings(database: Database) -> None:
    """One late on Oct 6 in UTC, already Oct 7 in Tehran, and one earlier."""
    async with database.session() as session:
        for title, hour in (("Late", 21), ("Early", 19)):
            await session.add(
                Meeting(
                    title=title,
                    starts_at=datetime(2026, 10, 6, hour, 0, tzinfo=UTC),
                    ends_at=datetime(2026, 10, 6, hour, 0),
                )
            )
        await session.commit()


class TestFilteringByDay:
    @pytest.mark.parametrize("path", ["starts_at", "ends_at"])
    async def test_a_day_is_the_readers_day(
        self, database: Database, path: str
    ) -> None:
        await plan_two_meetings(database)
        activate_timezone(TEHRAN, UTC)
        meetings = SQLAlchemyRepository(Meeting, filters=(DateRangeFilter(path),))

        async with database.session() as session:
            page = await meetings.list(
                session,
                QuerySpec(filters=(FilterValue(path, ("2026-10-07,2026-10-07",)),)),
            )

        assert [found.title for found in page] == ["Late"]

    def test_the_activity_page_reads_the_readers_days(self) -> None:
        activate_timezone(TEHRAN, PARIS)
        asked = ActivityFilters(since=date(2026, 10, 7), until=date(2026, 10, 7))

        query = asked.query(["meetings"])

        # The log keeps UTC, whatever zone the database keeps.
        assert query.since == datetime(2026, 10, 6, 20, 30)
        assert query.until == datetime(2026, 10, 7, 20, 30)


class TestTheApi:
    async def test_sends_times_in_utc_with_their_offset(
        self, database: Database
    ) -> None:
        await plan_two_meetings(database)
        async with serve(Admin(database, views=[MeetingView], api=True)) as served:
            served.cookies.set("adminsite_browser_timezone", "Asia/Tehran")
            record = (await served.get("/admin/-/api/meetings/1")).json()

        assert record["starts_at"] == "2026-10-06T21:00:00Z"
        assert record["ends_at"] == "2026-10-06T21:00:00Z"

    async def test_reads_a_time_with_its_offset(self, database: Database) -> None:
        async with serve(Admin(database, views=[MeetingView], api=True)) as served:
            answer = await served.post(
                "/admin/-/api/meetings",
                json={
                    "title": "Planning",
                    "starts_at": "2026-10-06T14:30:00+03:30",
                    "ends_at": "2026-10-06T15:30:00+03:30",
                },
            )
        saved = await meeting(database)

        assert answer.status_code == 201
        assert saved is not None
        assert in_utc(saved.starts_at) == datetime(2026, 10, 6, 11, 0)
        assert in_utc(saved.ends_at) == datetime(2026, 10, 6, 12, 0)


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
