import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette

from adminsite import Admin, ModelView, Permission
from adminsite.audit import AuditEvent, AuditLog, AuditQuery
from adminsite.auth import AuthProvider, PasswordAuth, SignInRefused, hash_password
from adminsite.backends.sqlalchemy import Database
from tests.models import Order, Product

PASSWORDS = {"nima": hash_password("letmein"), "clerk": hash_password("letmein")}


class ProductView(ModelView, model=Product):
    pass


class OrderView(ModelView, model=Order):
    """Orders, whose history the clerk may not read."""

    async def allows(
        self, action: Permission | str, *, request: Any = None, record: Any = None
    ) -> bool:
        user = request.scope.get("user_record") if request is not None else None
        if action == Permission.HISTORY and user == "clerk":
            return False
        return await super().allows(action, request=request, record=record)


class Account:
    def __init__(self, key: str, name: str) -> None:
        self.key = key
        self.name = name

    def __str__(self) -> str:
        return self.name


class SwitchedOff(AuthProvider):
    """Knows the account, and says why it may not come in."""

    async def verify(self, username: str, password: str) -> Any | None:
        raise SignInRefused(
            "This account is switched off.", user=Account("42", "Lena Fischer")
        )

    def identity(self, user: Any) -> str:
        return str(user.key)


class SaysNothing(AuthProvider):
    async def verify(self, username: str, password: str) -> Any | None:
        return None


class AuditorToo(PasswordAuth):
    """Lets the clerk see who signed in, though not every model's history."""

    async def may_read_sign_ins(self, request: Any, *, reads_everything: bool) -> bool:
        return True


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


def token_from(body: str) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', body)
    assert found is not None
    return found.group(1)


@asynccontextmanager
async def browser(
    database: Database, log: AuditLog, auth: AuthProvider
) -> AsyncIterator[httpx.AsyncClient]:
    site = Admin(
        database, title="Shop", audit=log, auth=auth, secret_key="for-the-tests"
    )
    site.add_view(ProductView)
    site.add_view(OrderView)
    app = Starlette()
    app.mount("/admin", site)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"user-agent": "Firefox/140"},
    ) as client:
        yield client


async def sign_in(client: httpx.AsyncClient, username: str, password: str) -> int:
    page = await client.get("/admin/login")
    answer = await client.post(
        "/admin/login",
        data={
            "username": username,
            "password": password,
            "_csrf": token_from(page.text),
        },
    )
    return answer.status_code


async def sign_out(client: httpx.AsyncClient) -> None:
    page = await client.get("/admin/")
    await client.post("/admin/logout", data={"_csrf": token_from(page.text)})


class TestSigningIn:
    async def test_every_attempt_and_the_sign_out_are_written_down(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, PasswordAuth(PASSWORDS)) as client:
            assert await sign_in(client, "nima", "wrong") == 401
            assert await sign_in(client, "nima", "letmein") == 303
            await sign_out(client)

        entries = await log.find(AuditQuery(user="nima"), limit=10)

        assert [entry.event for entry in entries] == [
            AuditEvent.SIGNED_OUT,
            AuditEvent.SIGNED_IN,
            AuditEvent.SIGN_IN_FAILED,
        ]
        for entry in entries:
            assert (entry.user, entry.user_key) == ("nima", "nima")
            assert (entry.ip, entry.user_agent) == ("127.0.0.1", "Firefox/140")
            assert (entry.view, entry.record_key) == ("", "")
        assert entries[2].error == "The password was wrong."
        assert entries[0].succeeded
        assert not entries[2].succeeded

    async def test_an_unknown_name_is_filed_under_what_was_typed(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, PasswordAuth(PASSWORDS)) as client:
            await sign_in(client, "nobody", "letmein")

        entry = (await log.find(AuditQuery(), limit=1))[0]

        assert (entry.user, entry.user_key) == ("nobody", None)
        assert entry.error == "There is no such username."

    async def test_a_provider_can_say_why_and_whose_account_it_was(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, SwitchedOff()) as client:
            page = await client.get("/admin/login")
            answer = await client.post(
                "/admin/login",
                data={
                    "username": "lena@example.com",
                    "password": "right",
                    "_csrf": token_from(page.text),
                },
            )

        entry = (await log.find(AuditQuery(user="42"), limit=1))[0]

        assert (entry.user, entry.user_key) == ("Lena Fischer", "42")
        assert entry.error == "This account is switched off."
        # The person trying is told nothing of the reason.
        assert "switched off" not in answer.text
        assert "do not match" in answer.text

    async def test_a_provider_that_says_nothing_still_leaves_a_trace(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, SaysNothing()) as client:
            await sign_in(client, "someone", "anything")

        entry = (await log.find(AuditQuery(), limit=1))[0]

        assert entry.event is AuditEvent.SIGN_IN_FAILED
        assert entry.user == "someone"
        assert entry.error == "The details did not match."

    async def test_without_auditing_signing_in_still_works(
        self, database: Database
    ) -> None:
        site = Admin(
            database,
            title="Shop",
            auth=PasswordAuth(PASSWORDS),
            secret_key="for-the-tests",
        )
        app = Starlette()
        app.mount("/admin", site)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            assert await sign_in(client, "nima", "wrong") == 401
            assert await sign_in(client, "nima", "letmein") == 303


class TestWhoSeesThem:
    async def test_someone_who_reads_every_history_does(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, PasswordAuth(PASSWORDS)) as client:
            await sign_in(client, "nobody", "x")
            await sign_in(client, "nima", "letmein")
            page = await client.get("/admin/-/activity")

        assert "could not sign in" in page.text
        assert "There is no such username." in page.text
        assert "from 127.0.0.1" in page.text

    async def test_someone_who_reads_less_does_not(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, PasswordAuth(PASSWORDS)) as client:
            await sign_in(client, "nobody", "x")
            await sign_in(client, "clerk", "letmein")
            page = await client.get("/admin/-/activity")

        assert page.status_code == 200
        assert "could not sign in" not in page.text
        assert "signed in" not in page.text

    async def test_the_provider_can_let_them_in(
        self, database: Database, log: AuditLog
    ) -> None:
        async with browser(database, log, AuditorToo(PASSWORDS)) as client:
            await sign_in(client, "nobody", "x")
            await sign_in(client, "clerk", "letmein")
            page = await client.get("/admin/-/activity")

        assert "could not sign in" in page.text
