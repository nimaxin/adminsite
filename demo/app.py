"""The public demo, open to anyone: the shop and the gallery.

Every visitor works on a copy of their own, so nobody sees what anyone else
typed, and a copy nobody has used for an hour is thrown away.

    uv run --group demo uvicorn demo.app:app

Then open http://127.0.0.1:8000/admin and sign in as admin / admin.
"""

import asyncio
import contextlib
import html
import logging
import os
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from adminsite import Admin, SavedViews
from adminsite.audit import (
    AuditEntry,
    AuditLog,
    AuditQuery,
    audit_metadata,
)
from adminsite.audit.entry import SIGN_IN_EVENTS
from adminsite.auth import hash_password
from adminsite.database import Database, SessionAdapter
from adminsite.fields import ImageField
from adminsite.files import LocalStorage
from adminsite.saved_views import saved_view_metadata
from adminsite.users import AdminUsers, Role, User, user_metadata
from examples import fields as gallery
from examples.shop import (
    Base,
    CustomerView,
    OrderView,
    ProductView,
    TagView,
    build_sample_shop,
    dashboard,
)

# uvicorn prints this logger, so the demo's housekeeping shows in the server's log.
logger = logging.getLogger("uvicorn.error")

BANNER = (
    "This is a public demo. Sign in as admin with the password admin. "
    "Only you see what you change, and it is cleared after an hour away."
)

MEGABYTE = 1024 * 1024
# A copy nobody has used for this long is thrown away.
IDLE_SECONDS = 3600
# How often the demo looks for copies to throw away.
SWEEP_SECONDS = 60
# The most copies kept at once, oldest out first. A copy takes about 0.3 MB of
# memory and 125 KB of the data folder, which on the server is 256 MB, so 500
# leave most of that folder for what visitors upload.
MAX_VISITORS = 500
VISITOR_COOKIE = "adminsite_demo_visitor"
# What secrets.token_urlsafe(16) makes. The id names a folder, so the cookie is
# used only when it looks exactly like one.
VISITOR_ID = re.compile(r"[A-Za-z0-9_-]{22}")
COOKIE_SECONDS = 7 * 24 * 3600

# What a visitor without the cookie gets first. A browser goes on from it to
# the address it asked for, now with the cookie; a client that keeps no
# cookies, such as a link preview, stays here and reads the card.
WELCOME = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>adminsite demo</title>
<meta name="description" content="A public demo of adminsite, an admin panel for
SQLAlchemy 2.0. Sign in as admin with the password admin.">
<meta property="og:type" content="website">
<meta property="og:title" content="adminsite demo">
<meta property="og:description" content="A public demo of adminsite, an admin panel
for SQLAlchemy 2.0. Sign in as admin with the password admin.">
<meta property="og:image"
      content="https://nimaxin.github.io/adminsite/assets/social-preview.png">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta name="twitter:card" content="summary_large_image">
<style>body {{ margin: 3rem 1.5rem; font: 16px system-ui, sans-serif;
background: #f3f3f0; color: #1a1a18; }}</style>
</head>
<body>
<p id="note"><a href="{target}">Open the demo</a></p>
<script>
  if (navigator.cookieEnabled) {{
    location.replace(document.querySelector("#note a").href);
  }} else {{
    document.querySelector("#note").textContent =
      "The demo needs cookies, to keep your copy apart from everyone else's.";
  }}
</script>
</body>
</html>
"""

# Everything a visitor can change: the shop, the gallery, their history, saved
# views, and the people who sign in.
METADATA = (
    Base.metadata,
    gallery.Base.metadata,
    audit_metadata,
    saved_view_metadata,
    user_metadata,
)


class WhatNotWho:
    """The demo's log: what visitors did, but not where they came from.

    Everyone signs in as admin, so sign ins say nothing, and the demo keeps no
    visitor's address or browser. Both are left out of the log.
    """

    def __init__(self, store: AuditLog) -> None:
        self.store = store

    async def record(self, entries: Sequence[AuditEntry]) -> None:
        """Keep what was done, without who signed in or from where."""
        kept = self._kept(entries)
        if kept:
            await self.store.record(kept)

    def lives_in(self, database: Database) -> bool:
        """Whether the log is in that database: in the demo it always is."""
        return self.store.lives_in(database)

    async def record_within(
        self, session: SessionAdapter, entries: Sequence[AuditEntry]
    ) -> None:
        """Keep what was done in the same transaction as the change itself."""
        kept = self._kept(entries)
        if kept:
            await self.store.record_within(session, kept)

    def _kept(self, entries: Sequence[AuditEntry]) -> list[AuditEntry]:
        return [
            replace(entry, ip=None, user_agent=None)
            for entry in entries
            if entry.event not in SIGN_IN_EVENTS
        ]

    async def find(self, query: AuditQuery, *, limit: int) -> list[AuditEntry]:
        """Read the log as the store keeps it."""
        return await self.store.find(query, limit=limit)


class DemoUsers(AdminUsers):
    """The demo's people, with the superuser filled in on the sign in page."""

    async def sign_in_values(self, request: Request) -> Mapping[str, str]:
        """Everyone signs in as admin, so nobody has to find the details."""
        return {"username": "admin", "password": "admin"}


def build_people() -> list[User]:
    """The superuser everyone signs in as, and a support agent with a role.

    Written straight into the tables: the passwords are shorter than the
    Users page would take.
    """
    support = Role(
        name="Support",
        description="Answers customers and sends their orders.",
        permissions={
            "orders": ["view", "edit", "ship"],
            "customers": ["view", "edit"],
            "products": ["view"],
        },
    )
    return [
        User(username="admin", password_hash=hash_password("admin"), is_superuser=True),
        User(
            username="support", password_hash=hash_password("support"), roles=[support]
        ),
    ]


class DemoCustomerView(CustomerView):
    """The example's customers, with a smaller import for strangers."""

    import_limit = 200


async def build_template(folder: Path) -> None:
    """Write the shop and the gallery as they start, for every visitor to copy."""
    folder.mkdir(parents=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{folder / 'shop.db'}")
    try:
        async with engine.begin() as connection:
            for metadata in METADATA:
                await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine) as session, session.begin():
            session.add_all(build_sample_shop())
            uploads = folder / "uploads"
            session.add_all(await asyncio.to_thread(gallery.build_gallery, uploads))
            session.add_all(await asyncio.to_thread(build_people))
    finally:
        await engine.dispose()


def build_admin(folder: Path, secret_key: str) -> tuple[Admin, AsyncEngine]:
    """The admin of one visitor's copy, kept in the folder `folder`."""
    # One person uses a copy, now and then, so no connection waits between requests.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{folder / 'shop.db'}", poolclass=NullPool
    )
    uploads = folder / "uploads"

    class DemoProductView(ProductView):
        """The example's products, with smaller photos kept beside the data."""

        fields = [
            replace(entry, storage=LocalStorage(uploads), max_size=MEGABYTE)
            if isinstance(entry, ImageField)
            else entry
            for entry in ProductView.fields
        ]

    class DemoShowcaseView(gallery.ShowcaseView):
        """The gallery, its files kept beside the data and as small as photos."""

        fields = gallery.showcase_fields(LocalStorage(uploads), upload_limit=MEGABYTE)

    admin = Admin(
        engine,
        title="Acme shop",
        banner=BANNER,
        views=[
            OrderView,
            DemoCustomerView,
            DemoProductView,
            TagView,
            DemoShowcaseView,
            gallery.SettingView,
            gallery.CustomerGroupView,
            gallery.CategoryView,
            gallery.SupplierView,
            gallery.LabelView,
            gallery.ServerView,
        ],
        dashboard=dashboard,
        auth=DemoUsers(),
        secret_key=secret_key,
        session_https_only=True,
        audit=WhatNotWho(AuditLog(engine, create_table=True)),
        saved_views=SavedViews(engine, create_table=True),
        timezones=["Europe/Paris", "Asia/Tehran", "America/New_York"],
    )
    return admin, engine


@dataclass
class Copy:
    """One visitor's copy of the demo, and when it was last used."""

    admin: Admin
    engine: AsyncEngine
    folder: Path
    last_used: float
    # The requests it is answering right now; a busy copy is never thrown away.
    busy: int = 0


class Visitors:
    """Gives each visitor an admin of their own, on their own copy of the demo.

    A cookie tells visitors apart. A copy is made only when the cookie comes
    back, so a client that keeps no cookies never makes one.
    """

    def __init__(
        self,
        data: Path,
        secret_key: str,
        *,
        idle_seconds: float = IDLE_SECONDS,
        max_visitors: int = MAX_VISITORS,
    ) -> None:
        self.template = data / "template"
        self.folder = data / "visitors"
        self.secret_key = secret_key
        self.idle_seconds = idle_seconds
        self.max_visitors = max_visitors
        self.copies: dict[str, Copy] = {}
        self._changing = asyncio.Lock()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Answer a request with the admin of the visitor who sent it."""
        request = Request(scope)
        visitor = request.cookies.get(VISITOR_COOKIE, "")
        if not VISITOR_ID.fullmatch(visitor):
            await self.welcome(request)(scope, receive, send)
            return
        copy = await self.hold(visitor)
        if copy is None:
            full = PlainTextResponse(
                "The demo is full. Try again in a minute.",
                status_code=503,
                headers={"Retry-After": "60"},
            )
            await full(scope, receive, send)
            return
        try:
            await copy.admin(scope, receive, send)
        finally:
            copy.busy -= 1
            copy.last_used = time.monotonic()

    def welcome(self, request: Request) -> Response:
        """Give a new visitor their cookie, then send them where they were going.

        Not with a redirect: a client that keeps no cookies would be sent round
        forever. The page it gets instead is where a browser goes on from.
        """
        if request.method in ("GET", "HEAD"):
            # raw_path is the whole path as the browser sent it, mount and all.
            path = request.scope.get("raw_path") or request.url.path.encode()
            target = path.decode("latin-1")
            if request.url.query:
                target += f"?{request.url.query}"
            response: Response = HTMLResponse(
                WELCOME.format(target=html.escape(target)),
                # Only ever without the cookie, so never kept for later.
                headers={"Cache-Control": "no-store"},
            )
        else:
            # A form cannot be sent again, so its sender starts over at home.
            home = request.scope.get("root_path", "")
            response = RedirectResponse(f"{home}/", status_code=303)
        # On the whole site, so a new cookie takes the place of a bad one
        # instead of sitting beside it.
        response.set_cookie(
            VISITOR_COOKIE,
            secrets.token_urlsafe(16),
            max_age=COOKIE_SECONDS,
            path="/",
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="lax",
        )
        return response

    async def hold(self, visitor: str) -> Copy | None:
        """The visitor's copy, made now if they have none, marked busy.

        None when every copy is busy and there is no room for another.
        """
        copy = self.copies.get(visitor)
        if copy is None:
            async with self._changing:
                copy = self.copies.get(visitor)
                if copy is None:
                    full = len(self.copies) >= self.max_visitors
                    if full and not await self._throw_away_oldest():
                        return None
                    copy = await self._make(visitor)
                    self.copies[visitor] = copy
        copy.busy += 1
        copy.last_used = time.monotonic()
        return copy

    async def sweep(self) -> None:
        """Throw away every copy nobody has used for `idle_seconds`."""
        async with self._changing:
            now = time.monotonic()
            idle = [
                visitor
                for visitor, copy in self.copies.items()
                if not copy.busy and now - copy.last_used >= self.idle_seconds
            ]
            for visitor in idle:
                await self._throw_away(visitor)
        if idle:
            logger.info(
                "Threw away %d idle copies of the demo, %d left.",
                len(idle),
                len(self.copies),
            )

    async def close(self) -> None:
        """Let go of every copy's database, for when the demo stops."""
        for copy in self.copies.values():
            await copy.engine.dispose()
        self.copies.clear()

    async def _make(self, visitor: str) -> Copy:
        folder = self.folder / visitor
        # A copy thrown away earlier may have left its folder behind.
        await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
        await asyncio.to_thread(shutil.copytree, self.template, folder)
        admin, engine = build_admin(folder, self.secret_key)
        return Copy(admin, engine, folder, time.monotonic())

    async def _throw_away_oldest(self) -> bool:
        idle = [
            (copy.last_used, visitor)
            for visitor, copy in self.copies.items()
            if not copy.busy
        ]
        if not idle:
            return False
        await self._throw_away(min(idle)[1])
        return True

    async def _throw_away(self, visitor: str) -> None:
        # Out of the dictionary first, so no request picks it up again.
        copy = self.copies.pop(visitor)
        await copy.engine.dispose()
        await asyncio.to_thread(shutil.rmtree, copy.folder, ignore_errors=True)


async def sweep_forever(visitors: Visitors) -> None:
    """Throw away idle copies every minute, for as long as the demo runs."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await visitors.sweep()
        except Exception:
            # The next sweep tries again, and the demo stays up meanwhile.
            logger.exception("Throwing away idle copies of the demo failed.")


def build_app(
    data: Path,
    secret_key: str,
    *,
    idle_seconds: float = IDLE_SECONDS,
    max_visitors: int = MAX_VISITORS,
) -> FastAPI:
    """The demo, keeping the template and every visitor's copy in `data`."""
    visitors = Visitors(
        data, secret_key, idle_seconds=idle_seconds, max_visitors=max_visitors
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Copies from an earlier run belong to nobody now.
        for folder in (visitors.template, visitors.folder):
            await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
        await build_template(visitors.template)
        sweeping = asyncio.create_task(sweep_forever(visitors))
        yield
        sweeping.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeping
        await visitors.close()

    app = FastAPI(
        title="adminsite demo",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/admin", visitors)
    app.state.visitors = visitors

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
