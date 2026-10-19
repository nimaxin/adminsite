"""The Users and Roles pages, which only superusers open."""

from typing import TYPE_CHECKING, Any, TypeVar

import anyio
from sqlalchemy import select
from starlette.requests import Request

from adminsite.auth.passwords import hash_password
from adminsite.database import SessionAdapter
from adminsite.exceptions import RefusedError
from adminsite.i18n import gettext as _
from adminsite.permissions import Permission
from adminsite.users.fields import (
    OwnLinks,
    OwnLongText,
    OwnPassword,
    OwnSwitch,
    OwnText,
    OwnTime,
    PermissionsField,
)
from adminsite.users.models import Role, User
from adminsite.users.rules import normal_username, password_problem
from adminsite.views import DeleteContext, ModelView, SaveContext

if TYPE_CHECKING:
    from adminsite.admin import Admin
    from adminsite.users.provider import AdminUsers

__all__ = [
    "AccessView",
    "RoleView",
    "UserView",
    "role_view",
]

M = TypeVar("M")


def _is_superuser(request: Request) -> bool:
    """Whether the person asking is a superuser of these users."""
    user = request.scope.get("user_record")
    return isinstance(user, User) and user.is_superuser


async def _another_superuser(session: SessionAdapter, key: int | None) -> bool:
    """Whether an active superuser other than this one is left."""
    others = select(User.id).where(
        User.is_superuser.is_(True), User.is_active.is_(True)
    )
    if key is not None:
        others = others.where(User.id != key)
    return await session.scalar(others.limit(1)) is not None


def _keep_a_superuser() -> RefusedError:
    """The refusal of a change that would leave nobody to manage users."""
    return RefusedError(
        _("Keep at least one active superuser, or nobody can manage users.")
    )


class AccessView(ModelView[M]):
    """A page of the sidebar's Admin section, which only superusers open."""

    @property
    def group(self) -> str:
        """The sidebar's Admin section, beside Activity, in the reader's language."""
        return _("Admin")

    @group.setter
    def group(self, value: str) -> None:
        """Kept in adminsite's own words, whatever the view is given."""

    async def allows(
        self, action: Permission | str, *, request: Request, record: M | None
    ) -> bool:
        """Only superusers, and no role can grant it, so nobody raises their own."""
        if not _is_superuser(request):
            return False
        return await super().allows(action, request=request, record=record)


class UserView(AccessView[User]):
    """The Users page: who may sign in, and with which roles."""

    name = "admin_users"
    fields = (
        OwnText(User.username, label="Username"),
        OwnPassword("password", label="Password", required=True),
        # On for someone new, who could not sign in otherwise.
        OwnSwitch(User.is_active, label="Active", default=True),
        OwnSwitch(User.is_superuser, label="Superuser"),
        OwnLinks(User.roles, label="Roles"),
        OwnTime(
            User.last_sign_in_at,
            label="Last sign in",
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
    )
    searchable_fields = (User.username,)
    list_filters = (User.is_active, User.is_superuser, User.roles)
    fields_default_sort = (User.username,)

    # The provider the users sign in through, set when the admin is built, so
    # a superuser who changes their own password stays signed in.
    _users: "AdminUsers | None" = None

    @property
    def label(self) -> str:
        """What one record is called, in the reader's language."""
        return _("User")

    @label.setter
    def label(self, value: str) -> None:
        """Kept in adminsite's own words, whatever the view is given."""

    @property
    def label_plural(self) -> str:
        """What the records are called, in the reader's language."""
        return _("Users")

    @label_plural.setter
    def label_plural(self, value: str) -> None:
        """Kept in adminsite's own words, whatever the view is given."""

    async def before_save(self, context: SaveContext[User]) -> None:
        """Keep the username in small letters, hash a new password, keep a superuser."""
        values = context.values
        if User.username in values:
            username = normal_username(values[User.username].get() or "")
            values[User.username].set(username)
            taken = select(User.id).where(User.username == username)
            if not context.created:
                taken = taken.where(User.id != context.record.id)
            if await context.session.scalar(taken.limit(1)) is not None:
                raise RefusedError(_("That username is taken."), field=User.username)
        password = values["password"].get()
        if password:
            problem = password_problem(password)
            if problem is not None:
                raise RefusedError(problem, field="password")
            hashed = await anyio.to_thread.run_sync(hash_password, password)
            values[User.password_hash].set(hashed)
        if context.created:
            return
        record = context.record
        was_one = record.is_superuser and record.is_active
        stays_one = bool(values[User.is_superuser].get()) and bool(
            values[User.is_active].get()
        )
        if (
            was_one
            and not stays_one
            and not await _another_superuser(context.session, record.id)
        ):
            raise _keep_a_superuser()

    async def after_save_committed(self, context: SaveContext[User]) -> None:
        """Keep a superuser who changed their own password signed in."""
        current = context.request.scope.get("user_record")
        if (
            self._users is not None
            and isinstance(current, User)
            and current.id == context.record.id
        ):
            self._users.keep_signed_in(context.request, context.record)

    async def before_delete(self, context: DeleteContext[User]) -> None:
        """Refuse to delete the last active superuser."""
        record = context.record
        if (
            record.is_superuser
            and record.is_active
            and not await _another_superuser(context.session, record.id)
        ):
            raise _keep_a_superuser()


class RoleView(AccessView[Role]):
    """The Roles page: what each role grants, view by view."""

    name = "admin_roles"
    searchable_fields = (Role.name,)
    exclude_fields_from_list = (Role.permissions,)
    fields_default_sort = (Role.name,)

    @property
    def label(self) -> str:
        """What one record is called, in the reader's language."""
        return _("Role")

    @label.setter
    def label(self, value: str) -> None:
        """Kept in adminsite's own words, whatever the view is given."""

    @property
    def label_plural(self) -> str:
        """What the records are called, in the reader's language."""
        return _("Roles")

    @label_plural.setter
    def label_plural(self, value: str) -> None:
        """Kept in adminsite's own words, whatever the view is given."""


def role_view(admin: "Admin") -> type[RoleView]:
    """The Roles page of one admin, its grid drawn from that admin's views."""
    fields: tuple[Any, ...] = (
        OwnText(Role.name, label="Name"),
        OwnLongText(Role.description, label="Description"),
        # A role may grant nothing yet, so the grid is never required.
        PermissionsField(
            Role.permissions, label="Permissions", required=False, admin=admin
        ),
        OwnLinks(
            Role.users,
            label="Users",
            exclude_from_list=True,
            exclude_from_create=True,
            exclude_from_edit=True,
        ),
    )
    return type("RoleView", (RoleView,), {"fields": fields})
