"""Delete marks a record of a view with soft_delete, and Restore brings it back."""

import html
import re
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, Grants, ModelView, Permission
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.filters import BooleanFilter
from tests.models import Plan, Subscription
from tests.support import counted, request_from


class PlanView(ModelView[Plan]):
    fields = ["id", "name", "deleted_at"]
    searchable_fields = ("name",)
    soft_delete = Plan.deleted_at


class SubscriptionView(ModelView[Subscription]):
    fields = ["id", "plan"]
    list_filters = [Subscription.plan]


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(
        database,
        views=[PlanView, SubscriptionView],
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


async def add_plans(
    database: Database, *names: str, deleted: bool = False
) -> list[str]:
    """Plans each held by a subscription, so none of them could really be deleted."""
    when = datetime(2026, 1, 2, 3, 4) if deleted else None
    async with database.session() as session:
        made = [Plan(name=name, deleted_at=when) for name in names]
        for plan in made:
            await session.add(plan)
            await session.add(Subscription(plan=plan))
        await session.commit()
        return [str(plan.id) for plan in made]


async def deleted_at(database: Database) -> dict[str, datetime | None]:
    """Each plan's name, with the time a delete marked it."""
    async with database.session() as session:
        rows = await session.execute(select(Plan.name, Plan.deleted_at))
        return dict(rows.all())


async def post(client: httpx.AsyncClient, address: str, **data: Any) -> httpx.Response:
    """Post as a page does, with the token the list hands out."""
    page = await client.get("/admin/plans")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert token is not None
    return await client.post(
        address, data={"_csrf": token.group(1), **data}, follow_redirects=True
    )


def offered(answer: httpx.Response) -> list[str]:
    """The names a lookup offers."""
    found = re.findall(
        r'<span class="min-w-0 flex-1 break-words">([^<]*)</span>', answer.text
    )
    return [html.unescape(name) for name in found]


async def names_in_api(client: httpx.AsyncClient, query: str = "") -> list[str]:
    answer = await client.get(f"/admin/-/api/plans{query}")
    assert answer.status_code == 200
    return [item["name"] for item in answer.json()["items"]]


class TestDelete:
    async def test_the_record_stays_with_the_time_it_was_deleted(
        self, client: httpx.AsyncClient, database: Database, log: AuditLog
    ) -> None:
        [key] = await add_plans(database, "Current plan")

        page = await post(client, f"/admin/plans/{key}/delete")

        assert "Plan deleted." in page.text
        assert (await deleted_at(database))["Current plan"] is not None
        entries = await log.find(AuditQuery(view="plans"), limit=10)
        assert [entry.event for entry in entries] == [AuditEvent.DELETED]

    async def test_the_ticked_rows_are_marked_together(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        keys = await add_plans(database, "Basic", "Pro")

        page = await post(client, "/admin/plans/action/delete_selected", keys=keys)

        assert "2 plans deleted." in page.text
        assert None not in (await deleted_at(database)).values()

    async def test_the_confirmation_says_it_can_be_restored(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        [key] = await add_plans(database, "Current plan")

        page = await client.get(f"/admin/plans/{key}")

        assert "You can restore it later." in page.text
        assert "This cannot be undone." not in page.text


class TestTheList:
    @pytest.fixture(autouse=True)
    async def plans(self, database: Database) -> None:
        await add_plans(database, "Current plan")
        await add_plans(database, "Retired plan", deleted=True)

    async def test_deleted_records_are_left_out(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/plans")

        assert "Current plan" in page.text
        assert "Retired plan" not in page.text

    async def test_the_deleted_filter_shows_them_alone(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/plans?deleted=true")

        assert "Retired plan" in page.text
        assert "Current plan" not in page.text

    async def test_the_total_counts_the_records_shown(
        self, client: httpx.AsyncClient
    ) -> None:
        assert "1 plan" in await counted(client, "/admin/plans")
        assert "1 plan" in await counted(client, "/admin/plans?deleted=true")

    async def test_the_api_reads_them_only_when_asked(
        self, client: httpx.AsyncClient
    ) -> None:
        assert await names_in_api(client) == ["Current plan"]
        assert await names_in_api(client, "?deleted=true") == ["Retired plan"]

    async def test_ticked_rows_are_offered_delete_or_restore_as_they_fit(
        self, client: httpx.AsyncClient
    ) -> None:
        shown = (await client.get("/admin/plans")).text
        deleted = (await client.get("/admin/plans?deleted=true")).text

        assert "/action/delete_selected" in shown
        assert "/action/restore_selected" not in shown
        assert "/action/restore_selected" in deleted
        assert "/action/delete_selected" not in deleted


class TestADeletedRecord:
    async def test_its_page_still_opens_and_says_so(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        [key] = await add_plans(database, "Retired plan", deleted=True)

        page = await client.get(f"/admin/plans/{key}")

        assert page.status_code == 200
        assert "Deleted on" in page.text
        assert f"/admin/plans/{key}/edit" not in page.text
        assert "confirm-delete" not in page.text

    async def test_it_cannot_be_changed(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        [key] = await add_plans(database, "Retired plan", deleted=True)

        answer = await client.get(f"/admin/plans/{key}/edit")

        assert answer.status_code == 403

    async def test_restore_brings_it_back(
        self, client: httpx.AsyncClient, database: Database, log: AuditLog
    ) -> None:
        [key] = await add_plans(database, "Retired plan", deleted=True)

        page = await post(client, "/admin/plans/action/restore", keys=[key])

        assert "1 plan restored." in page.text
        assert (await deleted_at(database))["Retired plan"] is None
        [entry] = await log.find(AuditQuery(view="plans"), limit=10)
        assert entry.event is AuditEvent.ACTION
        assert entry.action == "Restore"

    async def test_the_deleted_list_restores_every_match(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        await add_plans(database, "Basic", "Pro", deleted=True)
        await add_plans(database, "Current plan")

        page = await post(
            client, "/admin/plans/action/restore_selected?deleted=true", everything="1"
        )

        assert "2 plans restored." in page.text
        assert set((await deleted_at(database)).values()) == {None}


class TestWhoMayRestore:
    live = Plan(name="Current plan")
    gone = Plan(name="Retired plan", deleted_at=datetime(2026, 1, 2))

    async def test_only_a_deleted_record_can_be_restored(self) -> None:
        view, request = PlanView(), request_from()

        assert await view.allows(Permission.RESTORE, request=request, record=self.gone)
        assert not await view.allows(
            Permission.RESTORE, request=request, record=self.live
        )

    async def test_a_deleted_record_is_neither_changed_nor_deleted_again(
        self,
    ) -> None:
        view, request = PlanView(), request_from()

        for permission in (Permission.EDIT, Permission.DELETE):
            assert not await view.allows(permission, request=request, record=self.gone)
            assert await view.allows(permission, request=request, record=self.live)

    async def test_it_is_granted_with_delete(self) -> None:
        view = PlanView()
        deletes = request_from(grants=Grants({"plans": {"view", "delete"}}))
        edits = request_from(grants=Grants({"plans": {"view", "edit"}}))

        assert await view.allows(Permission.RESTORE, request=deletes, record=self.gone)
        assert not await view.allows(
            Permission.RESTORE, request=edits, record=self.gone
        )

    async def test_a_view_that_never_deletes_never_restores(self) -> None:
        class Kept(PlanView):
            can_delete = False

        names = [item.name for item in Kept().get_actions(request_from())]

        assert "restore" not in names
        assert "restore_selected" not in names


class TestPickers:
    @pytest.fixture(autouse=True)
    async def plans(self, database: Database) -> None:
        await add_plans(database, "Current plan")
        await add_plans(database, "Retired plan", deleted=True)

    async def test_a_form_offers_no_deleted_record(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/subscriptions/lookup/plan")

        assert "Current plan" in offered(answer)
        assert "Retired plan" not in offered(answer)

    async def test_a_filter_still_finds_records_by_one(
        self, client: httpx.AsyncClient
    ) -> None:
        answer = await client.get("/admin/subscriptions/filter/plan/lookup")

        assert {"Current plan", "Retired plan"} <= set(offered(answer))


class TestTheSetting:
    def test_a_column_that_holds_no_time_is_refused(self) -> None:
        class ByName(ModelView[Plan]):
            soft_delete = Plan.name

        with pytest.raises(AdminSiteError, match="not a date and time column"):
            ByName()

    def test_a_filter_by_the_same_name_is_refused(self) -> None:
        class Named(ModelView[Plan]):
            soft_delete = Plan.deleted_at
            list_filters = [BooleanFilter("deleted", path="id")]

        with pytest.raises(AdminSiteError, match="filter named 'deleted'"):
            Named()
