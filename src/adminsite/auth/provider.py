from collections.abc import Mapping
from functools import cache
from typing import Any

import anyio
from starlette.requests import Request

from adminsite.auth.passwords import hash_password, looks_hashed, verify_password
from adminsite.exceptions import AdminSiteError, SignInRefusedError
from adminsite.i18n import gettext as _

__all__ = [
    "SESSION_KEY",
    "AuthProvider",
    "PasswordAuth",
]

SESSION_KEY = "adminsite_user"


class AuthProvider:
    """Decides who may use the admin.

    Subclass it and write `verify`. The session handling here is enough
    for most projects, and `load_user` is where you turn the key kept in
    the session back into whatever your application calls a user.
    """

    async def verify(self, username: str, password: str) -> Any | None:
        """Return the user for these details, or nothing.

        Args:
            username: What was typed as the username.
            password: What was typed as the password.

        Returns:
            Whatever your application calls a user, or None when the details
            do not match.

        Raises:
            SignInRefusedError: Instead of returning None, to have the audit
                log say why the attempt failed, such as
                `SignInRefusedError("This account is switched off.",
                user=account)`.
        """
        raise NotImplementedError

    async def load_user(self, key: str) -> Any | None:
        """Turn the key kept in the session back into a user.

        The key itself by default. Override it to read the user from your
        own table, so a user switched off since is signed out at once.

        Args:
            key: What `identity` returned when the user signed in.

        Returns:
            The user, or None to sign them out.
        """
        return key

    async def authenticate_token(self, token: str) -> Any | None:
        """Return the user an API token belongs to, or nothing.

        The JSON API calls this for `Authorization: Bearer <token>`. Nobody
        gets in this way until you write it, for example by looking the
        token up in a table of API keys.

        Args:
            token: The token, without "Bearer ".

        Returns:
            The user, or None to refuse the request.
        """
        return None

    def identity(self, user: Any) -> str:
        """The key to keep in the session for this user.

        The audit log keeps it too, as `user_key`, which does not change
        when the user's name does.

        Args:
            user: What `verify` returned.

        Returns:
            The key, such as the user's id as text. `str(user)` by default.
        """
        return str(user)

    async def current_user(self, request: Request) -> Any | None:
        """Who is signed in, if anyone.

        Args:
            request: The request being answered.

        Returns:
            The user, as `load_user` answers, or None.
        """
        session = request.scope.get("session")
        if not session:
            return None
        key = session.get(SESSION_KEY)
        return await self.load_user(key) if key else None

    async def sign_in(
        self, request: Request, username: str, password: str
    ) -> Any | None:
        """Check the details and remember the user.

        Args:
            request: The request being answered.
            username: What was typed as the username.
            password: What was typed as the password.

        Returns:
            The user, now kept in the session, or None when `verify` refused.
        """
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

        Args:
            request: The request being answered.
            username: What was typed as the username.

        Returns:
            What to tell the person.
        """
        return _("That username and password do not match.")

    async def sign_in_values(self, request: Request) -> Mapping[str, str]:
        """What the sign in form starts with, by input name.

        Nothing by default. A public demo can fill in its shared username
        and password, so visitors only press Sign in. Never put real
        credentials here: anyone who opens the page can read them.

        Args:
            request: The request being answered.

        Returns:
            Starting values by input name: "username" and "password".
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

        Args:
            request: The request being answered.
            reads_everything: Whether this person may read the history of
                every view.

        Returns:
            True to show them the sign ins.
        """
        return reads_everything

    async def sign_out(self, request: Request) -> None:
        """Forget the user.

        Args:
            request: The request being answered.
        """
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

    Args:
        users: Each username with its password hash.

    Raises:
        AdminSiteError: When a password is not hashed.
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
        """Check the password against the stored hash.

        Args:
            username: What was typed as the username.
            password: What was typed as the password.

        Returns:
            The username, as the user.

        Raises:
            SignInRefusedError: When there is no such username, or the
                password is wrong, so the audit log says which.
        """
        stored = self.users.get(username)
        # An unknown name is checked against a hash of nothing in particular,
        # so it takes as long as a wrong password does. Answering at once
        # would tell whoever is guessing which usernames exist. The hashing
        # runs on a thread: 600,000 rounds would hold up every other request.
        matched = await anyio.to_thread.run_sync(
            verify_password, password, stored if stored is not None else _no_one()
        )
        if stored is None:
            raise SignInRefusedError(_("There is no such username."))
        if not matched:
            raise SignInRefusedError(_("The password was wrong."), user=username)
        return username


@cache
def _no_one() -> str:
    """A hash for a user who does not exist, made once and kept."""
    return hash_password("nobody")
