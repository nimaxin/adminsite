"""Changing one's own password, and an account that may open nothing yet."""

import httpx
from starlette.applications import Starlette

from adminsite import Admin
from adminsite.audit import AuditEvent, AuditQuery
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from tests.test_accessibility import Controls
from tests.test_auth import SECRET, password_toggle, sign_in, token_from
from tests.test_user_pages import OrderView, Site, site
from tests.test_users import PASSWORD

__all__ = ["site"]

NEW = "a brand new password"


async def change(site: Site, current: str, new: str, repeat: str) -> httpx.Response:
    """Send the password form as the browser would."""
    page = await site.client.get("/admin/-/password")
    return await site.client.post(
        "/admin/-/password",
        data={
            "current": current,
            "new": new,
            "repeat": repeat,
            "_csrf": token_from(page.text),
        },
    )


class TestTheMenu:
    async def test_the_account_menu_offers_it(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/")

        assert 'href="/admin/-/password"' in page.text
        assert "Change password" in page.text

    async def test_a_provider_that_cannot_change_passwords_offers_none(
        self, database: Database
    ) -> None:
        admin = Admin(
            database,
            title="Shop",
            views=[OrderView],
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key=SECRET,
        )
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await sign_in(client)
            page = await client.get("/admin/")
            form = await client.get("/admin/-/password")

        assert "/-/password" not in page.text
        assert form.status_code == 404


class TestChangingIt:
    async def test_every_box_is_named(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/-/password")
        controls = Controls()
        controls.feed(page.text)

        assert page.status_code == 200
        assert controls.unnamed() == []

    async def test_each_box_can_show_what_is_typed(self, site: Site) -> None:
        await site.sign_in("nima")

        page = await site.client.get("/admin/-/password")

        for name in ("current", "new", "repeat"):
            assert 'aria-pressed="false"' in password_toggle(page, f"password-{name}")

    async def test_a_wrong_current_password_is_refused(self, site: Site) -> None:
        await site.sign_in("nima")

        response = await change(site, "not the password", NEW, NEW)

        assert response.status_code == 422
        assert "That is not your current password." in response.text
        assert await site.users.verify("nima", PASSWORD) is not None

    async def test_the_new_password_is_typed_twice_alike(self, site: Site) -> None:
        await site.sign_in("nima")

        response = await change(site, PASSWORD, NEW, "something else")

        assert response.status_code == 422
        assert "The two new passwords differ." in response.text

    async def test_a_short_new_password_is_refused(self, site: Site) -> None:
        await site.sign_in("nima")

        response = await change(site, PASSWORD, "short", "short")

        assert response.status_code == 422
        assert "Enter at least 8 characters." in response.text

    async def test_it_changes_and_keeps_you_signed_in(self, site: Site) -> None:
        await site.sign_in("nima")

        response = await change(site, PASSWORD, NEW, NEW)
        after = await site.client.get("/admin/")

        assert response.status_code == 303
        assert after.status_code == 200
        assert "Password changed." in after.text
        assert await site.users.verify("nima", NEW) is not None

    async def test_every_other_session_is_signed_out(self, site: Site) -> None:
        await site.sign_in("nima")
        elsewhere = dict(site.client.cookies)
        site.client.cookies.clear()
        await site.sign_in("nima")

        await change(site, PASSWORD, NEW, NEW)
        site.client.cookies.clear()
        site.client.cookies.update(elsewhere)
        response = await site.client.get("/admin/")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    async def test_the_change_goes_to_the_users_history(self, site: Site) -> None:
        nima = await site.user("nima")
        await site.sign_in("nima")

        await change(site, PASSWORD, NEW, NEW)

        assert site.admin.audit is not None
        entries = await site.admin.audit.find(
            AuditQuery(view="admin_users", record_key=str(nima.id)), limit=10
        )
        assert [entry.event for entry in entries] == [AuditEvent.UPDATED]
        assert entries[0].changes == {"password_hash": ("***", "***")}
        assert entries[0].user == "nima"


class TestNothingOpenYet:
    async def test_someone_without_a_role_is_told_to_ask(self, site: Site) -> None:
        await site.users.create_user("lena", PASSWORD)
        await site.sign_in("lena")

        page = await site.client.get("/admin/")

        assert "Nothing here is open to you yet." in page.text

    async def test_someone_with_a_role_is_not(self, site: Site) -> None:
        await site.users.create_role("Support", {"orders": ["view"]})
        await site.users.create_user("lena", PASSWORD, roles=["Support"])
        await site.sign_in("lena")

        page = await site.client.get("/admin/")

        assert "Nothing here is open to you yet." not in page.text
