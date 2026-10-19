"""AdminUsers: people who sign in to the admin, kept in the admin's own tables."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, select, update
from starlette.applications import Starlette

from adminsite import Admin, ModelView
from adminsite.auth import SignInRefusedError
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError
from adminsite.permissions import Permission
from adminsite.users import AdminUsers, Role, User, user_metadata
from tests.models import Customer, Order
from tests.support import Backend, count_queries
from tests.test_auth import SECRET, token_from

PASSWORD = "a long password"


class OrderView(ModelView[Order]):
    fields = ["id", "status"]


class CustomerView(ModelView[Customer]):
    fields = ["name", "email"]


async def fresh_tables(database: Database) -> None:
    """Make the users' tables and empty them, so a database server starts clean."""
    async with database.session() as session:
        await session.run(lambda plain: user_metadata.create_all(plain.connection()))
        for table in reversed(user_metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()


async def stored(database: Database, username: str) -> User:
    """The user as the table holds them now."""
    async with database.session() as session:
        found: User | None = await session.scalar(
            select(User).where(User.username == username)
        )
    assert found is not None
    return found


async def change(database: Database, username: str, **values: object) -> None:
    """Change a user's row, as another request or a script might."""
    async with database.session() as session:
        await session.execute(
            update(User).where(User.username == username).values(**values)
        )
        await session.commit()


@pytest.fixture
async def users(database: Database) -> AdminUsers:
    found = AdminUsers()
    Admin(database, title="Shop", auth=found, secret_key=SECRET)
    await fresh_tables(database)
    return found


@dataclass
class Site:
    users: AdminUsers
    client: httpx.AsyncClient

    async def sign_in(self, username: str, password: str = PASSWORD) -> httpx.Response:
        page = await self.client.get("/admin/login")
        return await self.client.post(
            "/admin/login",
            data={
                "username": username,
                "password": password,
                "_csrf": token_from(page.text),
            },
        )


@pytest.fixture
async def site(database: Database) -> AsyncIterator[Site]:
    users = AdminUsers()
    admin = Admin(
        database,
        title="Shop",
        views=[OrderView, CustomerView],
        auth=users,
        secret_key=SECRET,
    )
    await fresh_tables(database)
    app = Starlette()
    app.mount("/admin", admin)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield Site(users, client)


class TestSigningIn:
    async def test_a_user_signs_in_with_their_password(self, users: AdminUsers) -> None:
        await users.create_user("lena", PASSWORD)

        found = await users.verify("lena", PASSWORD)

        assert isinstance(found, User)
        assert found.username == "lena"

    async def test_the_username_is_kept_in_small_letters(
        self, users: AdminUsers
    ) -> None:
        made = await users.create_user(" Lena ", PASSWORD)

        found = await users.verify("LENA", PASSWORD)

        assert made.username == "lena"
        assert found is not None
        assert found.id == made.id

    async def test_a_wrong_password_is_refused(self, users: AdminUsers) -> None:
        await users.create_user("lena", PASSWORD)

        with pytest.raises(SignInRefusedError) as refused:
            await users.verify("lena", "not the password")

        assert refused.value.reason == "The password was wrong."
        assert str(refused.value.user) == "lena"

    async def test_an_unknown_username_is_refused(self, users: AdminUsers) -> None:
        with pytest.raises(SignInRefusedError) as refused:
            await users.verify("nobody", PASSWORD)

        assert refused.value.reason == "There is no such username."
        assert refused.value.user is None

    async def test_a_switched_off_account_is_refused(
        self, users: AdminUsers, database: Database
    ) -> None:
        await users.create_user("lena", PASSWORD)
        await change(database, "lena", is_active=False)

        with pytest.raises(SignInRefusedError) as refused:
            await users.verify("lena", PASSWORD)

        assert refused.value.reason == "This account is switched off."

    async def test_the_session_keeps_the_users_id(self, users: AdminUsers) -> None:
        made = await users.create_user("lena", PASSWORD)

        assert users.identity(made) == str(made.id)


class TestLoadingTheUser:
    async def test_the_user_comes_with_their_roles_in_one_query(
        self, users: AdminUsers, backend: Backend
    ) -> None:
        await users.create_role("Support", {"orders": ["view"]})
        made = await users.create_user("lena", PASSWORD, roles=["Support"])

        with count_queries(backend) as counter:
            loaded = await users.load_user(str(made.id))

        assert counter.count == 1
        assert loaded is not None
        assert loaded.has_role("Support")
        assert not loaded.has_role("Billing")

    async def test_a_switched_off_user_is_not_loaded(
        self, users: AdminUsers, database: Database
    ) -> None:
        made = await users.create_user("lena", PASSWORD)
        await change(database, "lena", is_active=False)

        assert await users.load_user(str(made.id)) is None

    async def test_a_key_that_is_no_number_loads_no_one(
        self, users: AdminUsers
    ) -> None:
        assert await users.load_user("lena") is None


class TestGrants:
    async def test_a_superuser_may_do_everything(self, users: AdminUsers) -> None:
        made = await users.create_user("nima", PASSWORD, superuser=True)
        loaded = await users.load_user(str(made.id))

        grants = await users.grants_for(loaded)

        assert grants is not None
        assert grants.everything

    async def test_roles_add_up(self, users: AdminUsers) -> None:
        await users.create_role("Support", {"orders": ["view", "edit"]})
        await users.create_role(
            "Billing", {"orders": ["refund"], "customers": [Permission.VIEW]}
        )
        made = await users.create_user("lena", PASSWORD, roles=["Support", "Billing"])
        loaded = await users.load_user(str(made.id))

        grants = await users.grants_for(loaded)

        assert grants is not None
        assert grants.allows("orders", Permission.EDIT)
        assert grants.allows("orders", "refund")
        assert grants.allows("customers", Permission.VIEW)
        assert not grants.allows("customers", Permission.EDIT)
        assert not grants.allows("orders", Permission.DELETE)

    async def test_someone_without_roles_may_do_nothing(
        self, users: AdminUsers
    ) -> None:
        made = await users.create_user("lena", PASSWORD)
        loaded = await users.load_user(str(made.id))

        grants = await users.grants_for(loaded)

        assert grants is not None
        assert not grants.allows("orders", Permission.VIEW)

    async def test_grants_written_badly_by_hand_are_left_out(
        self, users: AdminUsers, database: Database
    ) -> None:
        async with database.session() as session:
            await session.add(
                Role(name="Odd", permissions={"orders": "view", "customers": ["view"]})
            )
            await session.commit()
        made = await users.create_user("lena", PASSWORD, roles=["Odd"])
        loaded = await users.load_user(str(made.id))

        grants = await users.grants_for(loaded)

        assert grants is not None
        assert not grants.allows("orders", Permission.VIEW)
        assert grants.allows("customers", Permission.VIEW)


class TestCreating:
    async def test_a_taken_username_is_refused(self, users: AdminUsers) -> None:
        await users.create_user("lena", PASSWORD)

        with pytest.raises(AdminSiteError, match="The username 'lena' is taken"):
            await users.create_user("Lena", PASSWORD)

    async def test_a_short_password_is_refused(self, users: AdminUsers) -> None:
        with pytest.raises(AdminSiteError, match="Enter at least 8 characters"):
            await users.create_user("lena", "short")

    async def test_a_role_that_does_not_exist_is_refused(
        self, users: AdminUsers
    ) -> None:
        with pytest.raises(AdminSiteError, match="No role is called 'Support'"):
            await users.create_user("lena", PASSWORD, roles=["Support"])

    async def test_a_role_name_is_taken_once(self, users: AdminUsers) -> None:
        await users.create_role("Support", {})

        with pytest.raises(AdminSiteError, match="already called 'Support'"):
            await users.create_role("Support", {})

    async def test_a_role_keeps_its_grants_in_order(self, users: AdminUsers) -> None:
        role = await users.create_role(
            "Support", {"orders": {Permission.EDIT, "refund", Permission.VIEW}}
        )

        assert role.permissions == {"orders": ["edit", "refund", "view"]}

    async def test_it_can_make_its_tables(self, database: Database) -> None:
        users = AdminUsers(create_table=True)
        Admin(database, title="Shop", auth=users, secret_key=SECRET)

        made = await users.create_user("lena", PASSWORD)

        assert made.id is not None
        await fresh_tables(database)

    async def test_it_needs_an_admin_first(self) -> None:
        with pytest.raises(AdminSiteError, match="once an admin has it"):
            await AdminUsers().create_user("lena", PASSWORD)

    async def test_times_are_kept_in_the_database_time_zone(
        self, database: Database
    ) -> None:
        users = AdminUsers()
        Admin(
            database,
            title="Shop",
            auth=users,
            secret_key=SECRET,
            database_timezone="Asia/Tehran",
        )
        await fresh_tables(database)

        made = await users.create_user("lena", PASSWORD)

        tehran = datetime.now(ZoneInfo("Asia/Tehran")).replace(tzinfo=None)
        assert abs(made.created_at - tehran) < timedelta(minutes=1)


class TestTheSignInNote:
    async def test_it_says_how_to_make_the_first_superuser(self, site: Site) -> None:
        page = await site.client.get("/admin/login")

        assert "No one can sign in yet." in page.text
        assert (
            '<code dir="ltr">adminsite users create <var>name</var> '
            '<span class="whitespace-nowrap">--superuser</span></code>' in page.text
        )

    async def test_it_goes_once_someone_can_sign_in(self, site: Site) -> None:
        await site.users.create_user("nima", PASSWORD, superuser=True)

        page = await site.client.get("/admin/login")

        assert "No one can sign in yet." not in page.text

    async def test_it_says_when_the_tables_are_missing(self, tmp_path: Path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'shop.db'}")
        admin = Admin(engine, title="Shop", auth=AdminUsers(), secret_key=SECRET)
        app = Starlette()
        app.mount("/admin", admin)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            page = await client.get("/admin/login")
        engine.dispose()

        assert "The users&#39; tables are not in the database yet." in page.text


class TestTheAdmin:
    async def test_a_user_sees_what_their_roles_grant(self, site: Site) -> None:
        await site.users.create_role("Support", {"orders": ["view"]})
        await site.users.create_user("lena", PASSWORD, roles=["Support"])
        await site.sign_in("lena")

        orders = await site.client.get("/admin/orders")
        customers = await site.client.get("/admin/customers")

        assert orders.status_code == 200
        assert 'href="/admin/customers"' not in orders.text
        assert customers.status_code == 403

    async def test_a_superuser_sees_everything(self, site: Site) -> None:
        await site.users.create_user("nima", PASSWORD, superuser=True)
        await site.sign_in("nima")

        customers = await site.client.get("/admin/customers")

        assert customers.status_code == 200

    async def test_a_wrong_password_reads_as_usual(self, site: Site) -> None:
        await site.users.create_user("lena", PASSWORD)

        response = await site.sign_in("lena", "not the password")

        assert response.status_code == 401
        assert "do not match" in response.text

    async def test_signing_in_notes_the_time(
        self, site: Site, database: Database
    ) -> None:
        await site.users.create_user("nima", PASSWORD, superuser=True)

        await site.sign_in("nima")

        assert (await stored(database, "nima")).last_sign_in_at is not None

    async def test_a_new_password_signs_the_user_out(
        self, site: Site, database: Database
    ) -> None:
        await site.users.create_user("nima", PASSWORD, superuser=True)
        await site.sign_in("nima")
        await change(database, "nima", password_hash="pbkdf2_sha256$1$salt$new")

        response = await site.client.get("/admin/orders")

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"

    async def test_switching_a_user_off_signs_them_out(
        self, site: Site, database: Database
    ) -> None:
        await site.users.create_user("nima", PASSWORD, superuser=True)
        await site.sign_in("nima")
        await change(database, "nima", is_active=False)

        response = await site.client.get("/admin/orders")

        assert response.status_code == 303
