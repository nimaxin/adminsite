"""The admin's own users and roles, in tables of their own."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, ForeignKey, MetaData, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from adminsite.timezones import to_column_time

__all__ = [
    "Role",
    "User",
    "user_metadata",
    "user_roles",
]

# The tables live on their own metadata, so a project can add them to its
# migrations without them ever mixing with the project's own models.
user_metadata = MetaData()


def _now() -> datetime:
    """Now, as a column without a zone keeps it in the admin's database."""
    return to_column_time(datetime.now(UTC))


class _Base(DeclarativeBase):
    """The base of the admin's own models, on `user_metadata`."""

    metadata = user_metadata


user_roles = Table(
    "adminsite_user_roles",
    user_metadata,
    Column(
        "user_id",
        ForeignKey("adminsite_users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        ForeignKey("adminsite_roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Role(_Base):
    """Permissions given together, such as everything a support agent needs.

    A person may hold several roles, and may do whatever any of them grants.
    """

    __tablename__ = "adminsite_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    """The role's key."""
    name: Mapped[str] = mapped_column(String(100), unique=True)
    """What the role is called, such as "Support"."""
    description: Mapped[str | None] = mapped_column(Text, default=None)
    """What the role is for, in a sentence."""
    permissions: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    """What the role grants, by view: `{"orders": ["view", "edit"]}`.

    A page of your own is named by "-/" and its name, such as
    "-/sales_report".
    """
    users: Mapped[list["User"]] = relationship(
        secondary=user_roles, back_populates="roles"
    )
    """The people who hold the role."""

    def __str__(self) -> str:
        return self.name


class User(_Base):
    """Someone who may sign in to the admin."""

    __tablename__ = "adminsite_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    """The user's key, which the session and the audit log keep."""
    username: Mapped[str] = mapped_column(String(150), unique=True)
    """What they sign in with, kept in small letters."""
    password_hash: Mapped[str] = mapped_column(String(200))
    """Their password, as `hash_password` made it."""
    is_active: Mapped[bool] = mapped_column(default=True)
    """Whether they may sign in. Switched off, they are signed out at once."""
    is_superuser: Mapped[bool] = mapped_column(default=False)
    """Whether they may do everything, managing users and roles included."""
    last_sign_in_at: Mapped[datetime | None] = mapped_column(default=None)
    """When they last signed in, or None if they never have."""
    created_at: Mapped[datetime] = mapped_column(default=_now)
    """When the account was made."""
    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users"
    )
    """The roles they hold."""

    def has_role(self, name: str) -> bool:
        """Whether they hold the role with this name.

        ```python
        if request.state.user.has_role("Support"):
            ...
        ```

        Args:
            name: The role's name, such as "Support".

        Returns:
            True when one of their roles is called that.
        """
        return any(role.name == name for role in self.roles)

    def __str__(self) -> str:
        return self.username
