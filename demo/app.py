"""The public demo: the example shop, open to anyone and reset every hour.

    uv run --group demo uvicorn demo.app:app

Then open http://127.0.0.1:8000/admin and sign in as admin / admin.
"""

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from adminsite import Admin, FieldOptions, SavedViews
from adminsite.audit import AuditLog, audit_metadata
from adminsite.auth import PasswordAuth, hash_password
from adminsite.fields import ImageField
from adminsite.files import LocalStorage
from adminsite.saved_views import saved_view_metadata
from examples.shop import (
    EUROS,
    Base,
    CustomerView,
    OrderView,
    ProductView,
    build_sample_shop,
    dashboard,
)

# uvicorn prints this logger, so each reset shows in the server's log.
logger = logging.getLogger("uvicorn.error")

BANNER = (
    "This is a public demo. Sign in as admin with the password admin. "
    "Anything you change is undone every hour."
)

RESET_SECONDS = 3600
MEGABYTE = 1024 * 1024

# Everything a visitor can change: the shop, its history and saved views.
TABLES = (
    *reversed(Base.metadata.sorted_tables),
    *audit_metadata.sorted_tables,
    *saved_view_metadata.sorted_tables,
)


class DemoCustomerView(CustomerView):
    """The example's customers, with a smaller import for strangers."""

    import_limit = 200


def seconds_until_reset(now: float) -> float:
    """How long until the next reset, which falls at the start of an hour."""
    return RESET_SECONDS - now % RESET_SECONDS


async def reset(engine: AsyncEngine, uploads: Path) -> None:
    """Put the shop back as it started, with no history, views or uploads.

    It happens in one transaction, so a visitor never sees an empty shop.
    """
    async with engine.begin() as connection:
        for metadata in (Base.metadata, audit_metadata, saved_view_metadata):
            await connection.run_sync(metadata.create_all)
    async with AsyncSession(engine) as session, session.begin():
        for table in TABLES:
            await session.execute(delete(table))
        session.add_all(build_sample_shop())
    await asyncio.to_thread(shutil.rmtree, uploads, ignore_errors=True)


async def reset_every_hour(engine: AsyncEngine, uploads: Path) -> None:
    """Reset the shop at the start of every hour, for as long as it runs."""
    while True:
        await asyncio.sleep(seconds_until_reset(time.time()))
        try:
            await reset(engine, uploads)
        except Exception:
            # The next hour tries again, and the demo stays up meanwhile.
            logger.exception("Resetting the demo failed.")
        else:
            logger.info("The demo is back as it started.")


def build_app(data: Path, secret_key: str) -> FastAPI:
    """The demo, keeping its database and uploads in the folder `data`."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{data / 'shop.db'}")
    uploads = data / "uploads"

    class DemoProductView(ProductView):
        """The example's products, with smaller photos kept beside the data."""

        fields = (
            ImageField("photo", storage=LocalStorage(uploads), max_size=MEGABYTE),
            FieldOptions("price", format=EUROS),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        data.mkdir(parents=True, exist_ok=True)
        await reset(engine, uploads)
        resetting = asyncio.create_task(reset_every_hour(engine, uploads))
        yield
        resetting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await resetting
        await engine.dispose()

    app = FastAPI(
        title="adminsite demo",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    admin = Admin(
        engine,
        title="Acme shop",
        banner=BANNER,
        views=[OrderView, DemoCustomerView, DemoProductView],
        dashboard=dashboard,
        auth=PasswordAuth({"admin": hash_password("admin")}),
        secret_key=secret_key,
        session_https_only=True,
        audit=AuditLog(engine, create_table=True),
        saved_views=SavedViews(engine, create_table=True),
    )
    app.mount("/admin", admin)
    app.state.admin = admin
    app.state.engine = engine
    app.state.uploads = uploads

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/admin/")

    @app.get("/healthz", include_in_schema=False)
    async def health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


app = build_app(
    Path(os.environ.get("DEMO_DATA", "demo-data")),
    # Without a key of its own, every restart signs everyone out.
    os.environ.get("DEMO_SECRET_KEY") or secrets.token_urlsafe(32),
)
