import hashlib
import html
import json
import re
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette

from adminsite import Admin, FieldOptions, ModelView, RefusedError
from adminsite.audit import AuditEntry, AuditLog, AuditQuery
from adminsite.backends.sqlalchemy import Database, SessionAdapter
from adminsite.fields import JSONField, PasswordField
from adminsite.views.writing import SaveContext
from tests.models import Account, Customer, Setting


def hashed(password: str) -> str:
    return "sha256$" + hashlib.sha256(password.encode()).hexdigest()


class AccountView(ModelView, model=Account):
    list_display = ("email",)
    form_fields = ("email", "password")
    fields = (PasswordField("password", required=True),)

    async def before_save(self, context: SaveContext) -> None:
        password = context.values.get("password")
        if password is None:
            return
        if len(password) < 8:
            raise RefusedError("Use eight characters or more.", field="password")
        context.set("password_hash", hashed(password))


def kept_as(customer: Customer) -> str:
    """The settings row holding a customer's preferences."""
    return f"customer-{customer.id}"


class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "preferences")
    fields = (
        JSONField("preferences", form_only=True),
        FieldOptions("email", secret=True),
    )

    async def form_values(
        self, session: SessionAdapter, record: Any, *, request: Any = None
    ) -> Mapping[str, Any]:
        if record is None:
            return {}
        row = await session.scalar(
            select(Setting).where(Setting.name == kept_as(record))
        )
        return {"preferences": row.options if row is not None else {}}

    async def after_save(self, context: SaveContext) -> None:
        preferences = context.values.get("preferences")
        if preferences is None:
            return
        name = kept_as(context.record)
        row = await context.session.scalar(select(Setting).where(Setting.name == name))
        if row is None:
            await context.session.add(Setting(name=name, options=preferences))
        else:
            row.options = preferences
        await context.session.flush()
        if preferences.get("refuse"):
            raise RefusedError("These cannot be kept.", field="preferences")


OLD_PASSWORD = "old password"


@pytest.fixture
async def account(database: Database) -> int:
    async with database.session() as session:
        found = Account(email="ops@shop.example", password_hash=hashed(OLD_PASSWORD))
        await session.add(found)
        await session.flush()
        key = found.id
        await session.commit()
    return key


@pytest.fixture
def log(tmp_path: Path) -> Iterator[AuditLog]:
    audit = AuditLog(f"sqlite:///{tmp_path / 'audit.db'}")
    yield audit
    audit.close()


@pytest.fixture
async def client(database: Database, log: AuditLog) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[AccountView, CustomerView],
        audit=log,
        secret_key="for-the-session",
        api=True,
    )
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def token_in(page: httpx.Response) -> str:
    found = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert found is not None
    return found.group(1)


async def submit(
    client: httpx.AsyncClient, url: str, data: dict[str, str]
) -> httpx.Response:
    form = await client.get(url)
    return await client.post(url, data={"_csrf": token_in(form), **data})


async def hash_of(database: Database, key: int) -> str:
    async with database.session() as session:
        found = await session.get(Account, key)
        assert found is not None
        return found.password_hash


def password_input(page: httpx.Response) -> str:
    found = re.search(r'<input[^>]*name="password"[^>]*>', page.text)
    assert found is not None
    return found.group(0)


class TestANewAccount:
    async def test_the_form_asks_for_a_password(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/accounts/new")

        field = password_input(page)
        assert 'type="password"' in field
        assert 'value=""' in field
        assert 'autocomplete="new-password"' in field
        assert "required" in field

    async def test_it_cannot_be_left_out(self, client: httpx.AsyncClient) -> None:
        answer = await submit(
            client, "/admin/accounts/new", {"email": "new@shop.example"}
        )

        assert answer.status_code == 422
        assert re.search(
            r'id="field-password-note">.*?This field is required.', answer.text, re.S
        )

    async def test_the_hook_stores_its_hash(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await submit(
            client,
            "/admin/accounts/new",
            {"email": "new@shop.example", "password": "correct horse"},
        )

        assert answer.status_code == 303
        async with database.session() as session:
            found = await session.scalar(
                select(Account).where(Account.email == "new@shop.example")
            )
        assert found is not None
        assert found.password_hash == hashed("correct horse")


class TestChangingAnAccount:
    async def test_the_form_leaves_the_password_empty(
        self, client: httpx.AsyncClient, account: int
    ) -> None:
        page = await client.get(f"/admin/accounts/{account}/edit")

        field = password_input(page)
        assert 'value=""' in field
        assert "required" not in field
        assert "Leave it empty to keep the current one." in page.text

    async def test_leaving_it_empty_keeps_the_password(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        answer = await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "desk@shop.example", "password": ""},
        )

        assert answer.status_code == 303
        assert await hash_of(database, account) == hashed(OLD_PASSWORD)

    async def test_spaces_alone_keep_it_too(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "ops@shop.example", "password": "   "},
        )

        assert await hash_of(database, account) == hashed(OLD_PASSWORD)

    async def test_typing_one_sets_it_as_typed(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "ops@shop.example", "password": " new pass phrase "},
        )

        assert await hash_of(database, account) == hashed(" new pass phrase ")

    async def test_a_refusal_shows_beside_it_and_never_repeats_it(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        answer = await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "ops@shop.example", "password": "tiny7"},
        )

        assert answer.status_code == 422
        assert re.search(
            r'id="field-password-note">.*?Use eight characters or more.',
            answer.text,
            re.S,
        )
        assert "tiny7" not in answer.text
        assert await hash_of(database, account) == hashed(OLD_PASSWORD)

    async def test_the_hash_is_nowhere_on_any_page(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "ops@shop.example", "password": "a new password"},
        )
        kept = await hash_of(database, account)

        pages = [
            await client.get("/admin/accounts"),
            await client.get(f"/admin/accounts/{account}"),
            await client.get(f"/admin/accounts/{account}/edit"),
            await client.get("/admin/accounts/export"),
        ]

        for page in pages:
            assert page.status_code == 200
            assert kept not in page.text
            assert hashed(OLD_PASSWORD) not in page.text
            assert "a new password" not in page.text


async def entries(log: AuditLog) -> list[AuditEntry]:
    return await log.find(AuditQuery(), limit=20)


class TestTheLog:
    async def test_a_new_password_shows_as_a_change_and_nothing_more(
        self, client: httpx.AsyncClient, log: AuditLog, account: int
    ) -> None:
        await submit(
            client,
            f"/admin/accounts/{account}/edit",
            {"email": "ops@shop.example", "password": "a new password"},
        )

        entry = (await entries(log))[0]

        assert entry.changes == {"password_hash": ("***", "***")}

    async def test_a_new_account_and_a_deleted_one_hide_it(
        self, client: httpx.AsyncClient, log: AuditLog, account: int
    ) -> None:
        await submit(
            client,
            "/admin/accounts/new",
            {"email": "new@shop.example", "password": "correct horse"},
        )
        page = await client.get(f"/admin/accounts/{account}")
        await client.post(
            f"/admin/accounts/{account}/delete", data={"_csrf": token_in(page)}
        )

        deleted, created = await entries(log)

        assert created.changes == {
            "email": (None, "new@shop.example"),
            "password_hash": (None, "***"),
        }
        assert deleted.changes == {"email": ("ops@shop.example", None)}

    async def test_a_field_marked_secret_is_hidden(
        self, client: httpx.AsyncClient, log: AuditLog
    ) -> None:
        await submit(
            client,
            "/admin/customers/1/edit",
            {"name": "Lena Fischer", "email": "lena@new.example"},
        )

        entry = (await entries(log))[0]

        assert entry.changes == {"email": ("***", "***")}


async def api_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """The client, carrying the form token the API asks a session for."""
    page = await client.get("/admin/accounts/new")
    client.headers["X-CSRF-Token"] = token_in(page)
    return client


class TestTheApi:
    async def test_it_takes_a_password_and_never_sends_one_back(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        api = await api_client(client)

        answer = await api.post(
            "/admin/-/api/accounts",
            json={"email": "new@shop.example", "password": "correct horse"},
        )

        assert answer.status_code == 201
        body = answer.json()
        assert body == {"key": body["key"], "email": "new@shop.example"}
        assert await hash_of(database, int(body["key"])) == hashed("correct horse")

    async def test_a_new_account_needs_one(self, client: httpx.AsyncClient) -> None:
        api = await api_client(client)

        answer = await api.post(
            "/admin/-/api/accounts", json={"email": "new@shop.example"}
        )

        assert answer.status_code == 422
        assert answer.json()["errors"] == {"password": "This field is required."}

    async def test_a_change_without_one_keeps_it(
        self, client: httpx.AsyncClient, database: Database, account: int
    ) -> None:
        api = await api_client(client)
        url = f"/admin/-/api/accounts/{account}"

        await api.patch(url, json={"email": "desk@shop.example"})
        await api.patch(url, json={"password": ""})
        kept = await hash_of(database, account)
        await api.patch(url, json={"password": "another password"})

        assert kept == hashed(OLD_PASSWORD)
        assert await hash_of(database, account) == hashed("another password")


async def preferences_of(database: Database, key: int = 1) -> dict[str, Any] | None:
    async with database.session() as session:
        row = await session.scalar(
            select(Setting).where(Setting.name == f"customer-{key}")
        )
        return None if row is None else row.options


class TestAValueKeptElsewhere:
    async def test_the_form_starts_from_where_it_is_kept(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        async with database.session() as session:
            await session.add(Setting(name="customer-1", options={"newsletter": True}))
            await session.commit()

        page = await client.get("/admin/customers/1/edit")

        box = re.search(r'name="preferences"[^>]*>([^<]*)</textarea>', page.text)
        assert box is not None
        assert json.loads(html.unescape(box.group(1))) == {"newsletter": True}

    async def test_saving_writes_it_back(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await submit(
            client,
            "/admin/customers/1/edit",
            {
                "name": "Lena Fischer",
                "email": "lena@fischer.de",
                "preferences": '{"newsletter": false, "language": "de"}',
            },
        )

        assert answer.status_code == 303
        assert await preferences_of(database) == {
            "newsletter": False,
            "language": "de",
        }

    async def test_a_refusal_undoes_the_record_and_the_rows(
        self, client: httpx.AsyncClient, database: Database
    ) -> None:
        answer = await submit(
            client,
            "/admin/customers/1/edit",
            {
                "name": "Renamed",
                "email": "lena@fischer.de",
                "preferences": '{"refuse": true}',
            },
        )

        assert answer.status_code == 422
        assert re.search(
            r'id="field-preferences-note">.*?These cannot be kept.', answer.text, re.S
        )
        assert await preferences_of(database) is None
        async with database.session() as session:
            lena = await session.get(Customer, 1)
            assert lena is not None
            assert lena.name == "Lena Fischer"

    async def test_the_record_page_leaves_it_out(
        self, client: httpx.AsyncClient
    ) -> None:
        page = await client.get("/admin/customers/1")

        assert page.status_code == 200
        assert "Preferences" not in page.text
