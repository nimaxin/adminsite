"""Signing in against the admin's own users, and what their roles grant."""

import hmac
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Any

import anyio
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload
from starlette.requests import Request

from adminsite._storage import Store
from adminsite.auth.passwords import hash_password, verify_password
from adminsite.auth.provider import AuthProvider, _no_one
from adminsite.database import Database
from adminsite.exceptions import AdminSiteError, IntegrityError, SignInRefusedError
from adminsite.i18n import gettext as _
from adminsite.permissions import Grants
from adminsite.plugins import Plugin
from adminsite.timezones import known_timezone, to_column_time
from adminsite.users.models import Role, User, user_metadata
from adminsite.users.rules import normal_username, password_problem
from adminsite.users.views import UserView, role_view

if TYPE_CHECKING:
    from adminsite.admin import Admin

__all__ = [
    "CHECK_KEY",
    "AdminUsers",
    "grants_from",
]

# Where the session keeps a check made from the user's password hash, so a
# new password signs out every session made with the old one.
CHECK_KEY = "adminsite_password_check"


class _Tables(Store):
    """The users' tables, made only where the project asks for them."""

    metadata = user_metadata


def grants_from(user: User) -> Grants:
    """What a user's roles add up to, or everything for a superuser."""
    if user.is_superuser:
        return Grants(everything=True)
    granted: dict[str, set[str]] = {}
    for role in user.roles:
        # The column holds JSON, which someone may have written by hand.
        if not isinstance(role.permissions, Mapping):
            continue
        for name, permissions in role.permissions.items():
            if isinstance(permissions, list):
                granted.setdefault(name, set()).update(map(str, permissions))
    return Grants(granted)


class AdminUsers(AuthProvider, Plugin):
    """Admin users and roles, kept in the admin's own database.

    ```python
    from adminsite import Admin
    from adminsite.users import AdminUsers

    admin = Admin(
        engine,
        views=[OrderView, CustomerView],
        auth=AdminUsers(),
        secret_key=settings.admin_secret_key,
    )
    ```

    Each person signs in with a username and a password, and may do what
    their roles grant, or everything as a superuser. The tables are
    `adminsite_users`, `adminsite_roles` and `adminsite_user_roles`, on
    `user_metadata`. adminsite never creates them in your database uninvited,
    so add `user_metadata` to your migrations, or pass `create_table=True`.

    Args:
        create_table: True to create the tables on first use, for a quick
            start.
    """

    name = "users"

    def __init__(self, *, create_table: bool = False) -> None:
        self.create_table = create_table
        self._tables: _Tables | None = None
        self._database_zone: tzinfo = UTC
        self._secret = ""

    def setup(self, admin: "Admin") -> None:
        """Keep the admin's database and secret key, and add the Users and Roles."""
        self._tables = _Tables(admin.database, create_table=self.create_table)
        self._database_zone = known_timezone(admin.database_timezone)
        self._secret = admin.secret_key
        users = UserView(admin.inspector, admin.field_registry)
        users._users = self
        admin.add_view(users)
        admin.add_view(role_view(admin))

    def keep_signed_in(self, request: Request, user: User) -> None:
        """Keep this session signed in after the user's own password changed."""
        request.session[CHECK_KEY] = self._check(user)

    async def _database(self) -> Database:
        """The admin's database, with the tables made if this may make them."""
        if self._tables is None:
            raise AdminSiteError(
                "AdminUsers works once an admin has it as its auth: "
                "Admin(engine, auth=AdminUsers(), secret_key=...)."
            )
        await self._tables.prepare()
        return self._tables.database

    def _now(self) -> datetime:
        """Now, as the users' tables keep a time."""
        return to_column_time(datetime.now(UTC), stored_in=self._database_zone)

    async def verify(self, username: str, password: str) -> User | None:
        """Check the password, and that the account is switched on."""
        database = await self._database()
        wanted = select(User).where(User.username == normal_username(username))
        async with database.session() as session:
            found: User | None = await session.scalar(wanted)
        # An unknown name is checked against a hash of nobody, so it takes as
        # long as a wrong password, and the hashing runs on a thread.
        stored = found.password_hash if found is not None else _no_one()
        matched = await anyio.to_thread.run_sync(verify_password, password, stored)
        if found is None:
            raise SignInRefusedError(_("There is no such username."))
        if not matched:
            raise SignInRefusedError(_("The password was wrong."), user=found)
        if not found.is_active:
            raise SignInRefusedError(_("This account is switched off."), user=found)
        return found

    def identity(self, user: Any) -> str:
        """The user's id, which stays the same when their username changes."""
        return str(user.id)

    async def load_user(self, key: str) -> User | None:
        """The user and their roles, in one query, while they are switched on."""
        try:
            wanted = int(key)
        except ValueError:
            return None
        database = await self._database()
        statement = (
            select(User)
            .where(User.id == wanted, User.is_active.is_(True))
            .options(joinedload(User.roles))
        )
        async with database.session() as session:
            result = await session.execute(statement)
            found: User | None = result.unique().scalar_one_or_none()
        return found

    async def grants_for(self, user: Any) -> Grants | None:
        """What the user's roles add up to, or everything for a superuser."""
        return grants_from(user) if isinstance(user, User) else Grants()

    async def current_user(self, request: Request) -> Any | None:
        """The signed in user, unless their password has changed since."""
        user = await super().current_user(request)
        if not isinstance(user, User):
            return None
        kept = (request.scope.get("session") or {}).get(CHECK_KEY)
        expected = self._check(user)
        if not isinstance(kept, str) or not hmac.compare_digest(kept, expected):
            return None
        return user

    async def sign_in(
        self, request: Request, username: str, password: str
    ) -> Any | None:
        """Sign the user in, keeping their password's check and the time."""
        user = await super().sign_in(request, username, password)
        if isinstance(user, User):
            request.session[CHECK_KEY] = self._check(user)
            await self._note_sign_in(user)
        return user

    def _check(self, user: User) -> str:
        """A check of the password hash that only the secret key can make."""
        secret, stored = self._secret.encode(), user.password_hash.encode()
        return hmac.new(secret, stored, "sha256").hexdigest()

    async def _note_sign_in(self, user: User) -> None:
        """Write down when the user signed in."""
        moment = self._now()
        database = await self._database()
        statement = (
            update(User).where(User.id == user.id).values(last_sign_in_at=moment)
        )
        async with database.session() as session:
            await session.execute(statement)
            await session.commit()
        user.last_sign_in_at = moment

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        superuser: bool = False,
        roles: Sequence[str] = (),
    ) -> User:
        """Add someone who may sign in, such as the first superuser.

        ```python
        await users.create_user("lena", "a long password", roles=["Support"])
        ```

        Args:
            username: What they sign in with, kept in small letters.
            password: Their password, hashed before it is kept.
            superuser: True to let them do everything, managing users and
                roles included.
            roles: The names of the roles they hold, each made already.

        Returns:
            The user, saved.

        Raises:
            AdminSiteError: When the username is empty or taken, the password
                is too short, or no role has one of the names.
        """
        name = normal_username(username)
        if not name:
            raise AdminSiteError("A user needs a username.")
        problem = password_problem(password)
        if problem is not None:
            raise AdminSiteError(problem)
        hashed = await anyio.to_thread.run_sync(hash_password, password)
        database = await self._database()
        async with database.session() as session:
            named = select(Role).where(Role.name.in_(roles))
            held = list(await session.scalars(named)) if roles else []
            missing = sorted(set(roles) - {role.name for role in held})
            if missing:
                raise AdminSiteError(
                    f"No role is called {missing[0]!r}. Make it first with create_role."
                )
            user = User(
                username=name,
                password_hash=hashed,
                is_superuser=superuser,
                created_at=self._now(),
                roles=held,
            )
            await session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                raise AdminSiteError(f"The username {name!r} is taken.") from None
        return user

    async def create_role(
        self,
        name: str,
        permissions: Mapping[str, Collection[str]],
        *,
        description: str | None = None,
    ) -> Role:
        """Add a role, such as one a project starts with.

        ```python
        await users.create_role("Support", {"orders": ["view", "edit", "refund"]})
        ```

        Args:
            name: What the role is called, such as "Support".
            permissions: What it grants, by view, as `Role.permissions` keeps
                it.
            description: What the role is for, in a sentence.

        Returns:
            The role, saved.

        Raises:
            AdminSiteError: When the name is empty, or a role has it already.
        """
        called = name.strip()
        if not called:
            raise AdminSiteError("A role needs a name.")
        role = Role(
            name=called,
            description=description,
            permissions={
                view: sorted({str(one) for one in granted})
                for view, granted in permissions.items()
            },
        )
        database = await self._database()
        async with database.session() as session:
            await session.add(role)
            try:
                await session.commit()
            except IntegrityError:
                raise AdminSiteError(f"A role is already called {called!r}.") from None
        return role
