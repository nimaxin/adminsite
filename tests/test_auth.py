import re
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.auth import (
    AuthProvider,
    PasswordAuth,
    hash_password,
    verify_password,
)
from adminsite.backends.sqlalchemy import Database
from adminsite.exceptions import AdminSiteError
from tests.models import Product

SECRET = "a-secret-for-the-tests"


class ProductView(ModelView, model=Product):
    list_display = ("id", "name", "price")
    form_fields = ("name", "price")


def build_admin(database: Database, auth: AuthProvider | None = None) -> Admin:
    site = Admin(
        database,
        title="Shop",
        auth=auth or PasswordAuth({"nima": hash_password("letmein")}),
        secret_key=SECRET,
    )
    site.add_view(ProductView)
    return site


@pytest.fixture
def client(database: Database) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", build_admin(database))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
def open_client(database: Database) -> httpx.AsyncClient:
    site = Admin(database, title="Shop")
    site.add_view(ProductView)
    app = Starlette()
    app.mount("/admin", site)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def token_from(body: str) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', body)
    assert found is not None
    return found.group(1)


async def sign_in(client: httpx.AsyncClient) -> None:
    page = await client.get("/admin/login")
    await client.post(
        "/admin/login",
        data={
            "username": "nima",
            "password": "letmein",
            "_csrf": token_from(page.text),
        },
    )


class TestSetup:
    def test_signing_in_needs_a_secret_key(self, database: Database) -> None:
        with pytest.raises(AdminSiteError, match="secret_key"):
            Admin(database, auth=PasswordAuth({"a": hash_password("b")}))


class TestSigningIn:
    async def test_a_page_sends_a_stranger_to_the_login(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/products")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    async def test_the_front_page_is_protected_too(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/admin/")

        assert response.status_code == 303

    async def test_the_login_page_is_open(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/admin/login")

        assert response.status_code == 200
        assert "Sign in" in response.text

    async def test_the_right_details_let_you_in(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)

        response = await client.get("/admin/products")

        assert response.status_code == 200
        assert "Linen shirt" in response.text

    async def test_the_wrong_details_do_not(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/admin/login")

        response = await client.post(
            "/admin/login",
            data={
                "username": "nima",
                "password": "wrong",
                "_csrf": token_from(page.text),
            },
        )

        assert response.status_code == 401
        assert "do not match" in response.text

    async def test_who_is_signed_in_is_shown(self, client: httpx.AsyncClient) -> None:
        await sign_in(client)

        response = await client.get("/admin/products")

        assert "nima" in response.text

    async def test_signing_out_ends_the_session(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)
        page = await client.get("/admin/products")

        await client.post("/admin/logout", data={"_csrf": token_from(page.text)})
        after = await client.get("/admin/products")

        assert after.status_code == 303

    async def test_an_admin_without_auth_is_open(
        self, open_client: httpx.AsyncClient
    ) -> None:
        response = await open_client.get("/admin/products")

        assert response.status_code == 200


class TestCsrf:
    async def test_a_form_carries_a_token(self, client: httpx.AsyncClient) -> None:
        await sign_in(client)

        response = await client.get("/admin/products/new")

        assert 'name="_csrf"' in response.text

    async def test_a_post_without_a_token_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)

        response = await client.post(
            "/admin/products/new", data={"name": "Sneaky", "price": "1.00"}
        )

        assert response.status_code == 403

    async def test_a_post_with_the_wrong_token_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)

        response = await client.post(
            "/admin/products/new",
            data={"name": "Sneaky", "price": "1.00", "_csrf": "made-up"},
        )

        assert response.status_code == 403

    async def test_a_post_with_the_right_token_goes_through(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)
        page = await client.get("/admin/products/new")

        response = await client.post(
            "/admin/products/new",
            data={
                "name": "Felt hat",
                "price": "42.00",
                "_csrf": token_from(page.text),
            },
        )

        assert response.status_code == 303

    async def test_deleting_needs_a_token_as_well(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)

        response = await client.post("/admin/products/1/delete")

        assert response.status_code == 403

    async def test_the_token_can_come_in_a_header(
        self, client: httpx.AsyncClient
    ) -> None:
        await sign_in(client)
        page = await client.get("/admin/products/new")

        response = await client.post(
            "/admin/products/1/delete",
            headers={"X-CSRF-Token": token_from(page.text)},
        )

        assert response.status_code == 303


class TestCustomProvider:
    async def test_a_provider_can_load_its_own_user(self, database: Database) -> None:
        class StaffAuth(AuthProvider):
            async def verify(self, username: str, password: str) -> Any | None:
                return {"name": username} if password == "open" else None

            def identity(self, user: Any) -> str:
                return str(user["name"])

            async def load_user(self, key: str) -> Any | None:
                return {"name": key}

        app = Starlette()
        app.mount("/admin", build_admin(database, StaffAuth()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/login")
            await client.post(
                "/admin/login",
                data={
                    "username": "dana",
                    "password": "open",
                    "_csrf": token_from(page.text),
                },
            )

            response = await client.get("/admin/products")

            assert response.status_code == 200


class TestAFailedAttempt:
    async def test_the_provider_decides_what_it_says(self, database: Database) -> None:
        class QuietAuth(PasswordAuth):
            async def sign_in_failed(self, request: Any, username: str) -> str:
                return "Ask the office for a new password."

        app = Starlette()
        app.mount(
            "/admin",
            build_admin(database, QuietAuth({"nima": hash_password("letmein")})),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/login")

            answer = await client.post(
                "/admin/login",
                data={
                    "username": "nima",
                    "password": "wrong",
                    "_csrf": token_from(page.text),
                },
            )

        assert answer.status_code == 401
        assert "Ask the office for a new password." in answer.text
        assert "do not match" not in answer.text

    async def test_the_provider_sees_who_tried(self, database: Database) -> None:
        tried: list[str] = []

        class WatchfulAuth(PasswordAuth):
            async def sign_in_failed(self, request: Any, username: str) -> str:
                tried.append(username)
                return await super().sign_in_failed(request, username)

        app = Starlette()
        app.mount(
            "/admin",
            build_admin(database, WatchfulAuth({"nima": hash_password("letmein")})),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/login")
            await client.post(
                "/admin/login",
                data={
                    "username": "dana",
                    "password": "wrong",
                    "_csrf": token_from(page.text),
                },
            )

        assert tried == ["dana"]

    async def test_a_good_password_never_reaches_it(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/login")

        answer = await client.post(
            "/admin/login",
            data={
                "username": "nima",
                "password": "letmein",
                "_csrf": token_from(page.text),
            },
        )

        assert answer.status_code == 303


class TestAnUnknownName:
    async def test_it_costs_as_much_as_a_wrong_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both go through the hash, so neither answers faster."""
        from adminsite.auth import provider

        checked: list[str] = []

        def counting(password: str, stored: str) -> bool:
            checked.append(stored)
            return False

        monkeypatch.setattr(provider, "verify_password", counting)
        auth = PasswordAuth({"nima": hash_password("letmein")})

        assert await auth.verify("nobody", "x") is None
        assert await auth.verify("nima", "x") is None

        assert len(checked) == 2
        assert checked[0] != checked[1]


class TestPasswordHashing:
    def test_a_hash_does_not_contain_the_password(self) -> None:
        stored = hash_password("letmein")

        assert "letmein" not in stored
        assert stored.startswith("pbkdf2_sha256$")

    def test_the_same_password_hashes_differently_each_time(self) -> None:
        assert hash_password("letmein") != hash_password("letmein")

    def test_a_hash_verifies_only_the_right_password(self) -> None:
        stored = hash_password("letmein")

        assert verify_password("letmein", stored) is True
        assert verify_password("letmeout", stored) is False

    def test_rubbish_does_not_verify(self) -> None:
        assert verify_password("letmein", "not-a-hash") is False

    def test_plain_passwords_are_refused_at_startup(self) -> None:
        with pytest.raises(AdminSiteError, match="not hashed"):
            PasswordAuth({"nima": "letmein"})
