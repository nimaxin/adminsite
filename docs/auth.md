# Signing in

Without an auth provider the admin is open to anyone who can reach it. That is fine behind a VPN or
on your own machine, and nowhere else.

## A fixed list of users

```python
from adminsite.auth import PasswordAuth

admin = Admin(
    engine,
    views=[OrderView],
    auth=PasswordAuth({"nima": settings.admin_password_hash}),
    secret_key=settings.admin_secret_key,
)
```

`PasswordAuth` takes password hashes, never passwords, and refuses a plain one at startup. Make a
hash once and keep the result in your settings:

```
python -c "from adminsite.auth import hash_password; print(hash_password('your password'))"
```

Hashes use PBKDF2 with SHA-256 and 600,000 rounds, from the standard library.

`secret_key` signs the session cookie. Keep it secret, make it long and random, and read it from
your settings. Passing `auth` without one is refused at startup.

## Your own user table

For anything more than a handful of people, subclass `AuthProvider` and check your own users:

```python
from sqlalchemy import select

from adminsite.auth import AuthProvider, verify_password


class StaffAuth(AuthProvider):
    async def verify(self, username: str, password: str):
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.email == username))
        if user is None or not user.is_staff:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def identity(self, user) -> str:
        return str(user.id)

    async def load_user(self, key: str):
        async with session_factory() as session:
            return await session.get(User, int(key))
```

| Method | What it does |
|---|---|
| `verify(username, password)` | Returns the user for these details, or `None`. |
| `identity(user)` | The short string kept in the session cookie. |
| `load_user(key)` | Turns that string back into a user on each request. |
| `sign_in_failed(request, username)` | Runs when a sign in fails, and returns what to say. |

The loaded user is on `request.scope["user_record"]`, for your [permission](permissions.md) checks,
and its `str()` is what the sidebar and the [audit log](audit.md) show.

## When a sign in fails

The login page says "That username and password do not match." Override `sign_in_failed` to say
something else, and to do something about it. It is the one place that sees every wrong password,
so it is where a record of attempts, an alert or a wait belongs:

```python
class StaffAuth(AuthProvider):
    async def sign_in_failed(self, request, username: str) -> str:
        await note_attempt(username, request.client.host)
        if await too_many_lately(request.client.host):
            return "Too many tries. Wait a minute and try again."
        return await super().sign_in_failed(request, username)
```

Keep the message vague. One that says the username exists tells whoever is guessing the same
thing.

## CSRF

Every form the admin draws carries a token tied to the session, and every post is checked against
it, including deletes and actions. A post without the right token is refused with a 403. Scripts
that post to the admin can send the token in an `X-CSRF-Token` header instead of a form field.

The [JSON API](api.md) also takes a bearer token, once your provider's `authenticate_token`
says who it belongs to.

Signing in starts a fresh session, so a token taken from the login page stops working once the
person is signed in. A script that drives the admin through a session takes its token from a page
drawn after signing in.

Without a `secret_key` there is no session, and so no token. That does not leave the admin open to
a form posted from another site: browsers say where a post came from, in the `Sec-Fetch-Site` and
`Origin` headers, and a post from elsewhere is refused. A request that carries neither, from a
script or `curl`, goes through, as it did before. This matters for an admin left open on a private
network, where the browser of anyone on that network could otherwise be made to post to it.

## The session cookie

The cookie is signed with `secret_key` and lasts two weeks. Serve the admin over HTTPS and say so,
and the cookie is never sent over plain HTTP:

```python
admin = Admin(
    engine, auth=auth, secret_key=..., session_https_only=True, session_max_age=8 * 3600
)
```

`session_max_age=None` keeps the cookie for the browser session only.
