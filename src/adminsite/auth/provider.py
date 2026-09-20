import secrets
from collections.abc import Mapping
from typing import Any

from starlette.requests import Request

SESSION_KEY = "adminsite_user"


class AuthProvider:
    """Decides who may use the admin.

    Subclass it and write `verify`. The session handling here is enough
    for most projects, and `load_user` is where you turn the key kept in
    the session back into whatever your application calls a user.
    """

    async def verify(self, username: str, password: str) -> Any | None:
        """Return the user for these details, or nothing."""
        raise NotImplementedError

    async def load_user(self, key: str) -> Any | None:
        """Turn the key kept in the session back into a user."""
        return key

    def identity(self, user: Any) -> str:
        """The key to keep in the session for this user."""
        return str(user)

    async def current_user(self, request: Request) -> Any | None:
        """Who is signed in, if anyone."""
        session = request.scope.get("session")
        if not session:
            return None
        key = session.get(SESSION_KEY)
        return await self.load_user(key) if key else None

    async def sign_in(
        self, request: Request, username: str, password: str
    ) -> Any | None:
        """Check the details and remember the user."""
        user = await self.verify(username, password)
        if user is None:
            return None
        request.session[SESSION_KEY] = self.identity(user)
        return user

    async def sign_out(self, request: Request) -> None:
        """Forget the user."""
        session = request.scope.get("session")
        if session:
            session.pop(SESSION_KEY, None)


class PasswordAuth(AuthProvider):
    """Signs in against a fixed set of username and password pairs.

    Useful for a small internal tool and for getting started. Anything
    larger should subclass `AuthProvider` and check its own user table.
    """

    def __init__(self, users: Mapping[str, str]) -> None:
        self.users = dict(users)

    async def verify(self, username: str, password: str) -> Any | None:
        """Compare the password without leaking how much of it matched."""
        stored = self.users.get(username)
        if stored is None:
            return None
        if not secrets.compare_digest(stored, password):
            return None
        return username
