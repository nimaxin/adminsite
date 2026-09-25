from collections.abc import Mapping
from functools import cache
from typing import Any

import anyio
from starlette.requests import Request

from adminsite.auth.passwords import hash_password, looks_hashed, verify_password
from adminsite.exceptions import AdminSiteError, SignInRefused
from adminsite.i18n import gettext as _

SESSION_KEY = "adminsite_user"


class AuthProvider:
    """Decides who may use the admin.

    Subclass it and write `verify`. The session handling here is enough
    for most projects, and `load_user` is where you turn the key kept in
    the session back into whatever your application calls a user.
    """

    async def verify(self, username: str, password: str) -> Any | None:
        """Return the user for these details, or nothing.

        To have the audit log say why an attempt failed, raise
        `SignInRefused("This account is switched off.", user=account)`
        instead of returning nothing.
        """
        raise NotImplementedError

    async def load_user(self, key: str) -> Any | None:
        """Turn the key kept in the session back into a user."""
        return key

    async def authenticate_token(self, token: str) -> Any | None:
        """Return the user an API token belongs to, or nothing.

        The JSON API calls this for `Authorization: Bearer <token>`. Nobody
        gets in this way until you write it, for example by looking the
        token up in a table of API keys.
        """
        return None

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
        # Nothing from before the sign in comes with it, not even the
        # form token, so a session planted in the browser earlier is
        # worth nothing once the person is signed in.
        request.session.clear()
        request.session[SESSION_KEY] = self.identity(user)
        return user

    async def sign_in_failed(self, request: Request, username: str) -> str:
        """Called when a sign in fails. Returns what to tell the person.

        Override it to record the attempt, to make the next one wait, or
        to say something other than the default. Whatever it returns is
        shown above the form, so keep it vague: a message that says the
        username exists tells an attacker so too.
        """
        return _("That username and password do not match.")

    async def sign_in_values(self, request: Request) -> Mapping[str, str]:
        """What the sign in form starts with, by input name.

        Nothing by default. A public demo can fill in its shared username
        and password, so visitors only press Sign in. Never put real
        credentials here: anyone who opens the page can read them.
        """
        return {}

    async def may_read_sign_ins(
        self, request: Request, *, reads_everything: bool
    ) -> bool:
        """Whether this person sees who signed in, on the Activity page.

        Signing in belongs to no model, so no view's permissions decide it.
        By default only someone who may read the history of every model
        sees it, which `reads_everything` says. Override it to let in, say,
        an auditor who reads less.
        """
        return reads_everything

    async def sign_out(self, request: Request) -> None:
        """Forget the user."""
        session = request.scope.get("session")
        if session:
            session.clear()


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
        # An unknown name is checked against a hash of nothing in particular,
        # so it takes as long as a wrong password does. Answering at once
        # would tell whoever is guessing which usernames exist. The hashing
        # runs on a thread: 600,000 rounds would hold up every other request.
        matched = await anyio.to_thread.run_sync(
            verify_password, password, stored if stored is not None else _no_one()
        )
        if stored is None:
            raise SignInRefused(_("There is no such username."))
        if not matched:
            raise SignInRefused(_("The password was wrong."), user=username)
        return username


@cache
def _no_one() -> str:
    """A hash for a user who does not exist, made once and kept."""
    return hash_password("nobody")
