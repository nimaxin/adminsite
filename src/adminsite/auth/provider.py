from collections.abc import Mapping
from typing import Any

from starlette.requests import Request

from adminsite.auth.passwords import looks_hashed, verify_password
from adminsite.exceptions import AdminSiteError

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
    """Signs in against a fixed set of usernames and password hashes.

    ```python
    from adminsite.auth import PasswordAuth, hash_password

    PasswordAuth({"nima": hash_password("letmein")})
    ```

    Passwords have to be hashed with `hash_password`, so a plain one
    never ends up in your settings or your repository. This suits a
    small internal tool. Anything larger should subclass `AuthProvider`
    and check its own user table.
    """

    def __init__(self, users: Mapping[str, str]) -> None:
        for username, stored in users.items():
            if not looks_hashed(stored):
                raise AdminSiteError(
                    f"The password for {username!r} is not hashed. "
                    "Use adminsite.auth.hash_password to hash it first."
                )
        self.users = dict(users)

    async def verify(self, username: str, password: str) -> Any | None:
        """Check the password against the stored hash."""
        stored = self.users.get(username)
        if stored is None:
            return None
        return username if verify_password(password, stored) else None
