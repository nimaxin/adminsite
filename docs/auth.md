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

`secret_key` signs the session cookie. Make it long and random, keep it secret, and read it from
your settings.

## Your own user table

For more than a handful of people, subclass `AuthProvider` and check your own users:

```python
from sqlalchemy import select

from adminsite.auth import AuthProvider, SignInRefusedError, verify_password


class StaffAuth(AuthProvider):
    async def verify(self, username: str, password: str) -> User | None:
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.email == username))
        if user is None or not verify_password(password, user.password_hash):
            return None
        if not user.is_staff:
            raise SignInRefusedError("Not a member of staff.", user=user)
        return user

    def identity(self, user: User) -> str:
        return str(user.id)

    async def load_user(self, key: str) -> User | None:
        async with session_factory() as session:
            return await session.get(User, int(key))
```

`verify` returns the user or `None`, or raises `SignInRefusedError`, whose reason goes to the
[audit log](audit.md) while the person signing in sees only that it failed. `identity` turns the
user into the short string kept in the session cookie, and `load_user` turns it back on each
request. [`AuthProvider`][adminsite.auth.AuthProvider] lists the other methods.

The loaded user is on `request.state.user`, for your [permission](permissions.md) checks. Its
`str()` is what the sidebar and the audit log show.

## When a sign in fails

The login page says "That username and password do not match." Override `sign_in_failed` to say
something else, or to do something about it, such as slowing down an address that keeps guessing:

```python
from starlette.requests import Request


class StaffAuth(AuthProvider):
    async def sign_in_failed(self, request: Request, username: str) -> str:
        address = request.client.host if request.client else ""
        await note_attempt(username, address)
        if await too_many_lately(address):
            return "Too many tries. Wait a minute and try again."
        return await super().sign_in_failed(request, username)
```

Keep the message vague. One that says the username exists tells whoever is guessing the same
thing.

## A public demo

A demo that everyone signs in to with the same account can fill in the form, so visitors only
press Sign in:

```python
class DemoAuth(PasswordAuth):
    async def sign_in_values(self, request: Request) -> dict[str, str]:
        return {"username": "admin", "password": "admin"}


admin = Admin(
    engine,
    auth=DemoAuth({"admin": hash_password("admin")}),
    secret_key=settings.admin_secret_key,
)
```

!!! warning "Only for a demo"

    Anyone who opens the sign in page can read what this fills in. Never use it with real
    credentials.

## CSRF

Every form the admin draws carries a token tied to the session, and every post is checked against
it. A script that posts to the admin can send the token in an `X-CSRF-Token` header, taken from a
page drawn after signing in. Without a `secret_key` there is no session and no token, and a post
from another site is refused by the `Sec-Fetch-Site` and `Origin` headers browsers send.

## The session cookie

The cookie is signed with `secret_key` and lasts two weeks. Serve the admin over HTTPS and say so,
and the cookie is never sent over plain HTTP:

```python
admin = Admin(
    engine,
    auth=auth,
    secret_key=settings.admin_secret_key,
    session_https_only=True,
    session_max_age=8 * 3600,
)
```

`session_max_age=None` keeps the cookie for the browser session only.

## Several admins in one app

Mount as many as you like, each with its own views, users and settings:

```python
staff = Admin(
    engine,
    title="Staff",
    views=[OrderView, CustomerView, ProductView],
    auth=staff_auth,
    secret_key=settings.staff_secret,
)
support = Admin(
    engine,
    title="Support",
    views=[OrderView, CustomerView],
    auth=support_auth,
    secret_key=settings.support_secret,
)

app.mount("/staff", staff)
app.mount("/support", support)
```

Each admin has its own session cookie, named after its title, so signing in to one does not sign
you in to the other. Two admins with the same title need `session_cookie=` to tell them apart.
