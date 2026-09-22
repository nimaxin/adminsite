# Getting started

## Install

```
pip install adminsite==0.1.0a2
```

adminsite does not pull in a database driver. Add the one you use, for example `asyncpg` or
`psycopg` for Postgres, or `aiosqlite` for SQLite with an async engine.

## Your first admin

Say you have these models:

```python
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)

    def __str__(self) -> str:
        return self.name


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    note: Mapped[str | None] = mapped_column(String(500), default=None)

    customer: Mapped[Customer] = relationship()
```

Describe how each one should appear, and mount the admin:

```python
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from adminsite import Admin, ModelView

engine = create_async_engine("postgresql+asyncpg://localhost/shop")


class CustomerView(ModelView, model=Customer):
    list_display = ("name", "email")
    search_fields = ("name", "email")


class OrderView(ModelView, model=Order):
    list_display = ("id", "customer.name", "note")
    search_fields = ("id", "customer.name")


app = FastAPI()
admin = Admin(engine, title="Shop", views=[CustomerView, OrderView])
app.mount("/admin", admin)
```

Run the app and open `/admin`. You get a list for each model, with search, sorting and paging, and
pages to add, change and delete records. The order form offers a customer picker rather than a
number box, because `customer_id` is a foreign key.

A view with no settings at all still works: it shows every column. Settings only narrow and order
what is shown.

## Mounting elsewhere

The admin is an ASGI app, so it mounts wherever ASGI apps do.

=== "Starlette"

    ```python
    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(routes=[Mount("/admin", app=admin)])
    ```

=== "FastAPI"

    ```python
    app.mount("/admin", admin)
    ```

=== "Litestar"

    ```python
    from litestar import Litestar, asgi


    @asgi("/admin", is_mount=True)
    async def admin_app(scope, receive, send) -> None:
        await admin(scope, receive, send)


    app = Litestar(route_handlers=[admin_app])
    ```

Links inside the admin follow the path it is mounted on, so `/admin`, `/backoffice` or
`/tools/admin` all work without configuration.

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

Each admin keeps its own session, in a cookie named after its title (`adminsite_staff`,
`adminsite_support`), so signing in to one never signs you in to the other. Give two admins with the
same title different names with `session_cookie=`. The same view class can be used in both; each
admin gets its own instance.

## Signing in

An admin reachable from the internet needs a login. The quickest one:

```python
from adminsite.auth import PasswordAuth, hash_password

admin = Admin(
    engine,
    views=[CustomerView, OrderView],
    auth=PasswordAuth({"nima": "pbkdf2_sha256$600000$..."}),
    secret_key=settings.admin_secret_key,
)
```

Make the hash once with `hash_password("your password")` and keep the result in your settings.
See [Signing in](auth.md) for checking your own user table instead.

## Trying the example

The repository has a small shop with an admin already set up:

```
git clone https://github.com/nimaxin/adminsite
cd adminsite
uv run uvicorn examples.shop:app --reload
```

Open http://127.0.0.1:8000/admin and sign in as `nima` / `letmein`.
