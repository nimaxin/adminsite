"""The Users and Roles pages, which only superusers open."""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from adminsite import Admin, AdminPage, ModelView
from adminsite.actions import Selection, action
from adminsite.audit import AuditLog
from adminsite.database import Database
from adminsite.users import AdminUsers, Role, User
from tests.models import Customer, Order
from tests.support import Backend, count_queries
from tests.test_accessibility import Controls
from tests.test_auth import SECRET, token_from
from tests.test_users import PASSWORD, fresh_tables


class OrderView(ModelView[Order]):
    fields = ["id", "status"]
    can_import = True

    @action("Pay the money back", permission="refund")
    async def refund(self, selection: Selection[Order]) -> str:
        return "refunded"


class CustomerView(ModelView[Customer]):
    fields = ["name", "email"]


class SalesReport(AdminPage):
    async def get(self, request: Request) -> Response:
        return PlainTextResponse("sales")


@dataclass
class Site:
    users: AdminUsers
    client: httpx.AsyncClient
    database: Database
    admin: Admin

    async def sign_in(self, username: str, password: str = PASSWORD) -> None:
        page = await self.client.get("/admin/login")
        await self.client.post(
            "/admin/login",
            data={
                "username": username,
                "password": password,
                "_csrf": token_from(page.text),
            },
        )

    async def post(self, address: str, data: dict[str, Any]) -> httpx.Response:
        """Send a form as the browser would, with the page's own token."""
        page = await self.client.get("/admin/")
        return await self.client.post(
            address, data={**data, "_csrf": token_from(page.text)}
        )

    async def user(self, username: str) -> User:
        async with self.database.session() as session:
            found: User | None = await session.scalar(
                select(User).where(User.username == username)
            )
        assert found is not None
        return found

    async def role(self, name: str) -> Role:
        async with self.database.session() as session:
            found: Role | None = await session.scalar(
                select(Role).where(Role.name == name)
            )
        assert found is not None
        return found


async def serve(database: Database, **options: Any) -> AsyncIterator[Site]:
    users = AdminUsers()
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, CustomerView],
        pages=[SalesReport],
        auth=users,
        secret_key=SECRET,
        **options,
    )
    await fresh_tables(database)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield Site(users, client, database, admin)


@pytest.fixture
async def site(database: Database, tmp_path: Path) -> AsyncIterator[Site]:
    log = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    async for found in serve(database, audit=log, api=True, languages=["fa"]):
        await found.users.create_user("nima", PASSWORD, superuser=True)
        yield found
    log.close()


class TestWhoOpensThem:
    async def test_a_superuser_finds_them_in_the_sidebar(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/orders")

        assert page.status_code == 200
        # Beside Activity, in the sidebar's Admin section.
        admin_section = page.text.index('id="nav-group-admin"')
        assert admin_section < page.text.index('href="/admin/admin_users"')
        assert admin_section < page.text.index('href="/admin/admin_roles"')
        assert admin_section < page.text.index('href="/admin/-/activity"')

    async def test_nobody_else_opens_them(self, site: Site) -> None:
        await site.users.create_role("Everything", {"orders": ["view", "edit"]})
        await site.users.create_user("lena", PASSWORD, roles=["Everything"])
        await site.sign_in("lena")

        users = await site.client.get("/admin/admin_users")
        roles = await site.client.get("/admin/admin_roles")
        orders = await site.client.get("/admin/orders")

        assert users.status_code == 403
        assert roles.status_code == 403
        assert 'href="/admin/admin_users"' not in orders.text

    async def test_no_role_can_grant_them(self, site: Site) -> None:
        await site.users.create_role("Sneaky", {"admin_users": ["view", "edit"]})
        await site.users.create_user("lena", PASSWORD, roles=["Sneaky"])
        await site.sign_in("lena")

        users = await site.client.get("/admin/admin_users")

        assert users.status_code == 403


class TestTheUserForm:
    async def test_a_superuser_adds_someone(self, site: Site) -> None:
        await site.users.create_role("Support", {"orders": ["view"]})
        support = await site.role("Support")
        await site.sign_in("nima")

        response = await site.post(
            "/admin/admin_users/new",
            {
                "username": " Lena ",
                "password": PASSWORD,
                "is_active": "true",
                "roles": [str(support.id)],
            },
        )

        assert response.status_code == 303
        lena = await site.users.verify("lena", PASSWORD)
        assert lena is not None
        loaded = await site.users.load_user(str(lena.id))
        assert loaded is not None
        assert loaded.has_role("Support")

    async def test_someone_new_starts_active(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/admin_users/new")

        switch = re.search(
            r'<input type="checkbox"[^>]*name="is_active"[^>]*>', page.text
        )
        assert switch is not None
        assert "checked" in switch.group(0)

    async def test_a_short_password_is_refused(self, site: Site) -> None:
        await site.sign_in("nima")

        response = await site.post(
            "/admin/admin_users/new",
            {"username": "lena", "password": "short", "is_active": "true"},
        )

        assert response.status_code == 422
        assert "Enter at least 8 characters." in response.text

    async def test_a_taken_username_is_refused(self, site: Site) -> None:
        await site.users.create_user("lena", PASSWORD)
        await site.sign_in("nima")

        response = await site.post(
            "/admin/admin_users/new",
            {"username": "LENA", "password": PASSWORD, "is_active": "true"},
        )

        assert response.status_code == 422
        assert "That username is taken." in response.text

    async def test_a_password_left_empty_stays(self, site: Site) -> None:
        lena = await site.users.create_user("lena", PASSWORD)
        await site.sign_in("nima")

        response = await site.post(
            f"/admin/admin_users/{lena.id}/edit",
            {"username": "lena", "password": "", "is_active": "true"},
        )

        assert response.status_code == 303
        assert await site.users.verify("lena", PASSWORD) is not None

    async def test_a_new_password_is_hashed(self, site: Site) -> None:
        lena = await site.users.create_user("lena", PASSWORD)
        await site.sign_in("nima")

        await site.post(
            f"/admin/admin_users/{lena.id}/edit",
            {"username": "lena", "password": "another password", "is_active": "true"},
        )

        assert (await site.user("lena")).password_hash.startswith("pbkdf2_sha256$")
        assert await site.users.verify("lena", "another password") is not None

    async def test_your_own_new_password_keeps_you_signed_in(self, site: Site) -> None:
        nima = await site.user("nima")
        await site.sign_in("nima")

        await site.post(
            f"/admin/admin_users/{nima.id}/edit",
            {
                "username": "nima",
                "password": "another password",
                "is_active": "true",
                "is_superuser": "true",
            },
        )
        after = await site.client.get("/admin/admin_users")

        assert after.status_code == 200


class TestTheLastSuperuser:
    async def test_it_keeps_its_superuser_switch(self, site: Site) -> None:
        nima = await site.user("nima")
        await site.sign_in("nima")

        response = await site.post(
            f"/admin/admin_users/{nima.id}/edit",
            {"username": "nima", "is_active": "true"},
        )

        assert response.status_code == 422
        assert "Keep at least one active superuser" in response.text
        assert (await site.user("nima")).is_superuser

    async def test_it_is_not_deleted(self, site: Site) -> None:
        nima = await site.user("nima")
        await site.sign_in("nima")

        await site.post(f"/admin/admin_users/{nima.id}/delete", {})

        assert (await site.user("nima")).is_superuser

    async def test_another_superuser_lets_one_go(self, site: Site) -> None:
        await site.users.create_user("omar", PASSWORD, superuser=True)
        nima = await site.user("nima")
        await site.sign_in("omar")

        response = await site.post(
            f"/admin/admin_users/{nima.id}/edit",
            {"username": "nima", "is_active": "true"},
        )

        assert response.status_code == 303
        assert not (await site.user("nima")).is_superuser


class TestTheRoleForm:
    async def test_the_grid_has_a_row_per_view_and_page(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/admin_roles/new")

        assert 'data-grant="orders" data-permission="edit"' in page.text
        assert 'data-grant="customers" data-permission="view"' in page.text
        assert 'data-grant="-/sales_report" data-permission="view"' in page.text
        assert 'data-grant="admin_users"' not in page.text
        assert "Pay the money back" in page.text
        assert 'data-grant="orders" data-permission="refund"' in page.text

    async def test_a_box_the_view_switches_off_is_not_drawn(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/admin_roles/new")

        assert 'data-grant="orders" data-permission="import"' in page.text
        assert 'data-grant="customers" data-permission="import"' not in page.text
        assert 'data-grant="-/sales_report" data-permission="edit"' not in page.text

    async def test_history_has_a_column_where_there_is_a_log(
        self, site: Site, database: Database
    ) -> None:
        await site.sign_in("nima")
        with_log = await site.client.get("/admin/admin_roles/new")
        async for plain in serve(database):
            await plain.users.create_user("nima", PASSWORD, superuser=True)
            await plain.sign_in("nima")
            without_log = await plain.client.get("/admin/admin_roles/new")

        assert 'data-permission="history"' in with_log.text
        assert 'data-permission="history"' not in without_log.text

    async def test_saving_keeps_what_the_views_offer(self, site: Site) -> None:
        await site.sign_in("nima")
        sent = {
            "orders": ["edit", "refund"],
            "customers": ["view"],
            "admin_users": ["view"],
            "nowhere": ["view"],
            "-/sales_report": ["view", "edit"],
        }

        response = await site.post(
            "/admin/admin_roles/new",
            {"name": "Support", "permissions": json.dumps(sent)},
        )

        assert response.status_code == 303
        assert (await site.role("Support")).permissions == {
            "orders": ["edit", "refund", "view"],
            "customers": ["view"],
            "-/sales_report": ["view"],
        }

    async def test_the_grid_shows_what_is_granted(self, site: Site) -> None:
        role = await site.users.create_role("Support", {"orders": ["view", "refund"]})
        await site.sign_in("nima")

        page = await site.client.get(f"/admin/admin_roles/{role.id}/edit")

        ticked = re.findall(
            r'data-grant="orders" data-permission="(\w+)"[^>]*checked', page.text
        )
        assert sorted(ticked) == ["refund", "view"]

    async def test_the_record_page_reads_the_grants(self, site: Site) -> None:
        role = await site.users.create_role("Support", {"orders": ["view", "edit"]})
        await site.sign_in("nima")

        page = await site.client.get(f"/admin/admin_roles/{role.id}")

        assert "Orders: View, Edit" in page.text

    async def test_the_api_refuses_grants_of_the_wrong_shape(self, site: Site) -> None:
        await site.sign_in("nima")
        page = await site.client.get("/admin/")

        response = await site.client.post(
            "/admin/-/api/admin_roles",
            json={"name": "Odd", "permissions": {"orders": "view"}},
            headers={"X-CSRF-Token": token_from(page.text)},
        )

        assert response.status_code == 422
        assert "Write the permissions as JSON" in response.text

    async def test_the_form_reads_the_same_whatever_the_views(
        self, site: Site, backend: Backend
    ) -> None:
        role = await site.users.create_role("Support", {"orders": ["view"]})
        await site.sign_in("nima")

        with count_queries(backend) as counter:
            page = await site.client.get(f"/admin/admin_roles/{role.id}/edit")

        assert page.status_code == 200
        # The user and their roles, the role, and who holds it: the grid,
        # drawn from the admin's views, asks nothing.
        assert counter.count == 3


class TestTheirWords:
    async def test_the_pages_speak_the_readers_language(self, site: Site) -> None:
        await site.sign_in("nima")

        users = await site.client.get(
            "/admin/admin_users", headers={"Accept-Language": "fa"}
        )
        roles = await site.client.get(
            "/admin/admin_roles/new", headers={"Accept-Language": "fa"}
        )
        english = await site.client.get("/admin/admin_roles/new")

        assert "کاربران" in users.text
        assert "نام کاربری" in users.text
        assert "ابرکاربر" in users.text
        assert "مشاهده" in roles.text
        assert "مجوزها" in roles.text
        assert "Permissions" in english.text
        assert "View" in english.text

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/admin_users",
            "/admin/admin_users/new",
            "/admin/admin_roles/new",
        ],
    )
    async def test_every_control_has_a_name(self, site: Site, path: str) -> None:
        await site.sign_in("nima")

        page = await site.client.get(path)
        controls = Controls()
        controls.feed(page.text)

        assert page.status_code == 200
        assert controls.unnamed() == []
