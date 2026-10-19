"""Grants: what an auth provider says each user may do, view by view."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from adminsite import Admin, AdminPage, Grants, ModelView, Permission
from adminsite.actions import Selection, action
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from tests.models import Customer, Order, OrderStatus
from tests.support import request_from
from tests.test_auth import SECRET, sign_in, token_from


class OrderView(ModelView[Order]):
    fields = ["id", "status"]

    @action("Pay the money back", permission="refund")
    async def refund(self, selection: Selection[Order]) -> str:
        return "refunded"

    @action("Hand to the courier", permission="ship")
    async def ship(self, selection: Selection[Order]) -> str:
        return "shipped"


class CustomerView(ModelView[Customer]):
    fields = ["name", "email"]


class SalesReport(AdminPage):
    async def get(self, request: Request) -> Response:
        return PlainTextResponse("sales")


class NoDeletes(ModelView[Order]):
    can_delete = False


class NoShippedEdits(ModelView[Order]):
    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        shipped = record is not None and record.status is OrderStatus.SHIPPED
        if action == Permission.EDIT and shipped:
            return False
        return await super().allows(action, request=request, record=record)


CLERK = Grants({"orders": {"view", "edit", "refund"}})


class Clerk(PasswordAuth):
    """Lets nima see and edit orders, and refund them, and nothing else."""

    async def grants_for(self, user: Any) -> Grants | None:
        return CLERK

    async def authenticate_token(self, token: str) -> Any | None:
        return "robot" if token == "s3cret" else None


@pytest.fixture
async def clerk(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, CustomerView],
        pages=[SalesReport],
        auth=Clerk({"nima": hash_password("letmein")}),
        secret_key=SECRET,
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


class TestGrants:
    def test_everything_allows_anything(self) -> None:
        grants = Grants(everything=True)

        assert grants.allows("orders", Permission.DELETE)
        assert grants.allows("anything", "refund")

    def test_a_permission_is_allowed_where_it_is_granted(self) -> None:
        grants = Grants({"orders": {"edit"}})

        assert grants.allows("orders", Permission.EDIT)
        assert not grants.allows("orders", Permission.DELETE)
        assert not grants.allows("customers", Permission.EDIT)

    def test_any_permission_lets_the_person_see_the_view(self) -> None:
        grants = Grants({"orders": ["edit"], "customers": []})

        assert grants.allows("orders", Permission.VIEW)
        assert grants.allows("orders", Permission.VIEW_DETAIL)
        assert not grants.allows("customers", Permission.VIEW)

    def test_an_action_asks_for_its_own_permission(self) -> None:
        grants = Grants({"orders": {"view", "refund"}})

        assert grants.allows("orders", "refund")
        assert not grants.allows("orders", "ship")


class TestViews:
    async def test_without_grants_the_view_decides_alone(self) -> None:
        view = OrderView()

        assert await view.allows(
            Permission.DELETE, request=request_from("nima"), record=None
        )

    async def test_the_grants_decide_what_the_settings_leave_on(self) -> None:
        view = OrderView()
        request = request_from("nima", CLERK)

        assert await view.allows(Permission.EDIT, request=request, record=None)
        assert await view.allows("refund", request=request, record=None)
        assert not await view.allows(Permission.DELETE, request=request, record=None)
        assert not await view.allows("ship", request=request, record=None)

    async def test_a_setting_that_is_off_wins_over_the_grants(self) -> None:
        view = NoDeletes()
        request = request_from("nima", Grants(everything=True))

        assert not await view.allows(Permission.DELETE, request=request, record=None)

    async def test_an_override_that_calls_super_keeps_the_grants(self) -> None:
        view = NoShippedEdits()
        everything = request_from("nima", Grants(everything=True))
        nothing = request_from("nima", Grants())
        shipped = Order(status=OrderStatus.SHIPPED)
        pending = Order(status=OrderStatus.PENDING)

        assert not await view.allows(
            Permission.EDIT, request=everything, record=shipped
        )
        assert await view.allows(Permission.EDIT, request=everything, record=pending)
        assert not await view.allows(Permission.EDIT, request=nothing, record=pending)


class TestPages:
    async def test_a_page_follows_the_grants(self) -> None:
        page = SalesReport()
        granted = Grants({"-/sales_report": {"view"}})

        assert await page.allows(request_from("nima", granted))
        assert not await page.allows(request_from("nima", CLERK))
        assert await page.allows(request_from("nima"))


class TestTheAdmin:
    async def test_the_sidebar_lists_only_what_is_granted(
        self, clerk: httpx.AsyncClient
    ) -> None:
        await sign_in(clerk)

        page = await clerk.get("/admin/orders")

        assert page.status_code == 200
        assert 'href="/admin/orders"' in page.text
        assert 'href="/admin/customers"' not in page.text
        assert 'href="/admin/-/sales_report"' not in page.text

    async def test_what_is_not_granted_answers_403(
        self, clerk: httpx.AsyncClient
    ) -> None:
        await sign_in(clerk)

        customers = await clerk.get("/admin/customers")
        new_order = await clerk.get("/admin/orders/new")
        report = await clerk.get("/admin/-/sales_report")

        assert customers.status_code == 403
        assert new_order.status_code == 403
        assert report.status_code == 403

    async def test_buttons_follow_the_grants(self, clerk: httpx.AsyncClient) -> None:
        await sign_in(clerk)

        page = await clerk.get("/admin/orders")

        assert 'href="/admin/orders/new"' not in page.text
        assert "Pay the money back" in page.text
        assert "Hand to the courier" not in page.text

    async def test_a_refused_post_changes_nothing(
        self, clerk: httpx.AsyncClient, database: Database
    ) -> None:
        await sign_in(clerk)
        page = await clerk.get("/admin/orders/1/edit")

        response = await clerk.post(
            "/admin/orders/1/delete", data={"_csrf": token_from(page.text)}
        )

        assert page.status_code == 200
        assert response.status_code == 403
        async with database.session() as session:
            assert await session.scalar(select(Order).where(Order.id == 1))

    async def test_the_api_follows_the_grants(self, clerk: httpx.AsyncClient) -> None:
        headers = {"Authorization": "Bearer s3cret"}

        orders = await clerk.get("/admin/-/api/orders", headers=headers)
        customers = await clerk.get("/admin/-/api/customers", headers=headers)
        deleted = await clerk.delete("/admin/-/api/orders/1", headers=headers)

        assert orders.status_code == 200
        assert customers.status_code == 403
        assert deleted.status_code == 403
